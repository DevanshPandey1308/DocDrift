from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, Self
from unittest.mock import AsyncMock, patch

from arq.connections import RedisSettings
from arq.worker import Retry
from docdrift_api.config import Settings
from docdrift_api.models import RunStatus
from docdrift_worker import worker


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    def __init__(self, run: SimpleNamespace | None) -> None:
        self.run = run
        self.flush_count = 0
        self.fail_flush_at: int | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> FakeTransaction:
        return FakeTransaction()

    async def scalar(self, _: object) -> SimpleNamespace | None:
        return self.run

    async def flush(self) -> None:
        self.flush_count += 1
        if self.flush_count == self.fail_flush_at:
            raise OSError("temporary database outage")

    async def execute(self, _: object) -> None:
        return None


class FakeSessionFactory:
    def __init__(self, run: SimpleNamespace | None) -> None:
        self.session = FakeSession(run)

    def __call__(self) -> FakeSession:
        return self.session


def make_run(status: RunStatus = RunStatus.PENDING) -> SimpleNamespace:
    return SimpleNamespace(
        id=5,
        repository_id=3,
        repository=SimpleNamespace(full_name="owner/project"),
        pull_request_number=10,
        head_sha="a" * 40,
        status=status,
    )


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def test_worker_requires_explicit_redis_and_database_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "DOCDRIFT_REDIS_URL"):
            worker.create_worker(Settings(database_url="postgresql://db/docdrift"))
        with self.assertRaisesRegex(ValueError, "DOCDRIFT_DATABASE_URL"):
            worker.create_worker(Settings(redis_url="redis://redis:6379/0"))

    def test_worker_uses_explicit_redis_settings_and_bounded_defaults(self) -> None:
        instance = worker.create_worker(
            Settings(
                database_url="postgresql://db/docdrift",
                redis_url="redis://user:password@redis:6379/3",
            )
        )

        self.assertIsInstance(instance.redis_settings, RedisSettings)
        assert instance.redis_settings is not None
        self.assertEqual(instance.redis_settings.host, "redis")
        self.assertEqual(instance.redis_settings.database, 3)
        self.assertEqual(instance.max_tries, worker.MAX_JOB_TRIES)
        self.assertEqual(instance.job_timeout_s, worker.JOB_TIMEOUT_SECONDS)

    async def test_startup_and_shutdown_manage_database_engine(self) -> None:
        engine = object()
        factory = object()
        context: dict[str, Any] = {"database_url": "postgresql://db/docdrift"}
        with (
            patch.object(worker, "create_session_factory", return_value=(factory, engine)) as create,
            patch.object(worker, "dispose_engine", new=AsyncMock()) as dispose,
        ):
            await worker.on_startup(context)
            self.assertIs(context["session_factory"], factory)
            self.assertIs(context["database_engine"], engine)
            await worker.on_shutdown(context)

        create.assert_called_once_with("postgresql://db/docdrift")
        dispose.assert_awaited_once_with(engine)
        self.assertNotIn("session_factory", context)
        self.assertNotIn("database_engine", context)

    async def test_worker_reports_state_returned_by_analysis_pipeline(self) -> None:
        factory = FakeSessionFactory(make_run())
        with patch.object(
            worker, "run_analysis_pipeline", new=AsyncMock(return_value=RunStatus.COMPLETED.value)
        ) as analyze:
            result = await worker.process_run({"session_factory": factory}, 5)

        self.assertEqual(result, {"run_id": 5, "state": RunStatus.COMPLETED.value})
        analyze.assert_awaited_once_with(factory, 5, checkout_component=None)

    async def test_missing_run_is_a_safe_no_op(self) -> None:
        self.assertEqual(
            await worker.process_run({"session_factory": FakeSessionFactory(None)}, 999),  # type: ignore[arg-type]
            {"run_id": 999, "state": "missing"},
        )

    async def test_terminal_state_is_not_reanalyzed_or_overwritten(self) -> None:
        for status in (RunStatus.COMPLETED, RunStatus.FAILED):
            with self.subTest(status=status):
                factory = FakeSessionFactory(make_run(status))
                result = await worker.process_run({"session_factory": factory}, 5)
                self.assertEqual(result, {"run_id": 5, "state": status.value})
                self.assertEqual(factory.session.run.status, status)

    async def test_transient_failure_retries_then_marks_active_run_failed(self) -> None:
        factory = FakeSessionFactory(make_run())
        context = {"session_factory": factory, "job_try": 1}
        with (
            patch.object(worker, "run_analysis_pipeline", side_effect=OSError("source secret")),
            patch.object(worker, "_mark_failed_if_active", new=AsyncMock()) as mark_failed,
            self.assertRaises(Retry) as raised,
        ):
            await worker.process_run(context, 5)
        self.assertEqual(raised.exception.defer_score, 1000)
        mark_failed.assert_not_awaited()

        context["job_try"] = worker.MAX_JOB_TRIES
        with (
            patch.object(worker, "run_analysis_pipeline", side_effect=OSError("temporary")),
            patch.object(worker, "_mark_failed_if_active", new=AsyncMock()) as mark_failed,
            self.assertRaises(worker.RunProcessingError),
        ):
            await worker.process_run(context, 5)
        mark_failed.assert_awaited_once_with(factory, 5)

    async def test_nontransient_failure_marks_run_failed_without_retry(self) -> None:
        factory = FakeSessionFactory(make_run())
        with (
            patch.object(worker, "run_analysis_pipeline", side_effect=ValueError("bad row")),
            patch.object(worker, "_mark_failed_if_active", new=AsyncMock()) as mark_failed,
            self.assertRaises(worker.RunProcessingError),
        ):
            await worker.process_run({"session_factory": factory}, 5)
        mark_failed.assert_awaited_once_with(factory, 5)


if __name__ == "__main__":
    unittest.main()
