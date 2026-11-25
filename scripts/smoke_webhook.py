"""Send a correctly signed local GitHub pull-request webhook."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence

DEFAULT_API_URL = "http://localhost:8000"


def build_payload(
    *,
    repo: str,
    pull_request: int,
    head_sha: str,
    base_sha: str,
    installation_id: int,
    repository_id: int,
) -> bytes:
    """Serialize the fields required by the API's pull-request validator."""
    return json.dumps(
        {
            "action": "opened",
            "installation": {"id": installation_id},
            "repository": {"id": repository_id, "full_name": repo},
            "pull_request": {
                "number": pull_request,
                "head": {"sha": head_sha},
                "base": {"sha": base_sha},
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def signature_header(body: bytes, secret: str) -> str:
    """Return GitHub's HMAC-SHA256 header for the exact body bytes."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--repo", required=True, help="GitHub repository in owner/name form")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number")
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--installation-id", required=True, type=int)
    parser.add_argument("--repository-id", required=True, type=int)
    parser.add_argument(
        "--secret",
        default=os.environ.get("DOCDRIFT_GITHUB_WEBHOOK_SECRET"),
        help="Webhook secret; defaults to DOCDRIFT_GITHUB_WEBHOOK_SECRET",
    )
    return parser


def _positive(value: int, field: str, parser: argparse.ArgumentParser) -> None:
    if value < 1:
        parser.error(f"{field} must be a positive integer")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.secret:
        parser.error("--secret or DOCDRIFT_GITHUB_WEBHOOK_SECRET is required")
    if len(args.repo.split("/")) != 2 or any(not part for part in args.repo.split("/")):
        parser.error("--repo must use owner/name form")
    for value, field in (
        (args.pr, "--pr"),
        (args.installation_id, "--installation-id"),
        (args.repository_id, "--repository-id"),
    ):
        _positive(value, field, parser)
    if not args.base_sha or len(args.base_sha) != 40:
        parser.error("--base-sha must be a 40-character commit SHA")
    if not args.head_sha or len(args.head_sha) > 128:
        parser.error("--head-sha must contain 1 to 128 characters")

    body = build_payload(
        repo=args.repo,
        pull_request=args.pr,
        head_sha=args.head_sha,
        base_sha=args.base_sha,
        installation_id=args.installation_id,
        repository_id=args.repository_id,
    )
    url = args.api_url.rstrip("/") + "/webhooks/github"
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature_header(body, args.secret),
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": str(uuid.uuid4()),
        },
        method="POST",
    )

    started = time.perf_counter()
    status = 0
    response_body = ""
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            response_body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        status = error.code
        response_body = error.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as error:
        elapsed_ms = (time.perf_counter() - started) * 1000
        print("status_code=unavailable")
        print(str(error.reason))
        print(f"request failed: {error.reason}", file=sys.stderr)
        print(f"elapsed_ms={elapsed_ms:.1f}")
        return 1
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"status_code={status}")
    print(response_body)
    print(f"elapsed_ms={elapsed_ms:.1f}")
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
