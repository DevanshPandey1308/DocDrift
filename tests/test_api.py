from __future__ import annotations

import hashlib
import hmac
import unittest
from typing import Any, Protocol, cast
from unittest.mock import AsyncMock, patch

from docdrift_api import app, create_app
from docdrift_api.config import Settings
from docdrift_api.persistence import PersistenceResult
from docdrift_api.queue import run_job_id
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ResponseLike(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    def json(self) -> Any: ...


class FakeRedisPool:
    def __init__(self, *, duplicate: bool = False, error: Exception | None = None) -> None:
        self.duplicate = duplicate
        self.error = error
        self.enqueued: list[tuple[str, int, str]] = []

    async def enqueue_job(self, function: str, run_id: int, *, _job_id: str) -> object | None:
        if self.error is not None:
            raise self.error
        self.enqueued.append((function, run_id, _job_id))
        return None if self.duplicate else object()


class ApiTests(unittest.TestCase):
    secret = "local-test-secret-do-not-use-in-production"

    def setUp(self) -> None:
        self.client = TestClient(create_app(Settings(github_webhook_secret=self.secret)))

    def _signature(self, body: bytes, secret: str | None = None) -> str:
        signing_secret = (secret or self.secret).encode("utf-8")
        digest = hmac.new(signing_secret, body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    def _headers(
        self,
        body: bytes,
        *,
        event: str | None = "pull_request",
        delivery_id: str | None = "delivery-123",
        signature: str | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        if event is not None:
            headers["X-GitHub-Event"] = event
        if delivery_id is not None:
            headers["X-GitHub-Delivery"] = delivery_id
        if signature is not None:
            headers["X-Hub-Signature-256"] = signature
        else:
            headers["X-Hub-Signature-256"] = self._signature(body)
        return headers

    def _assert_error(self, response: ResponseLike, status: int, code: str, message: str) -> None:
        self.assertEqual(response.status_code, status)
        self.assertEqual(
            response.json(),
            {"error": {"code": code, "message": message}},
        )

    def test_settings_read_supported_environment_variables(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "DOCDRIFT_API_TITLE": "Configured API",
                "DOCDRIFT_API_VERSION": "2.3.4",
                "DOCDRIFT_GITHUB_WEBHOOK_SECRET": self.secret,
                "DOCDRIFT_DATABASE_URL": "postgresql://user:pass@localhost/docdrift",
                "DOCDRIFT_REDIS_URL": "redis://:pass@localhost:6379/2",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.title, "Configured API")
        self.assertEqual(settings.version, "2.3.4")
        self.assertEqual(settings.github_webhook_secret, self.secret)
        self.assertEqual(settings.database_url, "postgresql://user:pass@localhost/docdrift")
        self.assertEqual(settings.redis_url, "redis://:pass@localhost:6379/2")

    def test_health_endpoint_works_without_webhook_secret(self) -> None:
        client = TestClient(create_app(Settings()))
        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "healthy", "service": "docdrift-api", "version": "0.1.0"},
        )

    def test_app_imports_and_initializes_without_infrastructure_credentials(self) -> None:
        self.assertIsInstance(app, FastAPI)
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)

    def test_valid_pull_request_is_accepted_and_delivery_id_is_preserved(self) -> None:
        body = self._pull_request_body()
        queue = FakeRedisPool()
        client = self._database_client(queue)
        with patch(
            "docdrift_api.webhooks.persist_pull_request",
            new=AsyncMock(return_value=PersistenceResult(duplicate_delivery=False, run_id=19)),
        ) as persist:
            response = client.post("/webhooks/github", content=body, headers=self._headers(body))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json(),
            {
                "status": "accepted",
                "event": "pull_request",
                "delivery_id": "delivery-123",
                "message": "Webhook delivery and run were persisted and queued; analysis has not run.",
            },
        )
        persist.assert_awaited_once()
        self.assertEqual(queue.enqueued, [("process_run", 19, run_job_id(19))])

    def test_duplicate_delivery_returns_consistent_duplicate_response(self) -> None:
        body = self._pull_request_body()
        client = self._database_client(FakeRedisPool(duplicate=True))
        with patch(
            "docdrift_api.webhooks.persist_pull_request",
            new=AsyncMock(return_value=PersistenceResult(duplicate_delivery=True, run_id=19)),
        ) as persist:
            response = client.post(
                "/webhooks/github", content=body, headers=self._headers(body)
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "duplicate")
        self.assertEqual(response.json()["delivery_id"], "delivery-123")
        persist.assert_awaited_once()

    def test_duplicate_delivery_retries_queue_publication_after_previous_failure(self) -> None:
        body = self._pull_request_body()
        with patch(
            "docdrift_api.webhooks.persist_pull_request",
            new=AsyncMock(return_value=PersistenceResult(duplicate_delivery=True, run_id=19)),
        ):
            response = self._database_client().post(
                "/webhooks/github", content=body, headers=self._headers(body)
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "accepted")

    def test_missing_redis_configuration_does_not_acknowledge_persisted_run(self) -> None:
        body = self._pull_request_body()
        factory = cast(async_sessionmaker[AsyncSession], object())
        client = TestClient(
            create_app(Settings(github_webhook_secret=self.secret), session_factory=factory)
        )
        with patch(
            "docdrift_api.webhooks.persist_pull_request",
            new=AsyncMock(return_value=PersistenceResult(duplicate_delivery=False, run_id=19)),
        ):
            response = client.post("/webhooks/github", content=body, headers=self._headers(body))

        self._assert_error(
            response,
            503,
            "redis_not_configured",
            "Pull-request intake requires DOCDRIFT_REDIS_URL to be configured.",
        )

    def test_queue_failure_returns_retryable_error_without_exposing_exception(self) -> None:
        body = self._pull_request_body()
        client = self._database_client(FakeRedisPool(error=RuntimeError("redis://secret")))
        with patch(
            "docdrift_api.webhooks.persist_pull_request",
            new=AsyncMock(return_value=PersistenceResult(duplicate_delivery=False, run_id=19)),
        ):
            response = client.post("/webhooks/github", content=body, headers=self._headers(body))

        self._assert_error(
            response,
            503,
            "queue_unavailable",
            "The run was persisted but could not be queued; retry the webhook delivery.",
        )
        self.assertNotIn("redis://secret", response.text)

    def test_missing_pull_request_fields_are_rejected_before_database_access(self) -> None:
        body = b'{"action":"opened","pull_request":{}}'
        with patch("docdrift_api.webhooks.persist_pull_request", new=AsyncMock()) as persist:
            response = self.client.post(
                "/webhooks/github", content=body, headers=self._headers(body)
            )

        self._assert_error(
            response, 400, "invalid_payload", "repository must be an object."
        )
        persist.assert_not_awaited()

    def test_valid_pull_request_without_database_is_not_acknowledged_as_accepted(self) -> None:
        body = self._pull_request_body()
        response = self.client.post("/webhooks/github", content=body, headers=self._headers(body))

        self._assert_error(
            response,
            503,
            "database_not_configured",
            "Pull-request intake requires DOCDRIFT_DATABASE_URL to be configured.",
        )

    def test_missing_installation_is_stored_as_absent_not_invented(self) -> None:
        body = (
            b'{"action":"opened","repository":{"full_name":"owner/project"},'
            b'"pull_request":{"number":3,"head":{"sha":"abc123"}}}'
        )
        with patch(
            "docdrift_api.webhooks.persist_pull_request",
            new=AsyncMock(return_value=PersistenceResult(duplicate_delivery=False, run_id=19)),
        ) as persist:
            response = self._database_client().post(
                "/webhooks/github", content=body, headers=self._headers(body)
            )

        self.assertEqual(response.status_code, 202)
        arguments = persist.await_args
        assert arguments is not None
        record = arguments.kwargs["pull_request"]
        self.assertIsNone(record.installation_id)
        self.assertIsNone(record.github_repository_id)

    def test_database_failure_does_not_return_successful_acceptance(self) -> None:
        body = self._pull_request_body()
        with patch(
            "docdrift_api.webhooks.persist_pull_request",
            new=AsyncMock(side_effect=SQLAlchemyError("private database details")),
        ):
            response = self._database_client().post(
                "/webhooks/github", content=body, headers=self._headers(body)
            )

        self._assert_error(
            response,
            503,
            "persistence_failed",
            "Webhook delivery could not be durably recorded; retry the delivery.",
        )
        self.assertNotIn("private database details", response.text)

    def test_invalid_signature_is_rejected_before_persistence(self) -> None:
        body = self._pull_request_body()
        with patch("docdrift_api.webhooks.persist_pull_request", new=AsyncMock()) as persist:
            response = self._database_client().post(
                "/webhooks/github",
                content=body,
                headers=self._headers(body, signature="sha256=" + "0" * 64),
            )

        self._assert_error(response, 401, "invalid_signature", "Webhook signature is invalid.")
        persist.assert_not_awaited()

    def _database_client(self, redis_pool: FakeRedisPool | None = None) -> TestClient:
        # The factory is injected solely to exercise the API adapter without PostgreSQL.
        factory = cast(async_sessionmaker[AsyncSession], object())
        return TestClient(
            create_app(
                Settings(github_webhook_secret=self.secret),
                session_factory=factory,
                redis_pool=cast(Any, redis_pool or FakeRedisPool()),
            )
        )

    @staticmethod
    def _pull_request_body() -> bytes:
        return (
            b'{"action":"opened","installation":{"id":7},'
            b'"repository":{"id":42,"full_name":"docdrift/example"},'
            b'"pull_request":{"number":3,"head":{"sha":"abc123"}}}'
        )

    def test_invalid_signature_is_rejected_without_disclosing_secret(self) -> None:
        body = b'{"pull_request":'
        response = self.client.post(
            "/webhooks/github",
            content=body,
            headers=self._headers(body, signature="sha256=" + "0" * 64),
        )

        self._assert_error(response, 401, "invalid_signature", "Webhook signature is invalid.")
        self.assertNotIn(self.secret, response.text)

    def test_missing_signature_is_rejected(self) -> None:
        body = b"{}"
        headers = self._headers(body)
        del headers["X-Hub-Signature-256"]
        response = self.client.post("/webhooks/github", content=body, headers=headers)

        self._assert_error(
            response,
            401,
            "invalid_signature",
            "A valid X-Hub-Signature-256 header is required.",
        )

    def test_missing_event_header_is_rejected(self) -> None:
        body = b"{}"
        response = self.client.post(
            "/webhooks/github",
            content=body,
            headers=self._headers(body, event=None),
        )

        self._assert_error(
            response, 400, "missing_header", "Required header X-GitHub-Event is missing."
        )

    def test_missing_delivery_id_is_rejected(self) -> None:
        body = b"{}"
        response = self.client.post(
            "/webhooks/github",
            content=body,
            headers=self._headers(body, delivery_id=None),
        )

        self._assert_error(
            response, 400, "missing_header", "Required header X-GitHub-Delivery is missing."
        )

    def test_empty_or_oversized_delivery_id_is_rejected(self) -> None:
        body = b"{}"
        for delivery_id in ("   ", " delivery-123 ", "x" * 256):
            with self.subTest(delivery_id_length=len(delivery_id)):
                response = self.client.post(
                    "/webhooks/github",
                    content=body,
                    headers=self._headers(body, delivery_id=delivery_id),
                )
                self._assert_error(
                    response,
                    400,
                    "invalid_delivery_id",
                    "X-GitHub-Delivery must contain 1 to 255 characters.",
                )

    def test_malformed_json_with_valid_signature_is_rejected(self) -> None:
        for body in (b'{"pull_request":', b'{"value":NaN}', b"\xff"):
            with self.subTest(body=body):
                response = self.client.post(
                    "/webhooks/github", content=body, headers=self._headers(body)
                )

                self._assert_error(
                    response, 400, "malformed_json", "Request body must contain valid JSON."
                )

    def test_valid_json_non_object_is_rejected(self) -> None:
        for body in (b"[]", b"42", b'"text"', b"null"):
            with self.subTest(body=body):
                response = self.client.post(
                    "/webhooks/github", content=body, headers=self._headers(body)
                )
                self._assert_error(
                    response, 400, "invalid_payload", "Webhook JSON payload must be an object."
                )

    def test_unsupported_event_is_ignored_after_signature_validation(self) -> None:
        body = b'{"action":"created"}'
        response = self.client.post(
            "/webhooks/github",
            content=body,
            headers=self._headers(body, event="push"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ignored",
                "event": "push",
                "delivery_id": "delivery-123",
                "message": "Event type is not supported; no analysis was run.",
            },
        )

    def test_missing_server_secret_returns_safe_configuration_error(self) -> None:
        client = TestClient(create_app(Settings()))
        body = b"{}"
        response = client.post(
            "/webhooks/github", content=body, headers=self._headers(body)
        )

        self._assert_error(
            response,
            503,
            "webhook_not_configured",
            "GitHub webhook intake is not configured on this server.",
        )
        self.assertNotIn(self.secret, response.text)

    def test_signature_covers_exact_raw_request_bytes(self) -> None:
        original_body = b'{"pull_request":{}}'
        signed_headers = self._headers(original_body)
        changed_body = b'{ "pull_request": {} }'
        response = self.client.post(
            "/webhooks/github", content=changed_body, headers=signed_headers
        )

        self._assert_error(response, 401, "invalid_signature", "Webhook signature is invalid.")


if __name__ == "__main__":
    unittest.main()
