"""Minimal ARQ worker lifecycle for persisted DocDrift runs."""

from __future__ import annotations

import logging
from typing import Any

from arq.worker import Retry, Worker
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from docdrift_api.config import Settings
from docdrift_api.database import create_session_factory, dispose_engine
from docdrift_api.models import Run, RunStatus
from docdrift_api.queue import redis_settings_from_url

logger = logging.getLogger(__name__)

MAX_JOB_TRIES = 3
RETRY_DELAYS_SECONDS = (1, 4)
JOB_TIMEOUT_SECONDS = 30


class RunProcessingError(RuntimeError):
    """Sanitized terminal worker exception that does not expose connection details."""


def create_worker(settings: Settings | None = None) -> Worker:
    """Build a worker with explicit Redis settings and DB lifecycle hooks."""
    worker_settings = settings or Settings.from_env()
    redis_settings = redis_settings_from_url(worker_settings.redis_url)
    if not worker_settings.database_url:
        raise ValueError("DOCDRIFT_DATABASE_URL must be configured for the worker.")

    return Worker(
        functions=[process_run],
        redis_settings=redis_settings,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        ctx={"database_url": worker_settings.database_url},
        max_tries=MAX_JOB_TRIES,
        job_timeout=JOB_TIMEOUT_SECONDS,
        keep_result=3600,
        max_jobs=4,
    )


async def on_startup(ctx: dict[str, Any]) -> None:
    """Create the worker's async database engine and session factory."""
    database_url = ctx.get("database_url")
    if not isinstance(database_url, str) or not database_url:
        raise ValueError("DOCDRIFT_DATABASE_URL must be configured for the worker.")
    session_factory, engine = create_session_factory(database_url)
    ctx["session_factory"] = session_factory
    ctx["database_engine"] = engine
    logger.info("worker_started")


async def on_shutdown(ctx: dict[str, Any]) -> None:
    """Dispose the worker-owned database engine when ARQ shuts down."""
    engine = ctx.pop("database_engine", None)
    ctx.pop("session_factory", None)
    if engine is not None:
        await dispose_engine(engine)
    logger.info("worker_stopped")


async def _process_run_transaction(
    session_factory: async_sessionmaker[AsyncSession], run_id: int
) -> str:
    """Validate a pending run and persist the explicit no-analysis state."""
    async with session_factory() as session, session.begin():
        run = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None:
            return "missing"
        if run.status != RunStatus.PENDING:
            return run.status.value
        if run.repository_id < 1 or run.pull_request_number < 1 or not run.head_sha:
            raise ValueError("Persisted run is missing required identity fields.")

        run.status = RunStatus.RUNNING
        await session.flush()

        # Analysis is intentionally out of scope. This state must not imply success.
        run.status = RunStatus.AWAITING_ANALYSIS
        await session.flush()
        return RunStatus.AWAITING_ANALYSIS.value


async def _mark_failed_if_active(
    session_factory: async_sessionmaker[AsyncSession], run_id: int
) -> None:
    """Mark an active run failed without overwriting a later/terminal state."""
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Run)
            .where(
                Run.id == run_id,
                Run.status.in_((RunStatus.PENDING, RunStatus.RUNNING)),
            )
            .values(status=RunStatus.FAILED)
        )


def _attempt_number(ctx: dict[str, Any]) -> int:
    value = ctx.get("job_try", 1)
    return value if isinstance(value, int) and value > 0 else 1


async def process_run(ctx: dict[str, Any], run_id: int) -> dict[str, str | int]:
    """Idempotently transition one persisted run to awaiting analysis."""
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        raise ValueError("run_id must be a positive integer.")
    session_factory = ctx.get("session_factory")
    if session_factory is None:
        raise RuntimeError("Worker database session factory is not initialized.")

    try:
        state = await _process_run_transaction(session_factory, run_id)
    except (SQLAlchemyError, OSError, TimeoutError) as error:
        attempt = _attempt_number(ctx)
        if attempt < MAX_JOB_TRIES:
            delay = RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)]
            logger.warning(
                "run_processing_retry",
                extra={"run_id": run_id, "attempt": attempt, "error_type": type(error).__name__},
            )
            raise Retry(defer=delay) from None
        await _fail_after_error(session_factory, run_id, error)
        raise RunProcessingError("Run processing failed after bounded retries.") from None
    except Exception as error:
        await _fail_after_error(session_factory, run_id, error)
        raise RunProcessingError("Run processing failed.") from None

    logger.info("run_processing_finished", extra={"run_id": run_id, "state": state})
    return {"run_id": run_id, "state": state}


async def _fail_after_error(
    session_factory: async_sessionmaker[AsyncSession], run_id: int, error: Exception
) -> None:
    try:
        await _mark_failed_if_active(session_factory, run_id)
    except Exception as persistence_error:
        logger.error(
            "run_failure_state_persistence_failed",
            extra={"run_id": run_id, "error_type": type(persistence_error).__name__},
        )
    logger.error(
        "run_processing_failed",
        extra={"run_id": run_id, "error_type": type(error).__name__},
    )


def main() -> None:
    """Run ARQ using environment-backed Redis and PostgreSQL configuration."""
    create_worker().run()


if __name__ == "__main__":
    main()
