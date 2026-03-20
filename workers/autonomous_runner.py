import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.alert_engine import evaluate_alert_rules
from brain.ai_coo_engine import run_ai_coo_cycle
from brain.business_context_engine import run_business_context_cycle
from brain.business_monitor import build_metrics_snapshot
from brain.crm_engine import dispatch_due_followups
from config.env_helpers import env_bool, env_float, env_int
from database import AsyncSessionLocal
from models import BusinessEvent, Client, ConversationLog, IntegrationRunLog, Objective, ObjectiveRun
from routes.autonomy import implement_recommendation, run_autonomy_cycles
from schemas.autonomy import CycleRunRequest, RecommendationImplementRequest

logger = logging.getLogger("kan_core.workers.autonomous_runner")


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


def _sanitize_database_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except Exception:
        return value
    if not parts.password:
        return value
    netloc = parts.netloc
    if "@" in netloc:
        creds, host = netloc.split("@", 1)
        if ":" in creds:
            user = creds.split(":", 1)[0]
            netloc = f"{user}:***@{host}"
        else:
            netloc = f"{creds}:***@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _iter_exception_chain(exc: BaseException) -> List[BaseException]:
    chain: List[BaseException] = []
    seen = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def _looks_like_db_connectivity_error(exc: BaseException) -> bool:
    needles = [
        "connect call failed",
        "connection refused",
        "could not connect",
        "name or service not known",
        "temporary failure in name resolution",
        "timeout",
        "connection timed out",
        "server closed the connection",
    ]
    for item in _iter_exception_chain(exc):
        msg = str(item).lower()
        if any(token in msg for token in needles):
            return True
    return False


def _db_error_help(exc: BaseException) -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    hint_url = _sanitize_database_url(database_url) if database_url else ""
    lines = [
        "Database connection failed for autonomous_runner.",
    ]
    if hint_url:
        lines.append(f"DATABASE_URL={hint_url}")
    else:
        lines.append("DATABASE_URL is not set (using default from database.py).")
    lines.append(f"Error: {exc!s}")
    lines.append("")
    lines.append("Fix:")
    lines.append("1) Start Postgres on localhost:5432, or set DATABASE_URL to your Postgres (e.g. Supabase).")
    lines.append("2) If tables are missing, run with AUTO_CREATE_DB=true once (or run alembic migrations).")
    return "\n".join(lines)


@dataclass(frozen=True)
class RunnerConfig:
    enabled: bool
    tick_seconds: int
    max_orgs_per_tick: int
    require_activity: bool
    include_active_clients: bool
    min_objective_interval_seconds: int
    bypass_min_interval_on_errors: bool
    window_days: int
    max_recommendations: int
    include_llm_summary: bool
    implement_autonomous_objectives: bool
    max_implement_per_objective: int
    min_confidence_to_implement: float
    only_high_priority: bool
    force_implement: bool
    confirm_sensitive: bool
    target_org_ids: List[UUID]
    enable_business_context_reasoning: bool
    business_context_window_hours: int
    business_context_min_prev_sales: int
    business_context_sales_drop_threshold: float
    business_context_create_recommendations: bool
    business_context_recommendation_cooldown_hours: int
    business_context_execute_campaigns: bool
    business_context_followups_max_jobs: int
    business_context_followups_dry_run: bool

    @staticmethod
    def from_env() -> "RunnerConfig":
        return RunnerConfig(
            enabled=env_bool("ENABLE_AUTONOMOUS_RUNNER", "false"),
            tick_seconds=env_int("AUTONOMOUS_RUNNER_TICK_SECONDS", 60, min_value=5, max_value=3600),
            max_orgs_per_tick=env_int("AUTONOMOUS_RUNNER_MAX_ORGS_PER_TICK", 50, min_value=1, max_value=500),
            require_activity=env_bool("AUTONOMOUS_RUNNER_REQUIRE_ACTIVITY", "true"),
            include_active_clients=env_bool("AUTONOMOUS_RUNNER_INCLUDE_ACTIVE_CLIENTS", "false"),
            min_objective_interval_seconds=env_int(
                "AUTONOMOUS_RUNNER_MIN_OBJECTIVE_INTERVAL_SECONDS", 300, min_value=30, max_value=86400
            ),
            bypass_min_interval_on_errors=env_bool("AUTONOMOUS_RUNNER_BYPASS_MIN_INTERVAL_ON_ERRORS", "true"),
            window_days=env_int("AUTONOMOUS_RUNNER_WINDOW_DAYS", 30, min_value=1, max_value=180),
            max_recommendations=env_int("AUTONOMOUS_RUNNER_MAX_RECOMMENDATIONS", 5, min_value=1, max_value=20),
            include_llm_summary=env_bool("AUTONOMOUS_RUNNER_INCLUDE_LLM_SUMMARY", "true"),
            implement_autonomous_objectives=env_bool("AUTONOMOUS_RUNNER_IMPLEMENT_AUTONOMOUS_OBJECTIVES", "true"),
            max_implement_per_objective=env_int(
                "AUTONOMOUS_RUNNER_MAX_IMPLEMENT_PER_OBJECTIVE", 2, min_value=0, max_value=10
            ),
            min_confidence_to_implement=env_float(
                "AUTONOMOUS_RUNNER_MIN_CONFIDENCE_TO_IMPLEMENT", 0.8, min_value=0.0, max_value=1.0
            ),
            only_high_priority=env_bool("AUTONOMOUS_RUNNER_ONLY_HIGH_PRIORITY", "true"),
            force_implement=env_bool("AUTONOMOUS_RUNNER_FORCE_IMPLEMENT", "false"),
            confirm_sensitive=env_bool("AUTONOMOUS_RUNNER_CONFIRM_SENSITIVE", "true"),
            target_org_ids=_parse_uuid_list(os.getenv("AUTONOMOUS_RUNNER_TARGET_ORG_IDS", "")),
            enable_business_context_reasoning=env_bool("ENABLE_BUSINESS_CONTEXT_REASONING", "false"),
            business_context_window_hours=env_int(
                "BUSINESS_CONTEXT_WINDOW_HOURS", 24, min_value=1, max_value=24 * 14
            ),
            business_context_min_prev_sales=env_int("BUSINESS_CONTEXT_MIN_PREV_SALES", 3, min_value=0, max_value=9999),
            business_context_sales_drop_threshold=env_float(
                "BUSINESS_CONTEXT_SALES_DROP_THRESHOLD", 0.3, min_value=0.05, max_value=0.95
            ),
            business_context_create_recommendations=env_bool("BUSINESS_CONTEXT_CREATE_RECOMMENDATIONS", "true"),
            business_context_recommendation_cooldown_hours=env_int(
                "BUSINESS_CONTEXT_RECOMMENDATION_COOLDOWN_HOURS", 24, min_value=1, max_value=24 * 14
            ),
            business_context_execute_campaigns=env_bool("BUSINESS_CONTEXT_EXECUTE_CAMPAIGNS", "false"),
            business_context_followups_max_jobs=env_int(
                "BUSINESS_CONTEXT_FOLLOWUPS_MAX_JOBS", 30, min_value=1, max_value=200
            ),
            business_context_followups_dry_run=env_bool("BUSINESS_CONTEXT_FOLLOWUPS_DRY_RUN", "true"),
        )


class AutonomousRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self._config = config
        self._consecutive_failures = 0

    async def _list_target_orgs(self, session: AsyncSession) -> List[UUID]:
        if self._config.target_org_ids:
            stmt = select(Client.id).where(
                Client.id.in_(self._config.target_org_ids),
                Client.is_active.is_(True),
            )
            rows = list((await session.execute(stmt)).scalars().all())
            return [UUID(str(x)) for x in rows]

        if self._config.include_active_clients:
            stmt = (
                select(Client.id)
                .where(Client.is_active.is_(True))
                .order_by(desc(Client.updated_at))
                .limit(max(1, self._config.max_orgs_per_tick))
            )
            rows = list((await session.execute(stmt)).scalars().all())
            return [UUID(str(x)) for x in rows]

        stmt = (
            select(Objective.organization_id)
            .where(Objective.status == "active")
            .distinct()
            .limit(max(1, self._config.max_orgs_per_tick))
        )
        rows = list((await session.execute(stmt)).scalars().all())
        return [UUID(str(x)) for x in rows]

    async def _has_activity_since(self, session: AsyncSession, *, org_id: UUID, since: datetime) -> bool:
        # Business events (chat/webhooks) are the primary activity signal.
        event_stmt = (
            select(BusinessEvent.id)
            .where(
                BusinessEvent.organization_id == org_id,
                BusinessEvent.created_at >= since,
            )
            .limit(1)
        )
        if (await session.execute(event_stmt)).scalars().first():
            return True

        # Integration runs matter for reliability/cost signals.
        run_stmt = (
            select(IntegrationRunLog.id)
            .where(
                IntegrationRunLog.client_id == org_id,
                IntegrationRunLog.created_at >= since,
            )
            .limit(1)
        )
        if (await session.execute(run_stmt)).scalars().first():
            return True

        # As a fallback, check raw conversation logs (may exist without events).
        convo_stmt = (
            select(ConversationLog.id)
            .where(
                ConversationLog.client_id == org_id,
                ConversationLog.timestamp >= since,
            )
            .limit(1)
        )
        return bool((await session.execute(convo_stmt)).scalars().first())

    async def _has_recent_errors(self, session: AsyncSession, *, org_id: UUID, since: datetime) -> bool:
        stmt = (
            select(BusinessEvent.id)
            .where(
                BusinessEvent.organization_id == org_id,
                BusinessEvent.created_at >= since,
                BusinessEvent.event_type.in_(
                    {"error", "workflow_failed", "api_failure", "provider_failure"}
                ),
            )
            .limit(1)
        )
        return bool((await session.execute(stmt)).scalars().first())

    async def _latest_objective_run_at(self, session: AsyncSession, *, objective_id: UUID) -> Optional[datetime]:
        stmt = (
            select(ObjectiveRun.finished_at, ObjectiveRun.created_at)
            .where(ObjectiveRun.objective_id == objective_id)
            .order_by(desc(ObjectiveRun.created_at))
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
        if not row:
            return None
        finished_at, created_at = row
        return finished_at or created_at

    async def _pick_objectives_to_run(
        self, session: AsyncSession, *, org_id: UUID, objectives: Sequence[Objective]
    ) -> List[UUID]:
        now = datetime.now(timezone.utc)
        selected: List[UUID] = []

        for objective in objectives:
            last_run_at = await self._latest_objective_run_at(session, objective_id=objective.id)
            if last_run_at is None:
                selected.append(objective.id)
                continue

            age_seconds = (now - last_run_at).total_seconds()
            if age_seconds < float(self._config.min_objective_interval_seconds):
                if self._config.bypass_min_interval_on_errors:
                    if await self._has_recent_errors(session, org_id=org_id, since=last_run_at):
                        selected.append(objective.id)
                continue

            if self._config.require_activity:
                if not await self._has_activity_since(session, org_id=org_id, since=last_run_at):
                    continue

            selected.append(objective.id)

        return selected

    def _should_auto_implement(self, objective: Objective) -> bool:
        if not self._config.implement_autonomous_objectives:
            return False
        return str(objective.autonomy_mode or "").strip().lower() == "autonomous"

    async def _process_org(self, org_id: UUID) -> None:
        async with AsyncSessionLocal() as session:
            objectives_stmt = select(Objective).where(
                Objective.organization_id == org_id,
                Objective.status == "active",
            )
            objectives = list((await session.execute(objectives_stmt)).scalars().all())
            run_business_context = bool(self._config.enable_business_context_reasoning)
            if not objectives and not run_business_context:
                logger.debug("org_no_work org=%s", org_id)
                return

            if run_business_context and self._config.require_activity and not objectives:
                now = datetime.now(timezone.utc)
                lookback_hours = max(int(self._config.business_context_window_hours) * 2, 24)
                since = now - timedelta(hours=lookback_hours)
                if not await self._has_activity_since(session, org_id=org_id, since=since):
                    logger.debug(
                        "org_skip_no_activity org=%s lookback_hours=%s",
                        org_id,
                        lookback_hours,
                    )
                    return

            # Observe: store metrics snapshots continuously.
            snapshot = await build_metrics_snapshot(session, organization_id=org_id, window_minutes=5)
            metrics = snapshot.metrics or {}

            # Detect + notify: evaluate alerts if enabled.
            if env_bool("ENABLE_ADVANCED_ALERTS", "false"):
                try:
                    await evaluate_alert_rules(session, organization_id=org_id, metrics=metrics)
                except Exception:
                    logger.exception("alerts_evaluate_failed org=%s", org_id)

            if run_business_context:
                try:
                    ctx_result = await run_business_context_cycle(
                        session,
                        organization_id=org_id,
                        window_hours=int(self._config.business_context_window_hours),
                        min_prev_sales=int(self._config.business_context_min_prev_sales),
                        sales_drop_threshold=float(self._config.business_context_sales_drop_threshold),
                        create_recommendations=bool(self._config.business_context_create_recommendations),
                        recommendation_cooldown_hours=int(self._config.business_context_recommendation_cooldown_hours),
                        execute_campaigns=bool(self._config.business_context_execute_campaigns),
                        followups_max_jobs=int(self._config.business_context_followups_max_jobs),
                        followups_dry_run=bool(self._config.business_context_followups_dry_run),
                    )
                    analysis = ctx_result.get("analysis") if isinstance(ctx_result, dict) else {}
                    insights = analysis.get("insights") if isinstance(analysis, dict) else []
                    created = ctx_result.get("created_recommendations") if isinstance(ctx_result, dict) else []
                    campaign = ctx_result.get("campaign") if isinstance(ctx_result, dict) else {}
                    logger.info(
                        "business_context_cycle org=%s insights=%s created_recommendations=%s campaign_executed=%s",
                        org_id,
                        len(insights) if isinstance(insights, list) else 0,
                        len(created) if isinstance(created, list) else 0,
                        bool((campaign or {}).get("executed", False))
                        if isinstance(campaign, dict)
                        else False,
                    )
                except Exception:
                    logger.exception("business_context_cycle_failed org=%s", org_id)

            if env_bool("ENABLE_AI_COO", "false"):
                try:
                    ai_res = await run_ai_coo_cycle(
                        session,
                        organization_id=org_id,
                        window_hours=env_int("AI_COO_WINDOW_HOURS", 24, min_value=1, max_value=24 * 14),
                        dry_run=env_bool("AI_COO_DRY_RUN", "true"),
                        max_actions=env_int("AI_COO_MAX_ACTIONS", 20, min_value=1, max_value=200),
                        execution_mode_override=os.getenv("AI_COO_FORCE_EXECUTION_MODE"),
                    )
                    plan_items = ai_res.get("action_plan") if isinstance(ai_res, dict) else []
                    logger.info(
                        "ai_coo_cycle org=%s executed=%s plan=%s",
                        org_id,
                        bool(ai_res.get("executed", False)) if isinstance(ai_res, dict) else False,
                        len(plan_items) if isinstance(plan_items, list) else 0,
                    )
                except Exception:
                    logger.exception("ai_coo_cycle_failed org=%s", org_id)

            if env_bool("ENABLE_FOLLOWUP_DISPATCHER", "false") and env_bool("ENABLE_CRM_INTERNAL", "false"):
                try:
                    dispatch_limit = env_int(
                        "FOLLOWUP_DISPATCHER_LIMIT", 50, min_value=1, max_value=500
                    )
                    await dispatch_due_followups(session, organization_id=org_id, limit=dispatch_limit)
                except Exception:
                    logger.exception("followup_dispatch_failed org=%s", org_id)

            if not objectives:
                return

            to_run = await self._pick_objectives_to_run(session, org_id=org_id, objectives=objectives)
            if not to_run:
                logger.debug(
                    "org_no_objectives_to_run org=%s active_objectives=%s require_activity=%s",
                    org_id,
                    len(objectives),
                    self._config.require_activity,
                )
                return

            req = CycleRunRequest(
                objective_ids=[str(x) for x in to_run],
                window_days=self._config.window_days,
                max_recommendations=self._config.max_recommendations,
                include_llm_summary=self._config.include_llm_summary,
            )
            objective_by_id = {str(o.id): o for o in objectives}

            logger.info("cycle_run_start org=%s objectives=%s", org_id, len(to_run))
            response = await run_autonomy_cycles(req=req, client_id=str(org_id), session=session)

            for run_item in response.runs:
                objective = objective_by_id.get(run_item.objective_id)
                if not objective or not self._should_auto_implement(objective):
                    continue

                implemented = 0
                for rec in run_item.recommendations:
                    if implemented >= self._config.max_implement_per_objective:
                        break
                    if rec.status != "proposed":
                        continue
                    if self._config.only_high_priority and rec.priority != "high":
                        continue
                    if float(rec.confidence) < float(self._config.min_confidence_to_implement):
                        continue

                    try:
                        impl_req = RecommendationImplementRequest(
                            force=self._config.force_implement,
                            # Our GoalRecommendation defaults are conservative (requires_confirmation=True).
                            # For an explicit objective.autonomy_mode=autonomous we allow confirm_sensitive by default.
                            confirm_sensitive=bool(self._config.confirm_sensitive),
                        )
                        result = await implement_recommendation(
                            recommendation_id=rec.id,
                            req=impl_req,
                            client_id=str(org_id),
                            session=session,
                        )
                        logger.info(
                            "recommendation_implement org=%s objective=%s rec=%s status=%s",
                            org_id,
                            run_item.objective_id,
                            rec.id,
                            result.status,
                        )
                        if result.status == "implemented":
                            implemented += 1
                    except HTTPException as exc:
                        logger.warning(
                            "recommendation_implement_blocked org=%s rec=%s status=%s detail=%s",
                            org_id,
                            rec.id,
                            exc.status_code,
                            exc.detail,
                        )
                    except Exception:
                        logger.exception("recommendation_implement_failed org=%s rec=%s", org_id, rec.id)

    async def tick(self) -> None:
        async with AsyncSessionLocal() as session:
            org_ids = await self._list_target_orgs(session)

        logger.debug("runner_tick_targets targets=%s", len(org_ids))
        for org_id in org_ids[: max(1, self._config.max_orgs_per_tick)]:
            try:
                await self._process_org(org_id)
            except Exception:
                logger.exception("org_tick_failed org=%s", org_id)

    async def run_forever(self) -> None:
        if not self._config.enabled:
            logger.warning("ENABLE_AUTONOMOUS_RUNNER disabled; exiting.")
            return

        logger.info(
            "autonomous_runner_start tick_seconds=%s max_orgs_per_tick=%s require_activity=%s",
            self._config.tick_seconds,
            self._config.max_orgs_per_tick,
            self._config.require_activity,
        )
        while True:
            try:
                await self.tick()
                self._consecutive_failures = 0
            except Exception:
                self._consecutive_failures += 1
                logger.exception("runner_tick_failed failures=%s", self._consecutive_failures)

            backoff = min(self._consecutive_failures, 6)
            sleep_seconds = int(self._config.tick_seconds * (2**backoff))
            await asyncio.sleep(max(1, sleep_seconds))


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuous autonomy runner")
    parser.add_argument("--once", action="store_true", help="Run a single tick then exit")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    return parser.parse_args()


async def _ensure_db_ready() -> None:
    if env_bool("AUTO_CREATE_DB", "false"):
        from database import init_db

        await init_db()
        return
    # Minimal connectivity check; if schema is missing this will still succeed.
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))


async def _run_once(config: RunnerConfig) -> int:
    runner = AutonomousRunner(config)
    try:
        await _ensure_db_ready()
        # In --once mode we always emit a short summary so "silent success" is not confusing.
        async with AsyncSessionLocal() as session:
            org_ids = await runner._list_target_orgs(session)
        logger.info(
            "autonomous_runner_once_start enabled=%s target_orgs=%s",
            config.enabled,
            len(org_ids),
        )
        await runner.tick()
        logger.info(
            "autonomous_runner_once_done enabled=%s target_orgs=%s",
            config.enabled,
            len(org_ids),
        )
    except Exception as exc:
        if _looks_like_db_connectivity_error(exc):
            logger.error(_db_error_help(exc))
            return 2
        logger.exception("autonomous_runner_once_failed")
        return 1
    return 0


async def _run_forever(config: RunnerConfig) -> None:
    runner = AutonomousRunner(config)
    try:
        await _ensure_db_ready()
    except Exception as exc:
        if _looks_like_db_connectivity_error(exc):
            logger.error(_db_error_help(exc))
            return
        logger.exception("autonomous_runner_db_init_failed")
        return
    await runner.run_forever()


def main() -> None:
    args = _parse_args()
    _configure_logging(str(args.log_level))
    config = RunnerConfig.from_env()
    if args.once:
        code = asyncio.run(_run_once(config))
        raise SystemExit(code)
    asyncio.run(_run_forever(config))


if __name__ == "__main__":
    main()
