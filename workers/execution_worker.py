import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, asc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from brain.tasks.task_engine import execute_task_run
from database import AsyncSessionLocal
from models import ExecutionJob

logger = logging.getLogger("kan_core.workers.execution_worker")


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
class WorkerConfig:
    enabled: bool
    lease_seconds: int
    poll_seconds: float


def _load_config() -> WorkerConfig:
    return WorkerConfig(
        enabled=_env_bool("ENABLE_EXECUTION_QUEUE", "false"),
        lease_seconds=_env_int("EXECUTION_WORKER_LEASE_SECONDS", 60, min_value=10, max_value=600),
        poll_seconds=float(os.getenv("EXECUTION_WORKER_POLL_SECONDS", "1.0") or 1.0),
    )


async def _claim_one(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int,
) -> Optional[ExecutionJob]:
    now = datetime.now(timezone.utc)
    schedule_col = getattr(ExecutionJob, "run_after", None) or getattr(ExecutionJob, "scheduled_for", None)
    if schedule_col is None:
        raise RuntimeError("ExecutionJob requires run_after or scheduled_for column")
    lease_col = getattr(ExecutionJob, "lease_expires_at", None)

    ready_conditions = [
        and_(
            ExecutionJob.status == "queued",
            schedule_col <= now,
        ),
    ]
    if lease_col is not None:
        ready_conditions.append(
            and_(
                ExecutionJob.status == "running",
                lease_col.is_not(None),
                lease_col <= now,
            )
        )

    stmt = (
        select(ExecutionJob)
        .where(
            ExecutionJob.status.in_(["queued", "running"]),
            or_(*ready_conditions),
        )
        .order_by(asc(schedule_col), asc(ExecutionJob.created_at))
        .with_for_update(skip_locked=True)
        .limit(1)
    )

    job = (await session.execute(stmt)).scalars().first()
    if not job:
        return None

    if int(job.attempts or 0) >= int(job.max_attempts or 0):
        job.status = "failed"
        job.last_error = "max_attempts_exceeded"
        job.leased_by = None
        job.lease_expires_at = None
        await session.commit()
        return None

    job.status = "running"
    if hasattr(job, "leased_by"):
        setattr(job, "leased_by", worker_id[:120])
    if hasattr(job, "lease_expires_at"):
        setattr(job, "lease_expires_at", now + timedelta(seconds=int(lease_seconds)))
    job.attempts = int(job.attempts or 0) + 1
    await session.commit()
    await session.refresh(job)
    return job


def _backoff_seconds(attempts: int) -> int:
    # 5, 10, 20, 40, ... capped at 5 minutes.
    base = 5
    value = base * (2 ** max(0, attempts - 1))
    return int(max(5, min(value, 300)))


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
        'relation "execution_jobs" does not exist',
        "undefinedtableerror",
        "no such table: execution_jobs",
    ]
    return any(token in msg for token in needles)


def _db_error_help() -> str:
    return (
        "Database connection failed for execution_worker.\n"
        "Fix:\n"
        "1) Start Postgres on localhost:5432, or set DATABASE_URL to your Postgres (e.g. Supabase).\n"
        "2) If tables are missing, run with AUTO_CREATE_DB=true once (or run alembic migrations).\n"
    )


def _schema_error_help() -> str:
    return (
        "Execution queue schema is missing for execution_worker.\n"
        "Fix:\n"
        "1) Run `alembic upgrade head` (recommended), or\n"
        "2) Run once with AUTO_CREATE_DB=true in development.\n"
    )


async def _mark_job_done(
    session: AsyncSession,
    *,
    job: ExecutionJob,
    status: str,
    last_error: Optional[str] = None,
    retry: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    if hasattr(job, "leased_by"):
        setattr(job, "leased_by", None)
    if hasattr(job, "lease_expires_at"):
        setattr(job, "lease_expires_at", None)

    if retry and int(job.attempts or 0) < int(job.max_attempts or 0):
        job.status = "queued"
        next_at = now + timedelta(seconds=_backoff_seconds(int(job.attempts or 0)))
        if hasattr(job, "run_after"):
            setattr(job, "run_after", next_at)
        if hasattr(job, "scheduled_for"):
            setattr(job, "scheduled_for", next_at)
        job.last_error = (str(last_error) if last_error else "retry")
    else:
        job.status = status
        job.last_error = (str(last_error) if last_error else None)

    await session.commit()


async def _handle_job(session: AsyncSession, *, job: ExecutionJob, worker_id: str) -> None:
    kind = str(job.kind or "").strip().lower()
    payload = job.payload if isinstance(job.payload, dict) else {}

    if kind != "task_run":
        await _mark_job_done(session, job=job, status="failed", last_error=f"unsupported_kind:{kind}")
        return

    run_raw = payload.get("run_id")
    try:
        run_id = UUID(str(run_raw))
    except Exception:
        await _mark_job_done(session, job=job, status="failed", last_error="invalid_run_id")
        return

    run = await execute_task_run(session, run_id=run_id, worker_id=worker_id)

    # Job mirrors the run outcome. Paused runs are treated as completed jobs (resume enqueues a new job).
    if run.status == "completed":
        await _mark_job_done(session, job=job, status="completed")
        return
    if run.status == "paused":
        await _mark_job_done(session, job=job, status="completed", last_error="paused_requires_approval")
        return
    if run.status == "cancelled":
        await _mark_job_done(session, job=job, status="cancelled")
        return
    if run.status == "failed":
        await _mark_job_done(session, job=job, status="failed", last_error=run.error or "run_failed")
        return

    # If still running/queued, keep it queued for another attempt.
    await _mark_job_done(session, job=job, status="queued", last_error="incomplete", retry=True)


async def _tick(config: WorkerConfig, *, worker_id: str) -> bool:
    async with AsyncSessionLocal() as session:
        job = await _claim_one(session, worker_id=worker_id, lease_seconds=config.lease_seconds)
        if not job:
            return False
        try:
            await _handle_job(session, job=job, worker_id=worker_id)
        except Exception as exc:
            logger.error("job failed", extra={"context": {"job_id": str(job.id), "error": str(exc)}})
            await _mark_job_done(session, job=job, status="queued", last_error=str(exc), retry=True)
        return True


async def _run_forever(config: WorkerConfig, *, worker_id: str) -> None:
    while True:
        did_work = await _tick(config, worker_id=worker_id)
        await asyncio.sleep(0.0 if did_work else max(0.2, float(config.poll_seconds)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Execution job worker (Postgres queue)")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    parser.add_argument("--worker-id", default=os.getenv("EXECUTION_WORKER_ID") or "exec-worker-1")
    args = parser.parse_args()

    config = _load_config()
    if not config.enabled:
        logger.error("ENABLE_EXECUTION_QUEUE is disabled; exiting.")
        sys.exit(1)

    try:
        if args.once:
            asyncio.run(_tick(config, worker_id=str(args.worker_id)))
        else:
            asyncio.run(_run_forever(config, worker_id=str(args.worker_id)))
    except Exception as exc:
        if _looks_like_db_connectivity_error(exc):
            logger.error("Database connection failed for execution_worker.")
            sys.stderr.write(_db_error_help())
            sys.exit(2)
        if _looks_like_missing_schema_error(exc):
            logger.error("Execution worker schema missing (execution_jobs table not found).")
            sys.stderr.write(_schema_error_help())
            sys.exit(2)
        raise


if __name__ == "__main__":
    main()
