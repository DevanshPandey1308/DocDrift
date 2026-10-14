from __future__ import annotations

import asyncio
import json
import unittest
from typing import TypedDict, cast
from unittest.mock import patch

from docdrift_api import app, create_app
from docdrift_api.config import Settings
from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Scope


class HealthPayload(TypedDict):
    status: str
    service: str
    version: str


async def request_health(application: FastAPI) -> tuple[int, dict[bytes, bytes], HealthPayload]:
    messages: list[Message] = []
    request_sent = False

    async def receive() -> Message:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 12345),
        "server": ("testserver", 80),
    }
    await cast(ASGIApp, application)(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        cast(bytes, message["body"])
        for message in messages
        if message["type"] == "http.response.body"
    )
    headers = cast(list[tuple[bytes, bytes]], start["headers"])
    return cast(int, start["status"]), dict(headers), cast(HealthPayload, json.loads(body))


class ApiTests(unittest.TestCase):
    def test_settings_read_supported_environment_variables(self) -> None:
        with patch.dict(
            "os.environ",
            {"DOCDRIFT_API_TITLE": "Configured API", "DOCDRIFT_API_VERSION": "2.3.4"},
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.title, "Configured API")
        self.assertEqual(settings.version, "2.3.4")

    def test_health_endpoint_returns_documented_json(self) -> None:
        status, headers, body = asyncio.run(
            request_health(create_app(Settings(title="Test API", version="2.3.4")))
        )

        self.assertEqual(status, 200)
        self.assertIn(b"application/json", headers[b"content-type"])
        self.assertEqual(body, {"status": "healthy", "service": "docdrift-api", "version": "2.3.4"})

    def test_app_imports_and_initializes_without_infrastructure_credentials(self) -> None:
        self.assertIsInstance(app, FastAPI)
        status, _, body = asyncio.run(request_health(create_app(Settings())))

        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "healthy")


if __name__ == "__main__":
    unittest.main()
