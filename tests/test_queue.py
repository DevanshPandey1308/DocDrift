from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from arq.connections import RedisSettings

from docdrift_api.queue import enqueue_run, redis_settings_from_url, run_job_id


class QueueTests(unittest.IsolatedAsyncioTestCase):
    def test_redis_settings_parse_supported_dsn_without_logging_it(self) -> None:
        settings = redis_settings_from_url("rediss://user:secret@redis.example:6380/4")

        self.assertIsInstance(settings, RedisSettings)
        self.assertEqual(settings.host, "redis.example")
        self.assertEqual(settings.port, 6380)
        self.assertEqual(settings.database, 4)
        self.assertEqual(settings.username, "user")
        self.assertEqual(settings.password, "secret")
        self.assertTrue(settings.ssl)
        self.assertEqual(settings.conn_timeout, 1)
        self.assertEqual(settings.conn_retries, 1)
        self.assertEqual(settings.conn_retry_delay, 1)

    def test_missing_or_invalid_redis_configuration_is_reported_safely(self) -> None:
        for value in (None, "https://user:secret@example.invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError) as raised:
                redis_settings_from_url(value)
            self.assertNotIn("secret", str(raised.exception))

    def test_job_id_is_stable_and_rejects_invalid_run_ids(self) -> None:
        self.assertEqual(run_job_id(7), "docdrift:run:7")
        with self.assertRaises(ValueError):
            run_job_id(0)

    async def test_enqueue_uses_stable_job_id_and_reports_arq_duplicate(self) -> None:
        pool = AsyncMock()
        pool.enqueue_job = AsyncMock(return_value=None)

        submitted = await enqueue_run(pool, 12)

        self.assertFalse(submitted)
        pool.enqueue_job.assert_awaited_once_with(
            "process_run", 12, _job_id="docdrift:run:12"
        )


if __name__ == "__main__":
    unittest.main()
