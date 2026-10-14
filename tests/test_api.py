from __future__ import annotations

import hashlib
import hmac
import unittest
from unittest.mock import patch

from docdrift_api import app, create_app
from docdrift_api.config import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response


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

    def _assert_error(self, response: Response, status: int, code: str, message: str) -> None:
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
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.title, "Configured API")
        self.assertEqual(settings.version, "2.3.4")
        self.assertEqual(settings.github_webhook_secret, self.secret)

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
        body = b'{ "action" : "opened", "pull_request": {} }\n'
        response = self.client.post("/webhooks/github", content=body, headers=self._headers(body))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "validated",
                "event": "pull_request",
                "delivery_id": "delivery-123",
                "message": "Webhook validated; no analysis was run or queued.",
            },
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
