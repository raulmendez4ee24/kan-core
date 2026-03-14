import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select

from brain.tasks.cron import last_scheduled_time
from brain.tasks.task_engine import enqueue_task_run
from database import AsyncSessionLocal
from models import Task, TaskRun

logger = logging.getLogger("kan_core.workers.task_scheduler")


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


def _env_int(name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = int(default)
    return max(min_value, min(value, max_value))


@dataclass(frozen=True)
class SchedulerConfig:
    enabled: bool
    interval_seconds: int


def _load_config() -> SchedulerConfig:
    # Backward compatibility:
    # ENABLE_TASK_STUDIO implies scheduler should run unless explicitly disabled.
    enabled = _env_bool("ENABLE_TASK_SCHEDULER", "false")
    if not enabled:
        enabled = _env_bool("ENABLE_TASK_STUDIO", "false")
    return SchedulerConfig(
        enabled=enabled,
        interval_seconds=_env_int("TASK_SCHEDULER_INTERVAL_SECONDS", 60, min_value=10, max_value=600),
    )


def _scheduled_correlation_id(task_id: UUID, scheduled_for_utc: datetime) -> str:
    ts = scheduled_for_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"task:{task_id}:{ts}"


def _looks_like_db_connectivity_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    needles = [
        "connect call failed",
        "connection refused",
        "could not connect",
        "connection timed out",
        "timeout",
    ]
    return any(token in msg for token in needles)


def _looks_like_missing_schema_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    needles = [
        'relation "tasks" does not exist',
        "undefinedtableerror",
        "no such table: tasks",
    ]
    return any(token in msg for token in needles)


def _db_error_help() -> str:
    return (
        "Database connection failed for task_scheduler.\n"
        "Fix:\n"
        "1) Start Postgres on localhost:5432, or set DATABASE_URL to your Postgres (e.g. Supabase).\n"
        "2) If tables are missing, run with AUTO_CREATE_DB=true once (or run alembic migrations).\n"
    )


def _schema_error_help() -> str:
    return (
        "Task scheduler schema is missing (tasks table not found).\n"
        "Fix:\n"
        "1) Run `alembic upgrade head` (recommended), or\n"
        "2) Run once with AUTO_CREATE_DB=true in development.\n"
    )


async def _already_enqueued(session, *, task_id: UUID, correlation_id: str) -> bool:
    stmt = (
        select(TaskRun.id)
        .where(TaskRun.task_id == task_id, TaskRun.correlation_id == correlation_id)
        .limit(1)
    )
    return bool((await session.execute(stmt)).scalars().first())


async def _tick() -> int:
    now = datetime.now(timezone.utc)
    created = 0
    async with AsyncSessionLocal() as session:
        stmt = select(Task).where(Task.status == "active", Task.schedule_cron.is_not(None))
        tasks = list((await session.execute(stmt)).scalars().all())
        for task in tasks:
            cron = str(task.schedule_cron or "").strip()
            if not cron:
                continue
            try:
                due = last_scheduled_time(cron=cron, timezone_name=task.timezone or "UTC", now_utc=now)
            except Exception:
                continue

            corr = _scheduled_correlation_id(task.id, due.scheduled_for_utc)
            if await _already_enqueued(session, task_id=task.id, correlation_id=corr):
                continue

            try:
                _ = await enqueue_task_run(
                    session,
                    organization_id=task.organization_id,
                    task_id=task.id,
                    triggered_by="schedule",
                    environment=task.target_env,
                    actor_user_id=None,
                    correlation_id=corr,
                )
                created += 1
            except Exception as exc:
                logger.error(
                    "failed to enqueue scheduled task run",
                    extra={"context": {"task_id": str(task.id), "error": str(exc)}},
                )
                continue
    return created


async def _run_forever(config: SchedulerConfig) -> None:
    while True:
        try:
            created = await _tick()
            if created:
                logger.info("scheduled task runs enqueued", extra={"context": {"count": created}})
        except Exception as exc:
            logger.error("task scheduler tick failed", extra={"context": {"error": str(exc)}})
        await asyncio.sleep(float(config.interval_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Task scheduler (cron -> execution_jobs)")
    parser.add_argument("--once", action="store_true", help="Run one tick and exit")
    args = parser.parse_args()

    config = _load_config()
    if not config.enabled:
        logger.error("ENABLE_TASK_SCHEDULER is disabled; exiting.")
        sys.exit(1)

    try:
        if args.once:
            asyncio.run(_tick())
        else:
            asyncio.run(_run_forever(config))
    except Exception as exc:
        if _looks_like_db_connectivity_error(exc):
            logger.error("Database connection failed for task_scheduler.")
            sys.stderr.write(_db_error_help())
            sys.exit(2)
        if _looks_like_missing_schema_error(exc):
            logger.error("Task scheduler schema missing (tasks table not found).")
            sys.stderr.write(_schema_error_help())
            sys.exit(2)
        raise


if __name__ == "__main__":
    main()
