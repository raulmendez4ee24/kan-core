import argparse
import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, init_db
from learning.reinforcement_optimizer import calibrate_org_learning_policy, refresh_learning_from_sources
from models import Client

logger = logging.getLogger("kan_core.workers.reinforcement_optimizer")


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


def _env_int(name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = int(default)
    return max(min_value, min(value, max_value))


def _parse_uuid_list(raw: str) -> List[UUID]:
    out: List[UUID] = []
    for token in (raw or "").split(","):
        item = token.strip()
        if not item:
            continue
        try:
            parsed = UUID(item)
        except ValueError:
            continue
        if parsed not in out:
            out.append(parsed)
    return out


@dataclass(frozen=True)
class OptimizerConfig:
    enabled: bool
    interval_seconds: int
    max_orgs_per_tick: int
    target_org_ids: List[UUID]

    @staticmethod
    def from_env() -> "OptimizerConfig":
        return OptimizerConfig(
            enabled=_env_bool("ENABLE_REINFORCEMENT_OPTIMIZER", "false"),
            interval_seconds=_env_int(
                "REINFORCEMENT_OPTIMIZER_INTERVAL_SECONDS", 86400, min_value=60, max_value=86400 * 7
            ),
            max_orgs_per_tick=_env_int("REINFORCEMENT_OPTIMIZER_MAX_ORGS_PER_TICK", 200, min_value=1, max_value=2000),
            target_org_ids=_parse_uuid_list(os.getenv("REINFORCEMENT_OPTIMIZER_TARGET_ORG_IDS", "")),
        )


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def _ensure_db_ready() -> None:
    if _env_bool("AUTO_CREATE_DB", "false"):
        await init_db()
        return
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))


async def _list_target_orgs(session: AsyncSession, config: OptimizerConfig) -> List[UUID]:
    if config.target_org_ids:
        stmt = select(Client.id).where(Client.id.in_(config.target_org_ids), Client.is_active.is_(True))
        rows = list((await session.execute(stmt)).scalars().all())
        return [UUID(str(x)) for x in rows]
    stmt = (
        select(Client.id)
        .where(Client.is_active.is_(True))
        .order_by(desc(Client.updated_at))
        .limit(max(1, config.max_orgs_per_tick))
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return [UUID(str(x)) for x in rows]


async def _tick(config: OptimizerConfig) -> None:
    async with AsyncSessionLocal() as session:
        org_ids = await _list_target_orgs(session, config)

    logger.info("reinforcement_optimizer_tick orgs=%s enabled=%s", len(org_ids), config.enabled)

    async with AsyncSessionLocal() as session:
        try:
            refreshed = await refresh_learning_from_sources(session)
            logger.info("learning_refresh ok patterns=%s", refreshed.get("integration_memory_patterns"))
        except Exception:
            logger.exception("learning_refresh_failed")

    for org_id in org_ids:
        async with AsyncSessionLocal() as session:
            try:
                calibrated = await calibrate_org_learning_policy(session, organization_id=org_id)
                logger.info(
                    "learning_calibrated org=%s updated=%s epsilon=%s",
                    org_id,
                    calibrated.get("updated"),
                    (calibrated.get("learning") or {}).get("exploration_epsilon") if isinstance(calibrated, dict) else None,
                )
            except Exception:
                logger.exception("learning_calibrate_failed org=%s", org_id)


async def _run_forever(config: OptimizerConfig) -> None:
    if not config.enabled:
        logger.warning("ENABLE_REINFORCEMENT_OPTIMIZER disabled; exiting.")
        return
    await _ensure_db_ready()
    while True:
        started = datetime.now(timezone.utc)
        try:
            await _tick(config)
        except Exception:
            logger.exception("reinforcement_optimizer_tick_failed")
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        sleep_seconds = max(5, int(config.interval_seconds - elapsed))
        await asyncio.sleep(sleep_seconds)


async def _run_once(config: OptimizerConfig) -> int:
    if not config.enabled:
        logger.warning("ENABLE_REINFORCEMENT_OPTIMIZER disabled; exiting.")
        return 0
    try:
        await _ensure_db_ready()
        await _tick(config)
    except Exception:
        logger.exception("reinforcement_optimizer_once_failed")
        return 1
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Learning reinforcement optimizer worker")
    parser.add_argument("--once", action="store_true", help="Run a single tick then exit")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    _configure_logging(str(args.log_level))
    config = OptimizerConfig.from_env()
    if args.once:
        code = asyncio.run(_run_once(config))
        raise SystemExit(code)
    asyncio.run(_run_forever(config))


if __name__ == "__main__":
    main()

