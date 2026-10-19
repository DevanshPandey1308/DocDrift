"""Minimal ARQ worker lifecycle for persisted DocDrift runs."""

from __future__ import annotations

import logging
from typing import Any

from arq.worker import Retry, Worker
from docdrift_api.config import Settings
from docdrift_api.database import create_session_factory, dispose_engine
from docdrift_api.models import Run, RunStatus
from docdrift_api.queue import redis_settings_from_url
from docdrift_core.orchestration import GroundedVerificationClient
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .checkout import RepositoryCheckoutError
from .fix_integration import FixCandidateClient, VerifiedFixSuggestion
from .github import GitHubPublicationError
from .llm_provider import AnthropicDocDriftProvider, RunLLMControls
from .pipeline import run_analysis_pipeline
from .publication import publish_completed_run

logger = logging.getLogger(__name__)

MAX_JOB_TRIES = 3
RETRY_DELAYS_SECONDS = (1, 4)
JOB_TIMEOUT_SECONDS = 30


class RunProcessingError(RuntimeError):
    """Sanitized terminal worker exception that does not expose connection details."""


def create_worker(
    settings: Settings | None = None,
    *,
    fix_candidate_client: FixCandidateClient | None = None,
    verification_client: GroundedVerificationClient | None = None,
) -> Worker:
    """Build a worker with explicit Redis settings and DB lifecycle hooks."""
    worker_settings = settings or Settings.from_env()
    redis_settings = redis_settings_from_url(worker_settings.redis_url)
    if not worker_settings.database_url:
        raise ValueError("DOCDRIFT_DATABASE_URL must be configured for the worker.")

    llm_provider: AnthropicDocDriftProvider | None = None
    if worker_settings.llm_enabled and worker_settings.anthropic_api_key and worker_settings.anthropic_verification_model and worker_settings.anthropic_fix_model:
        try:
            llm_provider = AnthropicDocDriftProvider.from_api_key(
                worker_settings.anthropic_api_key,
                verification_model=worker_settings.anthropic_verification_model,
                fix_model=worker_settings.anthropic_fix_model,
                timeout_seconds=worker_settings.llm_timeout_seconds,
            )
        except ImportError:
            logger.warning("llm_provider_unavailable", extra={"reason": "optional_sdk_missing"})
    elif worker_settings.llm_enabled:
        logger.warning("llm_provider_unavailable", extra={"reason": "incomplete_configuration"})

    return Worker(
        functions=[process_run],
        redis_settings=redis_settings,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        ctx={
            "database_url": worker_settings.database_url,
            "settings": worker_settings,
            "fix_candidate_client": fix_candidate_client,
            "verification_client": verification_client,
            "llm_provider": llm_provider,
            "llm_token_budget": worker_settings.llm_run_token_budget,
            "llm_timeout_seconds": worker_settings.llm_timeout_seconds,
            "llm_controls_by_run": {},
        },
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
    provider = ctx.get("llm_provider")
    if isinstance(provider, AnthropicDocDriftProvider):
        await provider.close()
    logger.info("worker_stopped")


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
    """Run deterministic PR analysis and persist findings for one queued run."""
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        raise ValueError("run_id must be a positive integer.")
    session_factory = ctx.get("session_factory")
    if session_factory is None:
        raise RuntimeError("Worker database session factory is not initialized.")

    try:
        verified_fix_suggestions: list[VerifiedFixSuggestion] = []
        provider = ctx.get("llm_provider")
        controls_by_run = ctx.setdefault("llm_controls_by_run", {})
        llm_controls = None
        if isinstance(provider, AnthropicDocDriftProvider) and isinstance(controls_by_run, dict):
            existing = controls_by_run.get(run_id)
            if isinstance(existing, RunLLMControls):
                llm_controls = existing
            else:
                llm_controls = RunLLMControls(
                    provider,
                    token_budget=ctx.get("llm_token_budget", 20_000),
                    timeout_seconds=ctx.get("llm_timeout_seconds", 20.0),
                )
                controls_by_run[run_id] = llm_controls
        effective_fix_client = ctx.get("fix_candidate_client") or llm_controls
        analysis_options: dict[str, Any] = {
            "checkout_component": ctx.get("checkout_component")
        }
        candidate_client = effective_fix_client
        if llm_controls is not None:
            analysis_options["llm_controls"] = llm_controls
        if candidate_client is not None:
            analysis_options.update(
                fix_candidate_client=candidate_client,
                verification_client=ctx.get("verification_client"),
                verified_fix_sink=verified_fix_suggestions,
            )
        state = await run_analysis_pipeline(
            session_factory,
            run_id,
            **analysis_options,
        )
    except (SQLAlchemyError, OSError, TimeoutError, RepositoryCheckoutError) as error:
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

    publication = "not_attempted"
    if state == RunStatus.COMPLETED.value:
        try:
            publication = await publish_completed_run(
                session_factory,
                run_id,
                settings=ctx.get("settings"),
                publisher=ctx.get("publisher_component"),
                verified_fix_suggestions=tuple(verified_fix_suggestions),
            )
        except GitHubPublicationError as error:
            attempt = _attempt_number(ctx)
            if error.transient and attempt < MAX_JOB_TRIES:
                delay = (
                    error.retry_after_seconds
                    or RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)]
                )
                logger.warning(
                    "github_check_run_retry",
                    extra={
                        "run_id": run_id,
                        "attempt": attempt,
                        "error_type": type(error).__name__,
                    },
                )
                raise Retry(defer=delay) from None
            logger.error(
                "github_check_run_failed",
                extra={"run_id": run_id, "error_type": type(error).__name__},
            )
            publication = "failed"
        except SQLAlchemyError as error:
            attempt = _attempt_number(ctx)
            if attempt < MAX_JOB_TRIES:
                delay = RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)]
                logger.warning(
                    "github_publication_state_retry",
                    extra={"run_id": run_id, "attempt": attempt, "error_type": type(error).__name__},
                )
                raise Retry(defer=delay) from None
            logger.error(
                "github_publication_state_failed",
                extra={"run_id": run_id, "error_type": type(error).__name__},
            )
            publication = "failed"
        except (ValueError, TypeError) as error:
            logger.error(
                "github_check_run_failed",
                extra={"run_id": run_id, "error_type": type(error).__name__},
            )
            publication = "failed"

    logger.info(
        "run_processing_finished",
        extra={"run_id": run_id, "state": state, "publication_status": publication},
    )
    controls_by_run = ctx.get("llm_controls_by_run")
    if isinstance(controls_by_run, dict):
        controls_by_run.pop(run_id, None)
    return {"run_id": run_id, "state": state, "publication": publication}


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
