"""ARQ connection and stable run-job submission helpers."""

from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings


def redis_settings_from_url(redis_url: str | None) -> RedisSettings:
    """Build explicit Redis settings without including credentials in errors."""
    if not redis_url:
        raise ValueError("DOCDRIFT_REDIS_URL must be configured.")
    try:
        settings = RedisSettings.from_dsn(redis_url)
    except (TypeError, ValueError, RuntimeError) as error:
        raise ValueError("DOCDRIFT_REDIS_URL must be a valid redis:// or rediss:// URL.") from error
    settings.conn_timeout = 1
    settings.conn_retries = 1
    settings.conn_retry_delay = 1
    return settings


async def create_queue_pool(redis_url: str) -> ArqRedis:
    """Connect to Redis using the configured DSN and bounded connection retry."""
    return await create_pool(redis_settings_from_url(redis_url))


async def close_queue_pool(pool: ArqRedis) -> None:
    """Close an API-owned ARQ Redis pool."""
    await pool.aclose()


def run_job_id(run_id: int) -> str:
    """Return the stable ARQ job identifier for a persisted run."""
    if run_id < 1:
        raise ValueError("run_id must be a positive integer.")
    return f"docdrift:run:{run_id}"


async def enqueue_run(pool: ArqRedis, run_id: int) -> bool:
    """Enqueue a run, returning false when ARQ already has its stable job ID."""
    job = await pool.enqueue_job("process_run", run_id, _job_id=run_job_id(run_id))
    return job is not None
