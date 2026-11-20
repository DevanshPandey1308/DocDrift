"""GitHub App installation repository discovery for authenticated dashboard users."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

GITHUB_API = "https://api.github.com"
_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "DocDrift",
}


class GitHubInstallationSyncError(RuntimeError):
    """Sanitized error while discovering repositories for an installation."""

    def __init__(
        self,
        message: str,
        *,
        operation: str = "github_api",
        status_code: int | None = None,
        upstream_detail: str | None = None,
        diagnostic_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.status_code = status_code
        self.upstream_detail = upstream_detail
        self.diagnostic_code = diagnostic_code


@dataclass(frozen=True)
class GitHubRepository:
    github_repository_id: int
    full_name: str


@dataclass(frozen=True)
class GitHubInstallationRepositories:
    github_installation_id: int
    repositories: tuple[GitHubRepository, ...]


class GitHubInstallationClient:
    """Intersect repositories visible to a dashboard user with the app's inventory."""

    def __init__(
        self,
        app_id: str | None,
        private_key: str | None,
        *,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self.app_id = app_id
        self.private_key = private_key
        self.client_factory = client_factory

    def _app_jwt(self) -> str:
        if not self.app_id:
            raise GitHubInstallationSyncError(
                "GitHub App ID is not configured.",
                operation="app_authentication",
                diagnostic_code="missing_app_id",
            )
        if not self.app_id.isascii() or not self.app_id.isdecimal():
            raise GitHubInstallationSyncError(
                "GitHub App ID must contain decimal digits.",
                operation="app_authentication",
                diagnostic_code="invalid_app_id",
            )
        if not self.private_key:
            raise GitHubInstallationSyncError(
                "GitHub App private key is not configured.",
                operation="app_authentication",
                diagnostic_code="missing_private_key",
            )
        try:
            from cryptography.exceptions import UnsupportedAlgorithm
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError as error:
            raise GitHubInstallationSyncError(
                "Cryptographic support for GitHub App authentication is unavailable.",
                operation="app_authentication",
                diagnostic_code="cryptography_dependency_missing",
            ) from error
        try:
            private_key = serialization.load_pem_private_key(
                self.private_key.encode("utf-8"), password=None
            )
        except UnsupportedAlgorithm as error:
            raise GitHubInstallationSyncError(
                "GitHub App private key uses an unsupported algorithm.",
                operation="app_authentication",
                diagnostic_code="unsupported_private_key_algorithm",
            ) from error
        except (TypeError, ValueError) as error:
            raise GitHubInstallationSyncError(
                "GitHub App private key is malformed, encrypted, or not PEM encoded.",
                operation="app_authentication",
                diagnostic_code="invalid_private_key_format",
            ) from error
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise GitHubInstallationSyncError(
                "GitHub App private key must be an RSA key for RS256 signing.",
                operation="app_authentication",
                diagnostic_code="unsupported_private_key_algorithm",
            )
        now = int(time.time())
        try:
            return jwt.encode(
                {"iat": now - 60, "exp": now + 540, "iss": self.app_id},
                private_key,
                algorithm="RS256",
            )
        except (jwt.PyJWTError, ValueError, TypeError) as error:
            raise GitHubInstallationSyncError(
                "GitHub App JWT signing failed.",
                operation="app_authentication",
                diagnostic_code="jwt_signing_failed",
            ) from error

    @staticmethod
    def _json_object(response: httpx.Response, *, operation: str) -> dict[str, Any]:
        try:
            result = response.json()
        except ValueError as error:
            raise GitHubInstallationSyncError(
                "GitHub returned an invalid response.", operation=operation
            ) from error
        if not isinstance(result, dict):
            raise GitHubInstallationSyncError(
                "GitHub returned an invalid response.", operation=operation
            )
        return result

    @staticmethod
    def _raise_response(
        response: httpx.Response,
        *,
        expected_status: int,
        operation: str,
        sensitive_values: tuple[str, ...] = (),
    ) -> None:
        if response.status_code != expected_status:
            # Only GitHub's short message field is useful for diagnostics. Never retain
            # response bodies, headers, or credentials as exception details.
            detail: str | None = None
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and isinstance(body.get("message"), str):
                detail = body["message"]
                for secret in sensitive_values:
                    if secret:
                        detail = detail.replace(secret, "[redacted]")
                detail = re.sub(
                    r"-----BEGIN [^-]+PRIVATE KEY-----.*?-----END [^-]+PRIVATE KEY-----",
                    "[redacted private key]",
                    detail,
                    flags=re.DOTALL,
                )
                detail = re.sub(
                    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
                    "[redacted token]",
                    detail,
                )
                detail = " ".join(detail.split())[:300] or None
            raise GitHubInstallationSyncError(
                "GitHub repository synchronization failed.",
                operation=operation,
                status_code=response.status_code,
                upstream_detail=detail,
            )

    async def _paged_array(
        self,
        client: httpx.AsyncClient,
        path: str,
        token: str,
        *,
        operation: str,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, 10_001):
            response = await client.get(
                f"{GITHUB_API}{path}",
                params={"per_page": page_size, "page": page},
                headers={**_API_HEADERS, "Authorization": f"Bearer {token}"},
            )
            self._raise_response(
                response,
                expected_status=200,
                operation=operation,
                sensitive_values=(token,),
            )
            try:
                page_items = response.json()
            except ValueError as error:
                raise GitHubInstallationSyncError(
                    "GitHub returned an invalid response.", operation=operation
                ) from error
            if not isinstance(page_items, list) or not all(
                isinstance(item, dict) for item in page_items
            ) or len(page_items) > page_size:
                raise GitHubInstallationSyncError(
                    "GitHub returned an invalid response.", operation=operation
                )
            items.extend(page_items)
            if len(page_items) < page_size:
                return items
        raise GitHubInstallationSyncError(
            "GitHub returned incomplete paginated results.", operation=operation
        )

    async def _paged_collection(
        self,
        client: httpx.AsyncClient,
        path: str,
        token: str,
        collection_name: str,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        expected_total: int | None = None
        while True:
            response = await client.get(
                f"{GITHUB_API}{path}",
                params={"per_page": 100, "page": page},
                headers={**_API_HEADERS, "Authorization": f"Bearer {token}"},
            )
            self._raise_response(
                response,
                expected_status=200,
                operation="installation_repositories",
                sensitive_values=(token,),
            )
            body = self._json_object(
                response, operation="installation_repositories"
            )
            page_items = body.get(collection_name)
            total = body.get("total_count")
            if (
                not isinstance(page_items, list)
                or isinstance(total, bool)
                or not isinstance(total, int)
                or total < 0
            ):
                raise GitHubInstallationSyncError(
                    "GitHub returned an invalid response.", operation="installation_repositories"
                )
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise GitHubInstallationSyncError(
                    "GitHub results changed during synchronization.",
                    operation="installation_repositories",
                )
            if not all(isinstance(item, dict) for item in page_items):
                raise GitHubInstallationSyncError(
                    "GitHub returned an invalid response.",
                    operation="installation_repositories",
                )
            items.extend(page_items)
            if len(items) == expected_total:
                return items
            if len(items) > expected_total or not page_items or page >= 10_000:
                raise GitHubInstallationSyncError(
                    "GitHub returned incomplete paginated results.",
                    operation="installation_repositories",
                )
            page += 1

    async def _app_installation_ids(self, client: httpx.AsyncClient) -> list[int]:
        installations = await self._paged_array(
            client,
            "/app/installations",
            self._app_jwt(),
            operation="app_installations",
        )
        ids: list[int] = []
        seen: set[int] = set()
        for installation in installations:
            installation_id = installation.get("id")
            if (
                isinstance(installation_id, bool)
                or not isinstance(installation_id, int)
                or installation_id < 1
            ):
                raise GitHubInstallationSyncError(
                    "GitHub returned an invalid installation response.",
                    operation="app_installations",
                )
            if installation_id not in seen:
                seen.add(installation_id)
                ids.append(installation_id)
        return ids

    async def _user_repositories(
        self, client: httpx.AsyncClient, oauth_token: str
    ) -> dict[int, GitHubRepository]:
        repositories = await self._paged_array(
            client, "/user/repos", oauth_token, operation="user_repositories"
        )
        result: dict[int, GitHubRepository] = {}
        for repository in repositories:
            repository_id = repository.get("id")
            full_name = repository.get("full_name")
            if (
                isinstance(repository_id, bool)
                or not isinstance(repository_id, int)
                or repository_id < 1
                or not isinstance(full_name, str)
                or len(full_name) > 255
                or full_name.count("/") != 1
                or any(not part or part in {".", ".."} for part in full_name.split("/"))
            ):
                raise GitHubInstallationSyncError(
                    "GitHub returned an invalid repository response.",
                    operation="user_repositories",
                )
            result[repository_id] = GitHubRepository(repository_id, full_name)
        return result

    async def synchronize(self, oauth_token: str) -> tuple[GitHubInstallationRepositories, ...]:
        """Sync every app installation the signed-in OAuth user is authorized to access."""
        if not self.app_id or not self.app_id.isdecimal() or not self.private_key:
            raise GitHubInstallationSyncError(
                "GitHub App installation sync is not configured.",
                operation="app_authentication",
                diagnostic_code=(
                    "missing_app_id"
                    if not self.app_id
                    else "invalid_app_id"
                    if not self.app_id.isascii() or not self.app_id.isdecimal()
                    else "missing_private_key"
                    if not self.private_key
                    else None
                ),
            )
        try:
            async with self.client_factory(
                timeout=httpx.Timeout(10.0), follow_redirects=False, headers=_API_HEADERS
            ) as client:
                user_repositories = await self._user_repositories(client, oauth_token)
                installation_ids = await self._app_installation_ids(client)
                results: list[GitHubInstallationRepositories] = []
                for installation_id in installation_ids:
                    app_jwt = self._app_jwt()
                    token_response = await client.post(
                        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
                        headers={**_API_HEADERS, "Authorization": f"Bearer {app_jwt}"},
                        json={"permissions": {"metadata": "read"}},
                    )
                    self._raise_response(
                        token_response,
                        expected_status=201,
                        operation="installation_token",
                        sensitive_values=(app_jwt,),
                    )
                    installation_token = self._json_object(
                        token_response, operation="installation_token"
                    ).get("token")
                    if not isinstance(installation_token, str) or not installation_token:
                        raise GitHubInstallationSyncError(
                            "GitHub returned an invalid installation token response.",
                            operation="installation_token",
                        )
                    repositories = await self._paged_collection(
                        client,
                        "/installation/repositories",
                        installation_token,
                        "repositories",
                    )
                    normalized: list[GitHubRepository] = []
                    seen_repositories: set[int] = set()
                    for repository in repositories:
                        repository_id = repository.get("id")
                        full_name = repository.get("full_name")
                        if (
                            isinstance(repository_id, bool)
                            or not isinstance(repository_id, int)
                            or repository_id < 1
                            or not isinstance(full_name, str)
                            or len(full_name) > 255
                            or full_name.count("/") != 1
                            or any(not part or part in {".", ".."} for part in full_name.split("/"))
                        ):
                            raise GitHubInstallationSyncError(
                                "GitHub returned an invalid repository response."
                            )
                        if repository_id in seen_repositories:
                            continue
                        seen_repositories.add(repository_id)
                        normalized.append(GitHubRepository(repository_id, full_name))
                    if any(repo.github_repository_id in user_repositories for repo in normalized):
                        results.append(
                            GitHubInstallationRepositories(installation_id, tuple(normalized))
                        )
                return tuple(results)
        except GitHubInstallationSyncError:
            raise
        except (httpx.HTTPError, TimeoutError) as error:
            raise GitHubInstallationSyncError(
                "GitHub repository synchronization is temporarily unavailable.",
                operation="repository_synchronization",
            ) from error
