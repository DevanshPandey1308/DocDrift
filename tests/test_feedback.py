from __future__ import annotations

import unittest
from typing import Self
from unittest.mock import AsyncMock

from docdrift_api.config import Settings
from docdrift_api.dashboard import _state, _valid_state
from docdrift_api.feedback import (
    FeedbackTargetNotFound,
    calculate_acceptance_metrics,
    list_feedback_for_finding,
    record_feedback,
)
from docdrift_api.main import create_app
from docdrift_api.models import FeedbackCategory, FeedbackEvent
from fastapi.testclient import TestClient


class _Context:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _Session:
    def __init__(self, scalar_values: list[object], event: FeedbackEvent | None = None) -> None:
        self._scalar_values = iter(scalar_values)
        self._event = event
        self.get = AsyncMock(return_value=event)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> _Context:
        return _Context()

    async def scalar(self, _statement: object) -> object:
        return next(self._scalar_values)

    async def scalars(self, _statement: object) -> list[object]:
        return [self._event] if self._event is not None else []


class FeedbackMetricsTests(unittest.TestCase):
    def test_acceptance_rate_excludes_false_positives_and_unknown_outcomes(self) -> None:
        metrics = calculate_acceptance_metrics(
            ["fix_accepted", "fix_rejected", "fix_accepted", "false_positive", "unknown"]
        )
        self.assertEqual(metrics.accepted_fixes, 2)
        self.assertEqual(metrics.rejected_fixes, 1)
        self.assertEqual(metrics.false_positives, 1)
        self.assertAlmostEqual(metrics.acceptance_rate or 0, 2 / 3)

    def test_zero_fix_outcomes_has_unknown_rate(self) -> None:
        metrics = calculate_acceptance_metrics(["false_positive", "unknown"])
        self.assertEqual(metrics.accepted_fixes, 0)
        self.assertEqual(metrics.rejected_fixes, 0)
        self.assertIsNone(metrics.acceptance_rate)

    def test_dashboard_routes_exist_and_require_auth_dependencies(self) -> None:
        paths = create_app(Settings()).openapi()["paths"]
        self.assertIn("/auth/github/login", paths)
        self.assertIn("/auth/session", paths)
        self.assertIn("/repositories", paths)
        self.assertIn("/repositories/{repository_id}/runs", paths)
        self.assertIn("/runs/{run_id}", paths)
        self.assertIn("/suggestions/{suggestion_id}/feedback", paths)
        self.assertIn("/findings/{finding_id}/feedback", paths)
        self.assertIn("/runs/{run_id}/feedback", paths)
        self.assertIn("/repositories/{repository_id}/feedback/metrics", paths)

    def test_oauth_state_signature_and_age_are_validated(self) -> None:
        settings = Settings(dashboard_session_encryption_key="bXlfc2VjcmV0X2tleQ==")
        value = _state(settings, "nonce", 1000)
        self.assertTrue(_valid_state(settings, value, now=1100))
        self.assertFalse(_valid_state(settings, value + "x", now=1100))
        self.assertFalse(_valid_state(settings, value, now=1700))

    def test_auth_configuration_fails_closed_and_private_routes_reject_anonymous(self) -> None:
        app = create_app(Settings())
        with TestClient(app) as client:
            self.assertEqual(client.get("/auth/github/login").status_code, 503)
            self.assertEqual(client.get("/auth/session").status_code, 401)
            self.assertEqual(client.get("/repositories").status_code, 401)
            self.assertEqual(client.get("/repositories/1/runs").status_code, 401)
            self.assertEqual(client.get("/runs/1").status_code, 401)
            self.assertEqual(
                client.get("/repositories/1/feedback/metrics").status_code, 401
            )

    def test_dashboard_cors_allows_only_the_configured_frontend_origin(self) -> None:
        app = create_app(Settings(dashboard_origin="http://localhost:5173"))
        with TestClient(app) as client:
            allowed = client.options(
                "/auth/session",
                headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
            )
            denied = client.options(
                "/auth/session",
                headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "GET"},
            )
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), "http://localhost:5173")
        self.assertEqual(allowed.headers.get("access-control-allow-credentials"), "true")
        self.assertNotIn("access-control-allow-origin", denied.headers)

    def test_dashboard_origin_rejects_paths_and_non_http_schemes(self) -> None:
        with self.assertRaisesRegex(ValueError, r"absolute HTTP\(S\) origin"):
            Settings(dashboard_origin="https://example.test/dashboard")
        with self.assertRaisesRegex(ValueError, r"absolute HTTP\(S\) origin"):
            Settings(dashboard_origin="javascript:alert(1)")


class FeedbackPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_mismatched_finding_run_repository(self) -> None:
        session = _Session([None])
        with self.assertRaises(FeedbackTargetNotFound):
            await record_feedback(
                lambda: session,  # type: ignore[arg-type]
                finding_id=5,
                run_id=6,
                repository_id=7,
                category=FeedbackCategory.FIX_ACCEPTED,
                source="explicit_api",
                source_event_id="event-1",
            )

    async def test_persists_explicit_feedback_for_valid_relationship(self) -> None:
        event = FeedbackEvent(
            id=12,
            finding_id=5,
            run_id=6,
            repository_id=7,
            category=FeedbackCategory.FIX_ACCEPTED,
            source="explicit_api",
            source_event_id="event-1",
        )
        session = _Session([5, 12], event)
        result = await record_feedback(
            lambda: session,  # type: ignore[arg-type]
            finding_id=5,
            run_id=6,
            repository_id=7,
            category=FeedbackCategory.FIX_ACCEPTED,
            source="explicit_api",
            source_event_id="event-1",
        )
        self.assertIs(result, event)
        session.get.assert_awaited_once_with(FeedbackEvent, 12)

    async def test_duplicate_logical_event_returns_existing_record(self) -> None:
        event = FeedbackEvent(
            id=12,
            finding_id=5,
            run_id=6,
            repository_id=7,
            category=FeedbackCategory.FIX_REJECTED,
            source="explicit_api",
            source_event_id="event-1",
        )
        session = _Session([5, None, event])
        result = await record_feedback(
            lambda: session,  # type: ignore[arg-type]
            finding_id=5,
            run_id=6,
            repository_id=7,
            category=FeedbackCategory.FIX_REJECTED,
            source="explicit_api",
            source_event_id="event-1",
        )
        self.assertIs(result, event)

    async def test_rejects_invalid_idempotency_metadata(self) -> None:
        with self.assertRaises(ValueError):
            await record_feedback(
                lambda: None,  # type: ignore[arg-type]
                finding_id=5,
                run_id=6,
                repository_id=7,
                category=FeedbackCategory.FIX_ACCEPTED,
                source="",
                source_event_id="event-1",
            )

    async def test_retrieval_checks_finding_run_repository_tuple(self) -> None:
        event = FeedbackEvent(
            id=12,
            finding_id=5,
            run_id=6,
            repository_id=7,
            category=FeedbackCategory.FALSE_POSITIVE,
            source="explicit_api",
            source_event_id="event-2",
        )
        session = _Session([5], event)
        events = await list_feedback_for_finding(
            session, finding_id=5, run_id=6, repository_id=7  # type: ignore[arg-type]
        )
        self.assertEqual(events, [event])

        mismatched = _Session([None])
        with self.assertRaises(FeedbackTargetNotFound):
            await list_feedback_for_finding(
                mismatched, finding_id=5, run_id=6, repository_id=8  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
