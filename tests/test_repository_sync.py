from __future__ import annotations

import hashlib
import unittest
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Self, cast
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.fernet import Fernet
from docdrift_api.config import Settings
from docdrift_api.dashboard import _csrf, _get_session
from docdrift_api.github_installations import (
    GitHubInstallationClient,
    GitHubInstallationSyncError,
)
from docdrift_api.main import create_app
from docdrift_api.models import DashboardSession, Repository
from docdrift_api.repository_sync import persist_repository_sync
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


_TEST_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_KEY = _TEST_PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode("ascii")


class GitHubAppJWTConfigurationTests(unittest.TestCase):
    def test_app_jwt_classifies_missing_and_invalid_configuration_safely(self) -> None:
        cases = [
            (None, "key", "missing_app_id"),
            ("１２３", "key", "invalid_app_id"),
            ("123", None, "missing_private_key"),
        ]
        for app_id, private_key, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                client = GitHubInstallationClient(app_id, private_key)
                with self.assertRaises(GitHubInstallationSyncError) as raised:
                    client._app_jwt()
                self.assertEqual(raised.exception.diagnostic_code, expected_code)
                self.assertEqual(raised.exception.operation, "app_authentication")

    def test_app_jwt_reports_malformed_pem_without_exposing_it(self) -> None:
        malformed_key = "not-a-private-key-secret"
        client = GitHubInstallationClient("123", malformed_key)
        with self.assertRaises(GitHubInstallationSyncError) as raised:
            client._app_jwt()
        self.assertEqual(raised.exception.diagnostic_code, "invalid_private_key_format")
        self.assertNotIn(malformed_key, str(raised.exception))

    def test_app_jwt_accepts_rsa_pem_and_uses_rs256(self) -> None:
        import jwt
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        token = GitHubInstallationClient("123", pem.decode("ascii"))._app_jwt()
        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        claims = jwt.decode(token, public_key, algorithms=["RS256"])
        self.assertEqual(claims["iss"], "123")
        self.assertLessEqual(claims["exp"] - claims["iat"], 600)

    def test_settings_loads_literal_escaped_and_actual_pem_newlines(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        common = {"DOCDRIFT_GITHUB_APP_ID": "123"}
        with patch.dict("os.environ", {**common, "DOCDRIFT_GITHUB_APP_PRIVATE_KEY": pem}):
            actual_newline = Settings.from_env().github_app_private_key
        with patch.dict(
            "os.environ",
            {**common, "DOCDRIFT_GITHUB_APP_PRIVATE_KEY": pem.replace("\n", "\\n")},
        ):
            escaped_newline = Settings.from_env().github_app_private_key
        self.assertEqual(actual_newline, pem)
        self.assertEqual(escaped_newline, pem)


class GitHubInstallationClientTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler: Callable[[httpx.Request], httpx.Response]) -> GitHubInstallationClient:
        def factory(**kwargs: Any) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

        return GitHubInstallationClient("123", _TEST_KEY, client_factory=factory)

    async def test_lists_authorized_installations_and_all_repository_pages(self) -> None:
        requests: list[tuple[str, str | None, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.url.path, request.headers.get("authorization"), request.url.params.get("page")))
            if request.url.path == "/user/repos":
                return httpx.Response(200, json=[
                    {"id": 100, "full_name": "org/one"},
                    {"id": 101, "full_name": "org/two"},
                ])
            if request.url.path == "/app/installations":
                self.assertTrue(request.headers["authorization"].startswith("Bearer app-jwt"))
                return httpx.Response(200, json=[{"id": 17}, {"id": 18}])
            if request.url.path.endswith("/access_tokens"):
                self.assertTrue(request.headers["authorization"].startswith("Bearer app-jwt"))
                return httpx.Response(201, json={"token": f"installation-token-{request.url.path.split('/')[-2]}"})
            if request.url.path == "/installation/repositories":
                installation_token = request.headers["authorization"]
                if installation_token.endswith("17"):
                    if request.url.params.get("page") == "1":
                        return httpx.Response(200, json={"total_count": 2, "repositories": [{"id": 100, "full_name": "org/one"}]})
                    return httpx.Response(200, json={"total_count": 2, "repositories": [{"id": 101, "full_name": "org/two"}]})
                return httpx.Response(200, json={"total_count": 0, "repositories": []})
            self.fail(f"Unexpected request: {request.url}")
            return httpx.Response(500)

        with patch("docdrift_api.github_installations.jwt.encode", return_value="app-jwt"):
            result = await self.client(handler).synchronize("oauth-user-token")

        self.assertEqual([item.github_installation_id for item in result], [17])
        self.assertEqual(
            [(repo.github_repository_id, repo.full_name) for repo in result[0].repositories],
            [(100, "org/one"), (101, "org/two")],
        )
        self.assertEqual(requests[0][0], "/user/repos")
        self.assertEqual(requests[0][1], "Bearer oauth-user-token")
        self.assertEqual(requests[0][2], "1")
        self.assertTrue(any(path == "/installation/repositories" and page == "2" for path, _, page in requests))
        self.assertTrue(any(path == "/installation/repositories" and page == "2" for path, _, page in requests))
        self.assertTrue(all(_TEST_KEY not in str(record) for record in requests))

    async def test_empty_authorized_installation_list_is_successful(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/user/repos":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[])

        client = self.client(handler)
        self.assertEqual(await client.synchronize("oauth-token"), ())

    async def test_github_api_failure_is_sanitized(self) -> None:
        client = self.client(lambda _: httpx.Response(403, json={"message": "private details"}))
        with self.assertRaisesRegex(GitHubInstallationSyncError, "synchronization failed") as raised:
            await client.synchronize("oauth-token")
        self.assertNotIn("private details", str(raised.exception))

    async def test_missing_app_configuration_fails_closed(self) -> None:
        with self.assertRaises(GitHubInstallationSyncError):
            await GitHubInstallationClient(None, None).synchronize("oauth-token")

    async def test_duplicate_installation_and_repository_ids_are_deduplicated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/user/repos":
                return httpx.Response(200, json=[{"id": 100, "full_name": "org/one"}])
            if request.url.path == "/app/installations":
                return httpx.Response(200, json=[{"id": 17}, {"id": 17}])
            if request.url.path.endswith("/access_tokens"):
                return httpx.Response(201, json={"token": "installation-token"})
            return httpx.Response(200, json={"total_count": 2, "repositories": [
                {"id": 100, "full_name": "org/one"}, {"id": 100, "full_name": "org/renamed"},
            ]})

        with patch("docdrift_api.github_installations.jwt.encode", return_value="app-jwt"):
            result = await self.client(handler).synchronize("oauth-token")
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].repositories), 1)
        self.assertEqual(result[0].repositories[0].full_name, "org/one")


class _Transaction:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _Session:
    def __init__(self, scalar_results: list[object]) -> None:
        self.scalar_results = iter(scalar_results)
        self.statements: list[object] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> _Transaction:
        return _Transaction()

    async def execute(self, statement: object) -> None:
        self.statements.append(statement)

    async def scalar(self, _statement: object) -> object:
        return next(self.scalar_results)


class RepositorySyncPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_repository_id_is_updated_for_rename_and_installation(self) -> None:
        from docdrift_api.github_installations import (
            GitHubInstallationRepositories,
            GitHubRepository,
        )

        existing = type("RepositoryRecord", (), {"id": 5, "github_repository_id": 100, "full_name": "org/old"})()
        session = _Session([7, existing, None])
        factory = type("Factory", (), {"__call__": lambda self: session})()
        count = await persist_repository_sync(
            cast(async_sessionmaker[AsyncSession], factory),
            [GitHubInstallationRepositories(17, (GitHubRepository(100, "org/new"),))],
        )
        self.assertEqual(count, 1)
        updates = [statement for statement in session.statements if statement.__class__.__name__ == "Update"]
        self.assertTrue(any("org/new" in str(cast(Any, statement).compile().params) for statement in updates))

    async def test_repeated_sync_updates_the_same_repository_record(self) -> None:
        from docdrift_api.github_installations import (
            GitHubInstallationRepositories,
            GitHubRepository,
        )

        existing = type("RepositoryRecord", (), {"id": 5, "github_repository_id": 100, "full_name": "org/one"})()
        session = _Session([7, existing, existing, 7, existing, existing])
        factory = type("Factory", (), {"__call__": lambda self: session})()
        data = [GitHubInstallationRepositories(17, (GitHubRepository(100, "org/one"),))]
        session_factory = cast(async_sessionmaker[AsyncSession], factory)
        self.assertEqual(await persist_repository_sync(session_factory, data), 1)
        self.assertEqual(await persist_repository_sync(session_factory, data), 1)
        updates = [statement for statement in session.statements if statement.__class__.__name__ == "Update"]
        repository_upserts = [
            statement for statement in updates if "full_name" in cast(Any, statement).compile().params
        ]
        stale_detachments = [
            statement
            for statement in updates
            if cast(Any, statement).compile().params.get("installation_id") is None
        ]
        self.assertEqual(len(repository_upserts), 2)
        self.assertEqual(len(stale_detachments), 2)


class RepositorySyncRouteTests(unittest.TestCase):
    def authenticated_listing_app(
        self, handler: Callable[[httpx.Request], httpx.Response]
    ) -> tuple[Any, str]:
        session_token = "session-token-for-test"
        encryption_key = Fernet.generate_key().decode("ascii")
        dashboard_session = DashboardSession(
            id=1,
            token_hash=hashlib.sha256(session_token.encode()).hexdigest(),
            github_user_id=42,
            github_login="maintainer",
            encrypted_access_token=Fernet(encryption_key.encode()).encrypt(
                b"oauth-token-for-test"
            ).decode("ascii"),
            csrf_token_hash="unused",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        repositories = [
            Repository(
                id=5,
                installation_id=7,
                github_repository_id=100,
                full_name="old-owner/old-name",
            ),
            Repository(
                id=6,
                installation_id=7,
                github_repository_id=200,
                full_name="owner/not-installed",
            ),
            Repository(
                id=7,
                installation_id=None,
                github_repository_id=300,
                full_name="owner/no-longer-installed",
            ),
        ]
        class Session:
            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def scalar(self, _statement: object) -> DashboardSession:
                return dashboard_session

            async def scalars(self, _statement: object) -> list[Repository]:
                return repositories

        app = create_app(
            Settings(
                github_app_id="123",
                github_app_private_key=_TEST_KEY,
                dashboard_session_encryption_key=encryption_key,
            ),
            session_factory=cast(async_sessionmaker[AsyncSession], lambda: Session()),
        )
        app.state.github_http_client_factory = lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        )
        return app, session_token

    def test_repository_list_uses_oauth_repo_access_and_live_app_installations(self) -> None:
        requests: list[tuple[str, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.url.path, request.headers.get("authorization")))
            if request.url.path == "/user/repos":
                return httpx.Response(200, json=[
                    {"id": 100, "full_name": "DevanshPandey1308/docdrift-test-repo"},
                    {"id": 200, "full_name": "owner/not-installed"},
                ])
            if request.url.path == "/app/installations":
                return httpx.Response(200, json=[{"id": 17}])
            if request.url.path.endswith("/access_tokens"):
                return httpx.Response(201, json={"token": "installation-token"})
            if request.url.path == "/installation/repositories":
                return httpx.Response(200, json={
                    "total_count": 1,
                    "repositories": [
                        {"id": 100, "full_name": "DevanshPandey1308/docdrift-test-repo"}
                    ],
                })
            self.fail(f"Unexpected GitHub request: {request.url.path}")
            return httpx.Response(500)

        app, session_token = self.authenticated_listing_app(handler)
        with (
            patch("docdrift_api.github_installations.jwt.encode", return_value="app-jwt"),
            TestClient(app) as client,
        ):
            response = client.get(
                "/repositories", cookies={"docdrift_session": session_token}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [{"id": 5, "full_name": "DevanshPandey1308/docdrift-test-repo"}],
        )
        self.assertEqual(requests, [
            ("/user/repos", "Bearer oauth-token-for-test"),
            ("/app/installations", "Bearer app-jwt"),
            ("/app/installations/17/access_tokens", "Bearer app-jwt"),
            ("/installation/repositories", "Bearer installation-token"),
        ])

    def test_repository_list_logs_sanitized_upstream_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/app/installations":
                return httpx.Response(403, json={"message": "sensitive upstream detail"})
            if request.url.path == "/user/repos":
                return httpx.Response(200, json=[])
            self.fail(f"Unexpected GitHub request: {request.url.path}")
            return httpx.Response(500)

        app, session_token = self.authenticated_listing_app(handler)
        with (
            patch("docdrift_api.github_installations.jwt.encode", return_value="app-jwt"),
            self.assertLogs("docdrift_api.dashboard", level="WARNING") as logs,
            TestClient(app) as client,
        ):
            response = client.get(
                "/repositories", cookies={"docdrift_session": session_token}
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("operation=app_installations", logs.output[0])
        self.assertIn("upstream_status=403", logs.output[0])
        self.assertIn("upstream_detail=sensitive upstream detail", logs.output[0])
        self.assertNotIn("oauth-token-for-test", logs.output[0])

    def test_repository_list_redacts_oauth_token_from_upstream_message(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/user/repos":
                return httpx.Response(
                    401,
                    json={"message": "Rejected credential oauth-token-for-test"},
                )
            self.fail(f"Unexpected GitHub request: {request.url.path}")
            return httpx.Response(500)

        app, session_token = self.authenticated_listing_app(handler)
        with (
            patch("docdrift_api.github_installations.jwt.encode", return_value="app-jwt"),
            self.assertLogs("docdrift_api.dashboard", level="WARNING") as logs,
            TestClient(app) as client,
        ):
            response = client.get(
                "/repositories", cookies={"docdrift_session": session_token}
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("operation=user_repositories", logs.output[0])
        self.assertIn("upstream_status=401", logs.output[0])
        self.assertIn("upstream_detail=Rejected credential [redacted]", logs.output[0])
        self.assertNotIn("oauth-token-for-test", logs.output[0])

    def test_authenticated_sync_uses_oauth_authorized_installation_not_browser_id(self) -> None:
        def factory(**kwargs: Any) -> httpx.AsyncClient:
            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/user/repos":
                    self.assertEqual(request.headers["authorization"], "Bearer oauth-token")
                    return httpx.Response(200, json=[{"id": 100, "full_name": "org/visible"}])
                if request.url.path == "/app/installations":
                    self.assertEqual(request.headers["authorization"], "Bearer app-jwt")
                    return httpx.Response(200, json=[{"id": 17}])
                if request.url.path.endswith("/access_tokens"):
                    return httpx.Response(201, json={"token": "hidden-installation-token"})
                return httpx.Response(200, json={"total_count": 1, "repositories": [
                    {"id": 100, "full_name": "org/visible"},
                ]})

            return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

        app = create_app(
            Settings(github_app_id="123", github_app_private_key=_TEST_KEY),
            session_factory=cast(async_sessionmaker[AsyncSession], object()),
        )
        async def authenticated_session() -> tuple[object, str]:
            return object(), "oauth-token"

        async def csrf_ok() -> None:
            return None

        app.dependency_overrides[_get_session] = authenticated_session
        app.dependency_overrides[_csrf] = csrf_ok
        app.state.github_http_client_factory = factory
        with (
            patch("docdrift_api.dashboard.persist_repository_sync", return_value=1) as persist,
            patch("docdrift_api.github_installations.jwt.encode", return_value="app-jwt"),
            TestClient(app) as client,
        ):
            response = client.post("/repositories/sync", json={"installation_id": 999})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"synced_installations": 1, "synced_repositories": 1})
        self.assertIsNotNone(persist.await_args)
        persisted = cast(Any, persist.await_args).args[1]
        self.assertEqual([item.github_installation_id for item in persisted], [17])

    def test_sync_route_rejects_anonymous_users(self) -> None:
        with TestClient(create_app(Settings())) as client:
            response = client.post("/repositories/sync")
        self.assertEqual(response.status_code, 401)

    def test_sync_route_requires_csrf_even_for_an_authenticated_session(self) -> None:
        app = create_app(Settings())

        async def authenticated_session() -> tuple[object, str]:
            return object(), "oauth-token"

        app.dependency_overrides[_get_session] = authenticated_session
        with TestClient(app) as client:
            response = client.post("/repositories/sync")
        self.assertEqual(response.status_code, 403)
