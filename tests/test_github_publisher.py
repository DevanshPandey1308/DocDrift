from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from docdrift_worker.github import (
    ANNOTATION_BATCH_SIZE,
    SUMMARY_MARKER,
    GitHubAppCheckRunPublisher,
    GitHubPublicationError,
    _eligible_inline_findings,
    build_check_summary,
    build_pr_summary,
    classify_severity,
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
    claim_id: str = "0123456789abcdef"
    deterministic: bool = True
    inline_comment_line: int | None = 8


class FakeGitHubAPI:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.checks: dict[str, dict[str, Any]] = {}
        self.next_id = 900
        self.fail_check_creation = False
        self.comments: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self.next_comment_id = 400
        self.review_comments: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self.pull_revisions: dict[int, dict[str, Any]] = {}
        self.default_head_sha = "a" * 40
        self.default_base_sha = "b" * 40
        self.reject_inline_positions = False
        self.fail_inline_permission = False
        self.advance_head_after_inline_comment = False

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-token"}, request=request)
        if "/pulls/" in request.url.path and request.url.path.count("/") >= 5:
            parts = request.url.path.split("/")
            if parts[-2].isdecimal() and parts[-1] == "comments":
                key = ("/".join(parts[2:4]), int(parts[-2]))
                if request.method == "GET":
                    return httpx.Response(
                        200, json=self.review_comments.get(key, []), request=request
                    )
                if request.method == "POST":
                    if self.fail_inline_permission:
                        return httpx.Response(403, request=request)
                    if self.reject_inline_positions:
                        return httpx.Response(422, request=request)
                    body = json.loads(request.content)
                    self.next_comment_id += 1
                    comment = {
                        "id": self.next_comment_id,
                        "body": body["body"],
                        "path": body["path"],
                        "line": body["line"],
                        "side": body["side"],
                        "user": {"type": "Bot", "login": "docdrift[bot]"},
                    }
                    self.review_comments.setdefault(key, []).append(comment)
                    if self.advance_head_after_inline_comment:
                        self.pull_revisions[key[1]] = {
                            "head": {"sha": "c" * 40},
                            "base": {"sha": self.default_base_sha},
                            "state": "open",
                        }
                    return httpx.Response(201, json=comment, request=request)
            if parts[-2] == "pulls" and parts[-1].isdecimal() and request.method == "GET":
                number = int(parts[-1])
                return httpx.Response(
                    200,
                    json=self.pull_revisions.get(
                        number,
                        {
                            "head": {"sha": self.default_head_sha},
                            "base": {"sha": self.default_base_sha},
                            "state": "open",
                        },
                    ),
                    request=request,
                )
        if "/pulls/comments/" in request.url.path and request.method == "PATCH":
            comment_id = int(request.url.path.rsplit("/", 1)[-1])
            comment = next(
                item
                for comments in self.review_comments.values()
                for item in comments
                if item["id"] == comment_id
            )
            comment["body"] = json.loads(request.content)["body"]
            return httpx.Response(200, json=comment, request=request)
        if request.method == "GET" and request.url.path.endswith("/check-runs"):
            head_sha = request.url.path.split("/commits/", 1)[1].split("/check-runs", 1)[0]
            check = self.checks.get(head_sha)
            runs = [check] if check else []
            return httpx.Response(200, json={"check_runs": runs}, request=request)
        if "/issues/" in request.url.path and request.url.path.endswith("/comments"):
            parts = request.url.path.split("/")
            key = ("/".join(parts[2:4]), int(parts[5]))
            if request.method == "GET":
                return httpx.Response(200, json=self.comments.get(key, []), request=request)
            if request.method == "POST":
                body = json.loads(request.content)
                self.next_comment_id += 1
                comment = {
                    "id": self.next_comment_id,
                    "body": body["body"],
                    "user": {"type": "Bot", "login": "docdrift[bot]"},
                }
                self.comments.setdefault(key, []).append(comment)
                return httpx.Response(201, json=comment, request=request)
        if "/issues/comments/" in request.url.path and request.method == "PATCH":
            parts = request.url.path.split("/")
            key = ("/".join(parts[2:4]), 12)
            comment_id = int(parts[-1])
            comment = next(
                item for item in self.comments.get(key, []) if item["id"] == comment_id
            )
            comment["body"] = json.loads(request.content)["body"]
            return httpx.Response(200, json=comment, request=request)
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

    def test_inline_selection_requires_an_exact_safe_changed_line_and_evidence(self) -> None:
        findings = [
            FakeFinding(inline_comment_line=None),
            FakeFinding(inline_comment_line=7),
            FakeFinding(document_path="../guide.md"),
            FakeFinding(evidence=()),
            FakeFinding(verdict="unverifiable"),
        ]

        selected = _eligible_inline_findings(
            findings, inline_min_severity="breaking", max_inline_comments=5
        )

        self.assertEqual(selected, [])

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

    async def test_summary_comment_is_created_then_updated_with_marker_and_pr_scope(self) -> None:
        api = FakeGitHubAPI()
        api.comments[("owner/project", 12)] = [{"id": 77, "body": "A generic comment"}]
        api.comments[("owner/project", 99)] = [
            {
                "id": 88,
                "body": SUMMARY_MARKER + " another PR",
                "user": {"type": "Bot", "login": "docdrift[bot]"},
            }
        ]
        publisher = make_publisher(api)
        arguments = {
            "repository": "owner/project",
            "repository_id": 58,
            "installation_id": 73,
            "pull_request_number": 12,
            "findings": [FakeFinding()],
        }
        first_id = await publisher.publish_summary_comment(**arguments)
        arguments["findings"] = [FakeFinding(claim_text="The default timeout is 60.")]
        second_id = await publisher.publish_summary_comment(**arguments)

        self.assertEqual(first_id, second_id)
        self.assertEqual(len(api.comments[("owner/project", 12)]), 2)
        self.assertEqual(api.comments[("owner/project", 99)][0]["body"], SUMMARY_MARKER + " another PR")
        comment = next(
            item for item in api.comments[("owner/project", 12)] if SUMMARY_MARKER in item["body"]
        )
        self.assertIn(SUMMARY_MARKER, comment["body"])
        self.assertIn("misleading / false", comment["body"])
        comment_requests = [
            request
            for request in api.requests
            if "/issues/12/comments" in request.url.path or "/issues/comments/" in request.url.path
        ]
        self.assertEqual(
            [request.method for request in comment_requests], ["GET", "POST", "GET", "PATCH"]
        )
        token_bodies = [
            json.loads(request.content)
            for request in api.requests
            if request.url.path.endswith("/access_tokens")
        ]
        self.assertEqual(token_bodies[-1]["permissions"], {"pull_requests": "write"})

    async def test_inline_comments_use_diff_line_api_and_retry_updates_stable_comment(self) -> None:
        api = FakeGitHubAPI()
        publisher = make_publisher(api)
        arguments = {
            "repository": "owner/project",
            "repository_id": 58,
            "installation_id": 73,
            "pull_request_number": 12,
            "base_sha": "b" * 40,
            "head_sha": "a" * 40,
            "external_id": "docdrift:run:4:head:" + "a" * 40,
            "findings": [FakeFinding(claim_type="cli_flag")],
            "inline_min_severity": "breaking",
            "max_inline_comments": 5,
        }

        first = await publisher.publish_inline_comments(**arguments)
        second = await publisher.publish_inline_comments(**arguments)

        self.assertEqual((first, second), (1, 1))
        comments = api.review_comments[("owner/project", 12)]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["path"], "docs/guide.md")
        self.assertEqual(comments[0]["line"], 8)
        self.assertEqual(comments[0]["side"], "RIGHT")
        self.assertIn("deterministic documentation mismatch", comments[0]["body"])
        self.assertIn("src/settings", comments[0]["body"])
        self.assertRegex(comments[0]["body"], r"<!-- docdrift-inline:v1:[0-9a-f]{32} -->")
        self.assertEqual(
            len(
                [
                    request
                    for request in api.requests
                    if request.method == "POST" and request.url.path.endswith("/pulls/12/comments")
                ]
            ),
            1,
        )
        self.assertEqual(
            len(
                [
                    request
                    for request in api.requests
                    if request.method == "PATCH" and "/pulls/comments/" in request.url.path
                ]
            ),
            1,
        )
        permissions = [
            json.loads(request.content)["permissions"]
            for request in api.requests
            if request.url.path.endswith("/access_tokens")
        ]
        self.assertEqual(permissions, [{"pull_requests": "write"}, {"pull_requests": "write"}])

    async def test_stale_head_and_base_suppress_inline_comments(self) -> None:
        api = FakeGitHubAPI()
        api.pull_revisions[12] = {
            "head": {"sha": "c" * 40},
            "base": {"sha": "b" * 40},
            "state": "open",
        }
        publisher = make_publisher(api)
        count = await publisher.publish_inline_comments(
            repository="owner/project",
            repository_id=58,
            installation_id=73,
            pull_request_number=12,
            base_sha="b" * 40,
            head_sha="a" * 40,
            external_id="docdrift:run:4:head:" + "a" * 40,
            findings=[FakeFinding(claim_type="cli_flag")],
            inline_min_severity="breaking",
            max_inline_comments=5,
        )

        self.assertEqual(count, 0)
        self.assertFalse(any(request.method == "POST" and request.url.path.endswith("/comments") for request in api.requests))

    async def test_head_update_during_batch_stops_further_inline_comments(self) -> None:
        api = FakeGitHubAPI()
        api.advance_head_after_inline_comment = True
        publisher = make_publisher(api)
        count = await publisher.publish_inline_comments(
            repository="owner/project",
            repository_id=58,
            installation_id=73,
            pull_request_number=12,
            base_sha="b" * 40,
            head_sha="a" * 40,
            external_id="docdrift:run:4:head:" + "a" * 40,
            findings=[
                FakeFinding(claim_type="cli_flag", claim_id="1" * 16),
                FakeFinding(claim_type="cli_flag", claim_id="2" * 16, document_line=9, inline_comment_line=9),
            ],
            inline_min_severity="breaking",
            max_inline_comments=5,
        )

        self.assertEqual(count, 1)
        self.assertEqual(len(api.review_comments[("owner/project", 12)]), 1)

    async def test_severity_budget_and_ignored_findings_filter_inline_publication(self) -> None:
        api = FakeGitHubAPI()
        publisher = make_publisher(api)
        findings = [
            FakeFinding(claim_type="cli_flag", claim_id="1" * 16, claim_text="Removed --old flag."),
            FakeFinding(claim_type="default_value", claim_id="2" * 16, claim_text="Default changed."),
            FakeFinding(claim_type="cli_flag", claim_id="3" * 16, ignored=True, claim_text="Ignored flag."),
            FakeFinding(claim_type="cli_flag", claim_id="4" * 16, verdict="unverifiable", claim_text="Unclear flag."),
        ]
        common = {
            "repository": "owner/project",
            "repository_id": 58,
            "installation_id": 73,
            "pull_request_number": 12,
            "base_sha": "b" * 40,
            "head_sha": "a" * 40,
            "external_id": "docdrift:run:4:head:" + "a" * 40,
            "findings": findings,
            "inline_min_severity": "breaking",
        }

        count = await publisher.publish_inline_comments(**common, max_inline_comments=1)
        no_budget = await publisher.publish_inline_comments(**common, max_inline_comments=0)

        self.assertEqual(count, 1)
        self.assertEqual(no_budget, 0)
        self.assertEqual(len(api.review_comments[("owner/project", 12)]), 1)
        body = api.review_comments[("owner/project", 12)][0]["body"]
        self.assertIn("old flag", body)
        self.assertNotIn("Default changed", body)
        self.assertNotIn("Ignored flag", body)
        self.assertNotIn("Unclear flag", body)

    async def test_rejected_position_keeps_finding_in_check_run_and_summary_fallbacks(self) -> None:
        api = FakeGitHubAPI()
        api.reject_inline_positions = True
        publisher = make_publisher(api)
        finding = FakeFinding(claim_type="cli_flag")
        count = await publisher.publish_inline_comments(
            repository="owner/project",
            repository_id=58,
            installation_id=73,
            pull_request_number=12,
            base_sha="b" * 40,
            head_sha="a" * 40,
            external_id="docdrift:run:4:head:" + "a" * 40,
            findings=[finding],
            inline_min_severity="breaking",
            max_inline_comments=5,
        )

        self.assertEqual(count, 0)
        self.assertEqual(build_check_summary([finding]).conclusion, "failure")
        self.assertIn("The default timeout", build_pr_summary([finding]))

    async def test_inline_permission_failure_is_sanitized(self) -> None:
        api = FakeGitHubAPI()
        api.fail_inline_permission = True
        publisher = make_publisher(api)
        with self.assertRaises(GitHubPublicationError) as raised:
            await publisher.publish_inline_comments(
                repository="owner/project",
                repository_id=58,
                installation_id=73,
                pull_request_number=12,
                base_sha="b" * 40,
                head_sha="a" * 40,
                external_id="docdrift:run:4:head:" + "a" * 40,
                findings=[FakeFinding(claim_type="cli_flag")],
                inline_min_severity="breaking",
                max_inline_comments=5,
            )
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertNotIn("installation-token", str(raised.exception))

    def test_severity_and_summary_order_are_deterministic_and_ignored_findings_stay_inactive(self) -> None:
        breaking = FakeFinding(
            claim_type="cli_flag",
            claim_text="A flag is removed.",
            document_path="z.md",
            evidence=(
                {"path": "src/cli.py", "line": 4, "snippet": "removed option"},
                {"path": "src/parser.py", "line": 9, "snippet": "no longer registered"},
            ),
        )
        breaking_low_confidence = FakeFinding(
            claim_type="cli_flag", claim_text="Another flag is removed.", document_path="a.md"
        )
        misleading = FakeFinding(claim_type="default_value", claim_text="A default changed.")
        unverifiable = FakeFinding(verdict="unverifiable", claim_type="cli_flag")
        ignored = FakeFinding(ignored=True, claim_type="cli_flag", claim_text="Ignored.")
        self.assertEqual(classify_severity(breaking), "breaking")
        self.assertEqual(classify_severity(misleading), "misleading")
        self.assertIsNone(classify_severity(unverifiable))
        body = build_pr_summary([misleading, breaking_low_confidence, unverifiable, ignored, breaking])
        self.assertLess(body.index("breaking / false"), body.index("misleading / false"))
        self.assertLess(body.index("A flag is removed"), body.index("Another flag is removed"))
        self.assertIn("unclassified / unverifiable", body)
        self.assertIn("### Ignored findings (1)", body)

    def test_summary_keeps_findings_beyond_inline_budget_and_never_invents_suggestions(self) -> None:
        findings = [
            FakeFinding(claim_type="cli_flag", claim_text=f"Removed flag {index}.")
            for index in range(7)
        ]
        body = build_pr_summary(findings)
        for index in range(len(findings)):
            self.assertIn(f"Removed flag {index}", body)
        self.assertNotIn("suggestion", body.lower())

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
