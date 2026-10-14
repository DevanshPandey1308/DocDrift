from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from docdrift_worker.github import (
    ANNOTATION_BATCH_SIZE,
    GitHubAppCheckRunPublisher,
    GitHubPublicationError,
    build_check_summary,
)


@dataclass
class FakeFinding:
    verdict: str = "false"
    claim_type: str = "default_value"
    claim_text: str = "The default timeout is 30."
    explanation: str = "Source says 60."
    document_path: str = "docs/guide.md"
    document_line: int = 8
    evidence: tuple[dict[str, object], ...] = (
        {"path": "src/settings.py", "line": 12, "snippet": "timeout = 60"},
    )
    ignored: bool = False
    ignore_reason: str | None = None


class FakeGitHubAPI:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.checks: dict[str, dict[str, Any]] = {}
        self.next_id = 900
        self.fail_check_creation = False

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"}, request=request)
        if request.method == "GET" and request.url.path.endswith("/check-runs"):
            head_sha = request.url.path.split("/commits/", 1)[1].split("/check-runs", 1)[0]
            check = self.checks.get(head_sha)
            runs = [check] if check else []
            return httpx.Response(200, json={"check_runs": runs}, request=request)
        if request.method == "GET" and request.url.path.endswith("/annotations"):
            check_id = int(request.url.path.split("/check-runs/", 1)[1].split("/", 1)[0])
            check = next((item for item in self.checks.values() if item["id"] == check_id), None)
            return httpx.Response(200, json=check["annotations"] if check else [], request=request)
        if request.method == "POST" and request.url.path.endswith("/check-runs"):
            if self.fail_check_creation:
                return httpx.Response(503, request=request)
            body = json.loads(request.content)
            self.next_id += 1
            check = {
                "id": self.next_id,
                "name": body["name"],
                "external_id": body["external_id"],
                "annotations": [],
            }
            self.checks[body["head_sha"]] = check
            return httpx.Response(201, json=check, request=request)
        if request.method == "PATCH":
            check_id = int(request.url.path.rsplit("/", 1)[-1])
            body = json.loads(request.content)
            check = next((item for item in self.checks.values() if item["id"] == check_id), None)
            if check is not None:
                check["annotations"].extend(body.get("output", {}).get("annotations", []))
            return httpx.Response(200, json={"id": check_id}, request=request)
        return httpx.Response(404, request=request)


def make_publisher(api: FakeGitHubAPI) -> GitHubAppCheckRunPublisher:
    transport = httpx.MockTransport(api)
    publisher = GitHubAppCheckRunPublisher(
        "1234",
        "not-a-real-key",
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
    )
    publisher._app_jwt = lambda: "test-app-jwt"  # type: ignore[method-assign]
    return publisher


class CheckRunSummaryTests(unittest.TestCase):
    def test_clean_analysis_has_success_conclusion(self) -> None:
        summary = build_check_summary([])
        self.assertEqual(summary.conclusion, "success")
        self.assertIn("0 finding(s)", summary.text)
        self.assertEqual(build_check_summary([FakeFinding(verdict="true")]).conclusion, "success")

    def test_findings_use_failure_or_neutral_and_safe_annotations(self) -> None:
        false = FakeFinding()
        true = FakeFinding(verdict="true")
        unverifiable = FakeFinding(verdict="unverifiable")
        invalid = FakeFinding(document_path="../secrets.txt")
        invalid_line = FakeFinding(document_line=0)
        summary = build_check_summary([false, true, unverifiable, invalid, invalid_line])

        self.assertEqual(summary.conclusion, "failure")
        self.assertIn("False: 3", summary.text)
        self.assertIn("True: 1", summary.text)
        self.assertIn("Unverifiable: 1", summary.text)
        self.assertEqual(len(summary.annotations), 1)
        self.assertEqual(summary.annotations[0]["path"], "docs/guide.md")
        self.assertEqual(summary.annotations[0]["start_line"], 8)
        self.assertEqual(summary.omitted_annotations, 2)

    def test_missing_source_evidence_is_not_annotated(self) -> None:
        summary = build_check_summary([FakeFinding(evidence=())])
        self.assertEqual(summary.conclusion, "failure")
        self.assertEqual(summary.annotations, ())
        self.assertEqual(summary.omitted_annotations, 1)

    def test_only_unverifiable_findings_are_neutral(self) -> None:
        self.assertEqual(
            build_check_summary([FakeFinding(verdict="unverifiable")]).conclusion, "neutral"
        )

    def test_ignored_findings_are_transparent_and_do_not_hide_active_failures(self) -> None:
        ignored = FakeFinding(ignored=True, ignore_reason="Known generated page")
        ignored_only = build_check_summary([ignored])
        self.assertEqual(ignored_only.conclusion, "neutral")
        self.assertIn("Ignored by repository configuration: 1", ignored_only.text)
        self.assertIn("Known generated page", ignored_only.text)
        self.assertEqual(ignored_only.annotations, ())

        mixed = build_check_summary([ignored, FakeFinding()])
        self.assertEqual(mixed.conclusion, "failure")
        self.assertEqual(len(mixed.annotations), 1)


class CheckRunPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_publishes_exact_head_sha_with_correct_annotation_and_permission(self) -> None:
        api = FakeGitHubAPI()
        publisher = make_publisher(api)
        head_sha = "a" * 40
        check_id = await publisher.publish(
            repository="owner/project",
            repository_id=58,
            installation_id=73,
            head_sha=head_sha,
            external_id=f"docdrift:run:4:head:{head_sha}",
            findings=[FakeFinding()],
        )

        self.assertEqual(check_id, 901)
        token_request = next(
            request for request in api.requests if request.url.path.endswith("/access_tokens")
        )
        self.assertEqual(
            json.loads(token_request.content),
            {"repository_ids": [58], "permissions": {"checks": "write"}},
        )
        create = next(
            request
            for request in api.requests
            if request.method == "POST" and request.url.path.endswith("/check-runs")
        )
        create_body = json.loads(create.content)
        self.assertEqual(create_body["head_sha"], head_sha)
        self.assertEqual(create_body["external_id"], f"docdrift:run:4:head:{head_sha}")
        updates = [request for request in api.requests if request.method == "PATCH"]
        self.assertTrue(any(request.url.path.endswith("/901") for request in updates))
        annotation_request = next(
            request for request in updates if b"annotations" in request.content
        )
        annotation = json.loads(annotation_request.content)["output"]["annotations"][0]
        self.assertEqual(annotation["path"], "docs/guide.md")
        self.assertEqual(annotation["start_line"], 8)
        self.assertIn("src/settings", annotation["message"])
        self.assertIn(":12", annotation["message"])
        final = json.loads(updates[-1].content)
        self.assertEqual(final["conclusion"], "failure")

    async def test_repeated_publication_reuses_check_by_external_id(self) -> None:
        api = FakeGitHubAPI()
        publisher = make_publisher(api)
        arguments = {
            "repository": "owner/project",
            "repository_id": 58,
            "installation_id": 73,
            "head_sha": "b" * 40,
            "external_id": "docdrift:run:5:head:" + "b" * 40,
            "findings": [FakeFinding()],
        }
        first_id = await publisher.publish(**arguments)
        second_id = await publisher.publish(**arguments)

        self.assertEqual(first_id, second_id)
        self.assertEqual(
            len(
                [
                    request
                    for request in api.requests
                    if request.method == "POST" and request.url.path.endswith("/check-runs")
                ]
            ),
            1,
        )
        self.assertGreaterEqual(
            len([request for request in api.requests if request.method == "PATCH"]), 2
        )
        stored = api.checks[arguments["head_sha"]]
        self.assertEqual(len(stored["annotations"]), 1)

    async def test_annotation_batches_respect_github_limit(self) -> None:
        api = FakeGitHubAPI()
        publisher = make_publisher(api)
        await publisher.publish(
            repository="owner/project",
            repository_id=58,
            installation_id=73,
            head_sha="c" * 40,
            external_id="docdrift:run:6:head:" + "c" * 40,
            findings=[
                FakeFinding(document_line=index + 1) for index in range(ANNOTATION_BATCH_SIZE + 3)
            ],
        )
        annotation_updates = [
            json.loads(request.content)["output"]["annotations"]
            for request in api.requests
            if request.method == "PATCH" and b"annotations" in request.content
        ]
        self.assertEqual([len(batch) for batch in annotation_updates], [50, 3])

    async def test_api_failure_is_sanitized_and_classified(self) -> None:
        api = FakeGitHubAPI()
        api.fail_check_creation = True
        publisher = make_publisher(api)
        with self.assertRaises(GitHubPublicationError) as raised:
            await publisher.publish(
                repository="owner/project",
                repository_id=58,
                installation_id=73,
                head_sha="d" * 40,
                external_id="docdrift:run:7:head:" + "d" * 40,
                findings=[FakeFinding()],
            )
        self.assertTrue(raised.exception.transient)
        self.assertNotIn("installation-token", str(raised.exception))

    async def test_rate_limit_respects_bounded_retry_after(self) -> None:
        api = FakeGitHubAPI()

        async def rate_limited(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/access_tokens"):
                return httpx.Response(201, json={"token": "installation-token"}, request=request)
            if request.method == "GET":
                return httpx.Response(429, headers={"Retry-After": "42"}, request=request)
            return await api(request)

        publisher = make_publisher(api)
        publisher.client_factory = lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(rate_limited), **kwargs
        )
        with self.assertRaises(GitHubPublicationError) as raised:
            await publisher.publish(
                repository="owner/project",
                repository_id=58,
                installation_id=73,
                head_sha="e" * 40,
                external_id="docdrift:run:8:head:" + "e" * 40,
                findings=[],
            )
        self.assertEqual(raised.exception.retry_after_seconds, 42)

    async def test_missing_app_configuration_fails_without_network(self) -> None:
        with self.assertRaisesRegex(GitHubPublicationError, "DOCDRIFT_GITHUB_APP_ID"):
            GitHubAppCheckRunPublisher(None, None)

    async def test_app_jwt_is_signed_and_short_lived(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        publisher = GitHubAppCheckRunPublisher("456", private_pem)
        public_pem = private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        claims = jwt.decode(publisher._app_jwt(), public_pem, algorithms=["RS256"])
        self.assertEqual(claims["iss"], "456")
        self.assertGreaterEqual(claims["exp"] - claims["iat"], 600)
        self.assertLessEqual(claims["exp"] - claims["iat"], 630)


if __name__ == "__main__":
    unittest.main()
