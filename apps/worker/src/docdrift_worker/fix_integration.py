"""In-memory orchestration for optional, verified documentation-fix proposals."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from docdrift_core.fix_verification import reverify_candidate_patch
from docdrift_core.index import CodeIndex, build_index
from docdrift_core.models import Evidence, Finding, Verdict
from docdrift_core.orchestration import GroundedVerificationClient
from docdrift_core.patches import PatchValidationError, create_claim_patch

from .fix_delivery import (
    FixDeliveryDecision,
    FixDeliveryMode,
    FixDeliveryStatus,
    plan_verified_fix_delivery,
)
from .github import is_valid_inline_location
from .noise import FindingExclusions

MAX_FIX_EVIDENCE_ITEMS = 8
MAX_FIX_EVIDENCE_CHARS = 8_000
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
logger = logging.getLogger(__name__)


class FixCandidateClient(Protocol):
    """Optional provider boundary that proposes replacement text for one claim span."""

    def propose_replacement(
        self, finding: Finding, evidence: tuple[Evidence, ...]
    ) -> Awaitable[str | FixCandidateResponse | None]: ...


@dataclass(frozen=True, slots=True)
class FixCandidateResponse:
    """A structured replacement plus actual provider-reported token usage."""

    replacement: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.output_tokens):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError("provider token usage must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class VerifiedFixSuggestion:
    """A planner-approved suggestion coupled to its original persisted finding."""

    decision: FixDeliveryDecision
    claim_id: str
    finding: Finding
    replacement_line: str


def _safe_document_path(root: Path, path: str) -> Path | None:
    if not is_valid_inline_location(path, 1, 1):
        return None
    try:
        resolved_root = root.resolve()
        candidate = (resolved_root / PurePosixPath(path)).resolve()
        if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
            return None
        return candidate
    except (OSError, RuntimeError, ValueError):
        return None


def _trusted_evidence(index: CodeIndex, evidence: Sequence[Evidence]) -> tuple[Evidence, ...]:
    """Return bounded source snippets that exactly match the current checkout."""
    result: list[Evidence] = []
    total = 0
    try:
        root = index.root.resolve()
        for item in evidence:
            if (
                not isinstance(item, Evidence)
                or not isinstance(item.snippet, str)
                or not item.snippet.strip()
            ):
                continue
            source_path = (root / PurePosixPath(item.path)).resolve()
            if not source_path.is_relative_to(root) or index.evidence(item.path, item.line) != item:
                continue
            if total + len(item.snippet) > MAX_FIX_EVIDENCE_CHARS:
                continue
            result.append(item)
            total += len(item.snippet)
            if len(result) == MAX_FIX_EVIDENCE_ITEMS:
                break
    except (OSError, RuntimeError, TypeError, ValueError):
        return ()
    return tuple(result)


def _line_at(document: str, line_number: int) -> str | None:
    lines = document.splitlines()
    if not 1 <= line_number <= len(lines):
        return None
    return lines[line_number - 1]


async def prepare_verified_fix_suggestions(
    findings: Sequence[Finding],
    repository_root: Path,
    *,
    candidate_client: FixCandidateClient | None,
    added_markdown_lines: dict[str, frozenset[int]] | None,
    analyzed_head_sha: str,
    current_head_sha: str,
    exclusions: FindingExclusions | None = None,
    verification_client: GroundedVerificationClient | None = None,
    code_index: CodeIndex | None = None,
) -> tuple[VerifiedFixSuggestion, ...]:
    """Generate, reverify, plan, and return only current in-diff suggestions.

    An absent client preserves deterministic-only operation. Candidate and provider
    failures fail closed. No documents are written and no GitHub calls occur here.
    """
    if (
        candidate_client is None
        or added_markdown_lines is None
        or not isinstance(analyzed_head_sha, str)
        or not _COMMIT_SHA.fullmatch(analyzed_head_sha)
        or not isinstance(current_head_sha, str)
        or not _COMMIT_SHA.fullmatch(current_head_sha)
        or analyzed_head_sha.lower() != current_head_sha.lower()
    ):
        return ()
    try:
        root = repository_root.resolve()
        index = code_index or build_index(root)
        if index.root.resolve() != root or index.errors:
            return ()
    except (OSError, RuntimeError, ValueError):
        return ()

    excluded = exclusions or FindingExclusions()
    suggestions: list[VerifiedFixSuggestion] = []
    for finding in findings:
        claim = finding.claim
        if (
            finding.verdict is not Verdict.FALSE
            or not finding.deterministic
            or excluded.reason_for(claim.id, claim.document_path) is not None
            or not finding.evidence
            or not is_valid_inline_location(claim.document_path, claim.line, claim.line)
            or claim.line not in added_markdown_lines.get(
                claim.document_path, frozenset()
            )
        ):
            continue
        trusted = _trusted_evidence(index, finding.evidence)
        if not trusted:
            continue
        document_path = _safe_document_path(root, claim.document_path)
        if document_path is None:
            continue
        try:
            original = document_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue

        try:
            proposal = await candidate_client.propose_replacement(finding, trusted)
        except Exception as error:  # noqa: BLE001 - provider errors must not affect deterministic reporting.
            logger.warning(
                "fix_candidate_provider_failed",
                extra={"claim_id": claim.id, "error_type": type(error).__name__},
            )
            continue
        replacement = proposal.replacement if isinstance(proposal, FixCandidateResponse) else proposal
        if not isinstance(replacement, str):
            continue
        expected = claim.text[claim.span_start : claim.span_end]
        try:
            patch = create_claim_patch(
                original,
                claim,
                expected_original=expected,
                replacement_text=replacement,
            )
        except (TypeError, ValueError, PatchValidationError):
            continue

        try:
            verification = await reverify_candidate_patch(
                original,
                patch,
                claim,
                index,
                evidence=trusted,
                client=verification_client,
            )
        except Exception as error:  # noqa: BLE001 - fix verification cannot fail analysis.
            logger.warning(
                "fix_candidate_reverification_failed",
                extra={"claim_id": claim.id, "error_type": type(error).__name__},
            )
            continue
        try:
            decision = plan_verified_fix_delivery(
                verification,
                patch,
                claim,
                analyzed_head_sha=analyzed_head_sha,
                current_head_sha=current_head_sha,
                added_markdown_lines=added_markdown_lines,
            )
        except Exception as error:  # noqa: BLE001 - planner errors fail closed to annotation.
            logger.warning(
                "fix_candidate_delivery_planning_failed",
                extra={"claim_id": claim.id, "error_type": type(error).__name__},
            )
            continue
        if (
            decision.status is not FixDeliveryStatus.READY
            or decision.mode is not FixDeliveryMode.INLINE_SUGGESTION
            or not decision.patch_delivery_eligible
            or decision.patch != patch
            or decision.document_path != claim.document_path
            or decision.document_line != claim.line
        ):
            continue
        replacement_line = _line_at(patch.proposed_text, claim.line)
        if (
            replacement_line is None
            or "\n" in replacement_line
            or "\r" in replacement_line
            or "```" in replacement_line
            or len(replacement_line) > 10_000
        ):
            continue
        suggestions.append(
            VerifiedFixSuggestion(decision, claim.id, finding, replacement_line)
        )
    return tuple(suggestions)
