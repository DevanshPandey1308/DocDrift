"""GitHub App authentication and deterministic Check Run publishing."""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape as escape_html
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import jwt

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
CHECK_NAME = "DocDrift"
SUMMARY_MARKER = "<!-- docdrift-summary:v1 -->"
ANNOTATION_BATCH_SIZE = 50
MAX_ANNOTATIONS = 1000
MAX_ANNOTATION_MESSAGE = 10_000
MAX_ANNOTATION_PATH = 1024
MAX_SUMMARY_TEXT = 60_000
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubPublicationError(RuntimeError):
    """Sanitized GitHub publication error with a retry classification."""

    def __init__(
        self, message: str, *, transient: bool = False, retry_after_seconds: int | None = None
    ) -> None:
        super().__init__(message)
        self.transient = transient
        self.retry_after_seconds = retry_after_seconds


class FindingView(Protocol):
    verdict: str
    claim_type: str
    claim_text: str
    explanation: str
    document_path: str
    document_line: int

    @property
    def evidence(self) -> Sequence[object]: ...


SEVERITY_ORDER = {"breaking": 0, "misleading": 1, "cosmetic": 2, "unclassified": 3}
_BREAKING_CLAIMS = {"function_signature", "cli_flag", "environment_variable", "route", "file_path", "python_version"}


def classify_severity(finding: FindingView) -> str | None:
    """Classify only deterministic false claims with a known functional claim type."""
    if (
        finding.verdict != "false"
        or not getattr(finding, "deterministic", True)
        or _confidence_rank(finding) == 0
    ):
        return None
    if finding.claim_type in _BREAKING_CLAIMS:
        return "breaking"
    if finding.claim_type == "default_value":
        return "misleading"
    return None


def _confidence_rank(finding: FindingView) -> int:
    """Use distinct valid evidence records as a disclosed, non-probabilistic confidence rank."""
    if finding.verdict != "false" or not getattr(finding, "deterministic", True):
        return 0
    valid: set[tuple[str, int, str]] = set()
    for source in finding.evidence:
        if not isinstance(source, Mapping):
            continue
        path, line, snippet = source.get("path"), source.get("line"), source.get("snippet")
        if (
            isinstance(path, str)
            and bool(path)
            and "\\" not in path
            and "\x00" not in path
            and ":" not in path
            and isinstance(line, int)
            and not isinstance(line, bool)
            and line > 0
            and isinstance(snippet, str)
            and bool(snippet.strip())
        ):
            source_path = PurePosixPath(path)
            if (
                not source_path.is_absolute()
                and str(source_path) == path
                and not any(part in {".", ".."} for part in source_path.parts)
            ):
                valid.add((path, line, snippet))
    return len(valid)


def _finding_sort_key(finding: FindingView) -> tuple[object, ...]:
    severity = classify_severity(finding) or "unclassified"
    return (
        SEVERITY_ORDER[severity],
        -_confidence_rank(finding),
        finding.document_path,
        finding.document_line,
        finding.claim_text,
    )


def build_pr_summary(findings: Sequence[FindingView]) -> str:
    """Build the editable PR summary body from persisted findings."""
    active = sorted(
        (finding for finding in findings if not getattr(finding, "ignored", False)),
        key=_finding_sort_key,
    )
    ignored = sorted(
        (finding for finding in findings if getattr(finding, "ignored", False)),
        key=_finding_sort_key,
    )
    counts = Counter(classify_severity(finding) for finding in active)
    false_count = sum(finding.verdict == "false" for finding in active)
    lines = [
        SUMMARY_MARKER,
        f"## DocDrift found {false_count} confirmed issue(s) across {len(active)} active finding(s)",
        "",
        (
            f"Severity: {counts['breaking']} breaking, {counts['misleading']} misleading, "
            f"{counts['cosmetic']} cosmetic."
        ),
        "",
        "Findings are ordered by severity, evidence availability, and stable document location.",
    ]
    for finding in active:
        severity = classify_severity(finding)
        label = severity or "unclassified"
        verdict = finding.verdict
        path = _escape_markdown(finding.document_path[:MAX_ANNOTATION_PATH])
        claim = _escape_markdown(" ".join(finding.claim_text.split())[:500])
        explanation = _escape_markdown(" ".join(finding.explanation.split())[:500])
        lines.extend(("", f"- **{label} / {verdict}** `{path}:{finding.document_line}` — {claim}"))
        if explanation:
            lines.append(f"  - {explanation}")
    if ignored:
        lines.extend(("", f"### Ignored findings ({len(ignored)})"))
        for finding in ignored:
            path = _escape_markdown(finding.document_path[:MAX_ANNOTATION_PATH])
            claim = _escape_markdown(" ".join(finding.claim_text.split())[:500])
            reason = _escape_markdown(
                " ".join((getattr(finding, "ignore_reason", None) or "excluded").split())[:500]
            )
            lines.append(f"- `{path}:{finding.document_line}` — {claim} ({reason})")
    body = "\n".join(lines)
    if len(body) > MAX_SUMMARY_TEXT:
        body = body[: MAX_SUMMARY_TEXT - 100] + "\n\nAdditional findings are available in the DocDrift Check Run."
    return body


@dataclass(frozen=True, slots=True)
class CheckRunSummary:
    title: str
    text: str
    conclusion: str
    annotations: tuple[dict[str, Any], ...]
    omitted_annotations: int


def _annotation_for(finding: FindingView) -> dict[str, Any] | None:
    """Return a GitHub-safe annotation at the documentation claim location."""
    if finding.verdict != "false" or getattr(finding, "ignored", False):
        return None

    def valid_path(path: object) -> bool:
        if (
            not isinstance(path, str)
            or not path
            or len(path) > MAX_ANNOTATION_PATH
            or "\\" in path
            or "\x00" in path
            or path.startswith("/")
        ):
            return False
        parsed_path = PurePosixPath(path)
        return (
            bool(parsed_path.parts)
            and str(parsed_path) == path
            and not any(part in (".", "..") for part in parsed_path.parts)
            and ":" not in path
        )

    path = finding.document_path
    if not valid_path(path):
        return None
    line = finding.document_line
    if isinstance(line, bool) or not isinstance(line, int) or not 1 <= line <= 2_147_483_647:
        return None
    source_lines: list[str] = []
    for source in finding.evidence:
        if not isinstance(source, Mapping):
            continue
        source_path = source.get("path")
        source_line = source.get("line")
        snippet = source.get("snippet")
        if (
            not valid_path(source_path)
            or isinstance(source_line, bool)
            or not isinstance(source_line, int)
            or not 1 <= source_line <= 2_147_483_647
            or not isinstance(snippet, str)
            or not snippet
        ):
            continue
        source_lines.append(f"- `{source_path}:{source_line}`: {snippet[:1000]}")
    if not source_lines:
        return None
    message = "\n\n".join(
        (
            _escape_markdown(finding.claim_text),
            _escape_markdown(finding.explanation),
            "Source evidence:\n" + "\n".join(_escape_markdown(item) for item in source_lines),
        )
    )
    if len(message) > MAX_ANNOTATION_MESSAGE:
        message = message[: MAX_ANNOTATION_MESSAGE - 1] + "…"
    title = f"DocDrift: {finding.claim_type}"[:255]
    return {
        "path": path,
        "start_line": line,
        "end_line": line,
        "annotation_level": "failure",
        "title": title,
        "message": message,
    }


def build_check_summary(findings: Sequence[FindingView]) -> CheckRunSummary:
    """Summarize only classifications currently emitted by the deterministic core."""
    active_findings = sorted(
        (finding for finding in findings if not getattr(finding, "ignored", False)),
        key=_finding_sort_key,
    )
    ignored_findings = sorted(
        (finding for finding in findings if getattr(finding, "ignored", False)),
        key=_finding_sort_key,
    )
    counts = Counter(finding.verdict for finding in active_findings)
    false_count = counts["false"]
    unverifiable_count = counts["unverifiable"]
    if false_count:
        conclusion = "failure"
    elif unverifiable_count or ignored_findings:
        conclusion = "neutral"
    else:
        conclusion = "success"

    lines = [
        f"DocDrift analyzed {len(findings)} finding(s) at the pull request head.",
        "",
        f"- Breaking: {sum(classify_severity(item) == 'breaking' for item in active_findings)}",
        f"- Misleading: {sum(classify_severity(item) == 'misleading' for item in active_findings)}",
        f"- Cosmetic: {sum(classify_severity(item) == 'cosmetic' for item in active_findings)}",
        f"- False: {false_count}",
        f"- True: {counts['true']}",
        f"- Unverifiable: {unverifiable_count}",
        f"- Ignored by repository configuration: {len(ignored_findings)}",
    ]
    valid: list[dict[str, Any]] = []
    unannotated: list[FindingView] = []
    for finding in active_findings:
        annotation = _annotation_for(finding)
        if annotation is not None and len(valid) < MAX_ANNOTATIONS:
            valid.append(annotation)
        else:
            unannotated.append(finding)
    omitted = sum(finding.verdict == "false" for finding in unannotated)
    lines.extend(("", f"Annotations: {len(valid)}."))
    if omitted:
        lines.append(
            f"{omitted} finding(s) could not be attached as annotations and remain represented in the counts above."
        )
    if unannotated:
        lines.extend(("", "Findings without annotations:"))
        summary_length = sum(len(line) + 1 for line in lines)
        hidden = 0
        for finding in unannotated:
            claim = _escape_markdown(" ".join(finding.claim_text.split())[:500])
            explanation = _escape_markdown(" ".join(finding.explanation.split())[:500])
            document_path = _escape_markdown(finding.document_path[:MAX_ANNOTATION_PATH])
            entry = (
                f"- {finding.verdict} `{document_path}:{finding.document_line}`: "
                f"{claim} — {explanation}"
            )
            if summary_length + len(entry) + 1 > MAX_SUMMARY_TEXT:
                hidden += 1
                continue
            lines.append(entry)
            summary_length += len(entry) + 1
        if hidden:
            lines.append(
                f"{hidden} additional finding(s) omitted to fit GitHub's summary size limit."
            )
    if ignored_findings:
        lines.extend(("", "Ignored findings (retained for transparency):"))
        summary_length = sum(len(line) + 1 for line in lines)
        hidden_ignored = 0
        for finding in ignored_findings:
            document_path = _escape_markdown(finding.document_path[:MAX_ANNOTATION_PATH])
            claim = _escape_markdown(" ".join(finding.claim_text.split())[:500])
            reason = _escape_markdown(
                " ".join((getattr(finding, "ignore_reason", None) or "excluded").split())[:500]
            )
            entry = f"- `{document_path}:{finding.document_line}`: {claim} — {reason}"
            if summary_length + len(entry) + 1 > MAX_SUMMARY_TEXT:
                hidden_ignored += 1
                continue
            lines.append(entry)
            summary_length += len(entry) + 1
        if hidden_ignored:
            lines.append(
                f"{hidden_ignored} ignored finding(s) omitted to fit GitHub's summary size limit."
            )
    return CheckRunSummary(
        title="DocDrift deterministic analysis",
        text="\n".join(lines),
        conclusion=conclusion,
        annotations=tuple(valid),
        omitted_annotations=omitted,
    )


def _escape_markdown(value: str) -> str:
    escaped = escape_html(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>])", r"\\\1", escaped)


def _validate_repository(repository: str) -> tuple[str, str]:
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("Persisted GitHub repository identity is invalid.")
    owner, name = repository.split("/", 1)
    if owner in (".", "..") or name in (".", ".."):
        raise ValueError("Persisted GitHub repository identity is invalid.")
    return owner, name


def _api_error(response: httpx.Response, operation: str) -> GitHubPublicationError:
    limited = response.status_code == 429 or (
        response.status_code == 403
        and (
            response.headers.get("x-ratelimit-remaining") == "0"
            or response.headers.get("retry-after") is not None
        )
    )
    transient = limited or response.status_code >= 500 or response.status_code == 401
    retry_after: int | None = None
    if limited:
        header = response.headers.get("retry-after")
        if header and header.isdecimal():
            retry_after = max(1, min(int(header), 300))
        reset = response.headers.get("x-ratelimit-reset")
        if retry_after is None and reset and reset.isdecimal():
            retry_after = max(1, min(int(reset) - int(time.time()), 300))
    return GitHubPublicationError(
        f"GitHub {operation} failed with HTTP {response.status_code}.",
        transient=transient,
        retry_after_seconds=retry_after,
    )


class CheckRunPublisher(Protocol):
    async def publish(
        self,
        *,
        repository: str,
        repository_id: int,
        installation_id: int,
        head_sha: str,
        external_id: str,
        findings: Sequence[FindingView],
        check_run_id: int | None = None,
    ) -> int: ...


class GitHubAppCheckRunPublisher:
    """Publish with a fresh, least-privilege installation token for each attempt."""

    def __init__(
        self,
        app_id: str | None,
        private_key: str | None,
        *,
        client_factory: type[httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        if not app_id or not private_key:
            raise GitHubPublicationError(
                "GitHub Check Run publishing requires DOCDRIFT_GITHUB_APP_ID and "
                "DOCDRIFT_GITHUB_APP_PRIVATE_KEY."
            )
        if not app_id.isdecimal():
            raise GitHubPublicationError("DOCDRIFT_GITHUB_APP_ID must be a numeric app ID.")
        self.app_id = app_id
        self.private_key = private_key
        self.client_factory = client_factory

    def _app_jwt(self) -> str:
        now = int(time.time())
        try:
            return jwt.encode(
                {"iat": now - 60, "exp": now + 540, "iss": self.app_id},
                self.private_key,
                algorithm="RS256",
            )
        except (jwt.PyJWTError, ValueError, TypeError) as error:
            raise GitHubPublicationError(
                "DOCDRIFT_GITHUB_APP_PRIVATE_KEY is not a valid RSA private key."
            ) from error

    async def _installation_token(
        self,
        client: httpx.AsyncClient,
        installation_id: int,
        repository_id: int,
        *,
        permissions: Mapping[str, str] | None = None,
    ) -> str:
        if (
            isinstance(installation_id, bool)
            or installation_id < 1
            or isinstance(repository_id, bool)
            or repository_id < 1
        ):
            raise GitHubPublicationError(
                "Valid GitHub installation and repository IDs are required."
            )
        response = await client.post(
            f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {self._app_jwt()}"},
            json={
                "repository_ids": [repository_id],
                "permissions": dict(permissions or {"checks": "write"}),
            },
        )
        if response.status_code != 201:
            raise _api_error(response, "installation token request")
        try:
            token = response.json().get("token")
        except (ValueError, AttributeError) as error:
            raise GitHubPublicationError(
                "GitHub returned an invalid installation token response."
            ) from error
        if not isinstance(token, str) or not token:
            raise GitHubPublicationError("GitHub returned an invalid installation token response.")
        return token

    async def publish(
        self,
        *,
        repository: str,
        repository_id: int,
        installation_id: int,
        head_sha: str,
        external_id: str,
        findings: Sequence[FindingView],
        check_run_id: int | None = None,
    ) -> int:
        try:
            owner, name = _validate_repository(repository)
        except ValueError as error:
            raise GitHubPublicationError(str(error)) from error
        if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
            raise GitHubPublicationError("Persisted pull request head SHA is invalid.")
        if not external_id or len(external_id) > 1000:
            raise GitHubPublicationError("Check Run external ID is invalid.")
        summary = build_check_summary(findings)
        repository_path = f"/repos/{owner}/{name}"
        check_path = f"{repository_path}/check-runs"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "DocDrift",
        }
        async with self.client_factory(
            base_url=GITHUB_API,
            timeout=httpx.Timeout(15.0),
            follow_redirects=False,
            headers=headers,
        ) as client:
            token = await self._installation_token(client, installation_id, repository_id)
            headers["Authorization"] = f"Bearer {token}"
            client.headers.update(headers)

            if check_run_id is None:
                check_run_id = await self._find_existing_check(
                    client, repository_path, head_sha, external_id, int(self.app_id)
                )
            if check_run_id is None:
                response = await client.post(
                    check_path,
                    json={
                        "name": CHECK_NAME,
                        "head_sha": head_sha,
                        "external_id": external_id,
                        "status": "in_progress",
                        "output": {
                            "title": summary.title,
                            "summary": "Publishing DocDrift findings.",
                        },
                    },
                )
                if response.status_code != 201:
                    raise _api_error(response, "Check Run creation")
                try:
                    check_run_id = response.json()["id"]
                except (ValueError, KeyError, TypeError) as error:
                    raise GitHubPublicationError(
                        "GitHub returned an invalid Check Run response."
                    ) from error
                if (
                    isinstance(check_run_id, bool)
                    or not isinstance(check_run_id, int)
                    or check_run_id < 1
                ):
                    raise GitHubPublicationError("GitHub returned an invalid Check Run ID.")
            else:
                response = await client.patch(
                    f"{check_path}/{check_run_id}", json={"status": "in_progress"}
                )
                if response.status_code != 200:
                    raise _api_error(response, "Check Run update")

            existing_annotations = await self._list_annotations(
                client, repository_path, check_run_id
            )
            existing_keys = {self._annotation_key(item) for item in existing_annotations}
            pending_annotations: list[dict[str, Any]] = []
            for annotation in summary.annotations:
                key = self._annotation_key(annotation)
                if key not in existing_keys:
                    pending_annotations.append(annotation)
                    existing_keys.add(key)

            for offset in range(0, len(pending_annotations), ANNOTATION_BATCH_SIZE):
                batch = pending_annotations[offset : offset + ANNOTATION_BATCH_SIZE]
                response = await client.patch(
                    f"{check_path}/{check_run_id}",
                    json={
                        "status": "in_progress",
                        "output": {
                            "title": summary.title,
                            "summary": summary.text,
                            "annotations": list(batch),
                        },
                    },
                )
                if response.status_code != 200:
                    raise _api_error(response, "Check Run annotation update")

            response = await client.patch(
                f"{check_path}/{check_run_id}",
                json={
                    "status": "completed",
                    "conclusion": summary.conclusion,
                    "output": {"title": summary.title, "summary": summary.text},
                },
            )
            if response.status_code != 200:
                raise _api_error(response, "Check Run completion")
        logger.info(
            "github_check_run_published",
            extra={
                "repository": repository,
                "check_run_id": check_run_id,
                "external_id": external_id,
            },
        )
        return check_run_id

    async def _find_existing_check(
        self,
        client: httpx.AsyncClient,
        repository_path: str,
        head_sha: str,
        external_id: str,
        app_id: int,
    ) -> int | None:
        url = f"{GITHUB_API}{repository_path}/commits/{head_sha}/check-runs"
        params: dict[str, str | int] = {
            "check_name": CHECK_NAME,
            "app_id": app_id,
            "filter": "all",
            "per_page": 100,
        }
        for _ in range(10):
            response = await client.get(url, params=params)
            if response.status_code != 200:
                raise _api_error(response, "Check Run lookup")
            try:
                runs = response.json().get("check_runs", [])
            except (ValueError, AttributeError) as error:
                raise GitHubPublicationError(
                    "GitHub returned an invalid Check Run list."
                ) from error
            if not isinstance(runs, list):
                raise GitHubPublicationError("GitHub returned an invalid Check Run list.")
            matches = [
                item
                for item in runs
                if isinstance(item, Mapping)
                and item.get("name") == CHECK_NAME
                and item.get("external_id") == external_id
                and isinstance(item.get("id"), int)
                and not isinstance(item.get("id"), bool)
            ]
            if matches:
                existing_id = max(matches, key=lambda item: item["id"]).get("id")
                if isinstance(existing_id, int) and not isinstance(existing_id, bool):
                    return existing_id
            next_link = response.links.get("next", {}).get("url")
            if not next_link:
                return None
            parsed = urlsplit(next_link)
            if parsed.scheme != "https" or parsed.netloc != "api.github.com":
                raise GitHubPublicationError("GitHub returned an unsafe pagination URL.")
            url = next_link
            params = {}
        return None

    async def _list_annotations(
        self, client: httpx.AsyncClient, repository_path: str, check_run_id: int
    ) -> list[Mapping[str, Any]]:
        """Read existing annotations so a retry appends only missing annotations."""
        url = f"{GITHUB_API}{repository_path}/check-runs/{check_run_id}/annotations"
        params: dict[str, int] = {"per_page": 100}
        annotations: list[Mapping[str, Any]] = []
        for _ in range(10):
            response = await client.get(url, params=params)
            if response.status_code != 200:
                raise _api_error(response, "Check Run annotation lookup")
            try:
                page = response.json()
            except ValueError as error:
                raise GitHubPublicationError(
                    "GitHub returned an invalid Check Run annotation list."
                ) from error
            if not isinstance(page, list) or any(
                not isinstance(annotation, Mapping) for annotation in page
            ):
                raise GitHubPublicationError(
                    "GitHub returned an invalid Check Run annotation list."
                )
            annotations.extend(page)
            next_link = response.links.get("next", {}).get("url")
            if not next_link:
                return annotations
            parsed = urlsplit(next_link)
            if parsed.scheme != "https" or parsed.netloc != "api.github.com":
                raise GitHubPublicationError("GitHub returned an unsafe pagination URL.")
            url = next_link
            params = {}
        return annotations

    async def publish_summary_comment(
        self,
        *,
        repository: str,
        repository_id: int,
        installation_id: int,
        pull_request_number: int,
        findings: Sequence[FindingView],
    ) -> int:
        """Create or edit the stable DocDrift comment scoped to one repository and PR."""
        try:
            owner, name = _validate_repository(repository)
        except ValueError as error:
            raise GitHubPublicationError(str(error)) from error
        if (
            isinstance(pull_request_number, bool)
            or not isinstance(pull_request_number, int)
            or pull_request_number < 1
        ):
            raise GitHubPublicationError("Persisted pull request number is invalid.")

        repository_path = f"/repos/{owner}/{name}"
        comments_path = f"{repository_path}/issues/{pull_request_number}/comments"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "DocDrift",
        }
        body = build_pr_summary(findings)
        async with self.client_factory(
            base_url=GITHUB_API,
            timeout=httpx.Timeout(15.0),
            follow_redirects=False,
            headers=headers,
        ) as client:
            token = await self._installation_token(
                client,
                installation_id,
                repository_id,
                permissions={"pull_requests": "write"},
            )
            client.headers.update({**headers, "Authorization": f"Bearer {token}"})
            existing_id = await self._find_summary_comment(client, comments_path)
            if existing_id is None:
                response = await client.post(comments_path, json={"body": body})
                expected_status = 201
                operation = "PR summary comment creation"
            else:
                response = await client.patch(
                    f"{repository_path}/issues/comments/{existing_id}", json={"body": body}
                )
                expected_status = 200
                operation = "PR summary comment update"
            if response.status_code != expected_status:
                raise _api_error(response, operation)
            try:
                comment_id = response.json().get("id")
            except (ValueError, AttributeError) as error:
                raise GitHubPublicationError("GitHub returned an invalid PR comment response.") from error
            if isinstance(comment_id, bool) or not isinstance(comment_id, int) or comment_id < 1:
                raise GitHubPublicationError("GitHub returned an invalid PR comment ID.")

        logger.info(
            "github_pr_summary_published",
            extra={"repository": repository, "pull_request_number": pull_request_number},
        )
        return comment_id

    async def _find_summary_comment(
        self, client: httpx.AsyncClient, comments_path: str
    ) -> int | None:
        """Find our marker only in comments for this exact repository/PR endpoint."""
        url = f"{GITHUB_API}{comments_path}"
        params: dict[str, int] = {"per_page": 100}
        matching_ids: list[int] = []
        for _ in range(10):
            response = await client.get(url, params=params)
            if response.status_code != 200:
                raise _api_error(response, "PR summary comment lookup")
            try:
                comments = response.json()
            except ValueError as error:
                raise GitHubPublicationError("GitHub returned an invalid PR comment list.") from error
            if not isinstance(comments, list):
                raise GitHubPublicationError("GitHub returned an invalid PR comment list.")
            for item in comments:
                if not isinstance(item, Mapping):
                    continue
                user = item.get("user")
                comment_id = item.get("id")
                if (
                    isinstance(item.get("body"), str)
                    and SUMMARY_MARKER in item["body"]
                    and isinstance(user, Mapping)
                    and user.get("type") == "Bot"
                    and isinstance(user.get("login"), str)
                    and user["login"].endswith("[bot]")
                    and isinstance(comment_id, int)
                    and not isinstance(comment_id, bool)
                    and comment_id > 0
                ):
                    matching_ids.append(comment_id)
            next_link = response.links.get("next", {}).get("url")
            if not next_link:
                return max(matching_ids) if matching_ids else None
            parsed = urlsplit(next_link)
            if parsed.scheme != "https" or parsed.netloc != "api.github.com":
                raise GitHubPublicationError("GitHub returned an unsafe pagination URL.")
            url = next_link
            params = {}
        return max(matching_ids) if matching_ids else None

    @staticmethod
    def _annotation_key(annotation: Mapping[str, Any]) -> tuple[object, ...]:
        return tuple(
            annotation.get(key)
            for key in (
                "path",
                "start_line",
                "end_line",
                "annotation_level",
                "title",
                "message",
            )
        )
