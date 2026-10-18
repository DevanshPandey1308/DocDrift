"""Pure planning for delivery of already verified documentation fixes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from enum import StrEnum

from docdrift_core.fix_verification import (
    CandidatePatchStatus,
    ClaimReverification,
    ExtractionCoverageStatus,
    FixReverificationResult,
    MarkdownValidationStatus,
    ReExtractionStatus,
    ReVerificationStatus,
)
from docdrift_core.models import Claim, Finding, Verdict
from docdrift_core.patches import (
    DocumentationPatch,
    PatchLimits,
    create_claim_patch,
    create_span_patch,
)

from .github import is_valid_inline_location

_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


class FixDeliveryMode(StrEnum):
    """Safe destination selected for a candidate fix."""

    INLINE_SUGGESTION = "inline_suggestion"
    ANNOTATION_ONLY = "annotation_only"


class FixDeliveryStatus(StrEnum):
    """Whether patch delivery is ready, fell back, or was refused."""

    READY = "ready"
    FALLBACK = "fallback"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class FixDeliveryDecision:
    """A pure delivery plan. Only READY decisions carry a candidate patch."""

    mode: FixDeliveryMode
    status: FixDeliveryStatus
    patch_delivery_eligible: bool
    patch: DocumentationPatch | None
    document_path: str | None
    document_line: int | None
    head_sha: str | None
    reason: str


def _decision(
    mode: FixDeliveryMode,
    status: FixDeliveryStatus,
    reason: str,
    *,
    patch: DocumentationPatch | None = None,
    document_path: str | None = None,
    document_line: int | None = None,
    head_sha: str | None = None,
) -> FixDeliveryDecision:
    return FixDeliveryDecision(
        mode=mode,
        status=status,
        patch_delivery_eligible=status is FixDeliveryStatus.READY,
        patch=patch if status is FixDeliveryStatus.READY else None,
        document_path=document_path,
        document_line=document_line,
        head_sha=head_sha,
        reason=reason,
    )


def _complete_verified_result(result: object) -> bool:
    """Require the full edited-region verification record, not only its boolean flag."""
    if not isinstance(result, FixReverificationResult) or result.fix_verified is not True:
        return False
    if (
        result.patch_status is not CandidatePatchStatus.APPLIED_IN_MEMORY
        or result.markdown_validation_status is not MarkdownValidationStatus.VALID
        or result.extraction_coverage_status is not ExtractionCoverageStatus.COMPLETE
        or result.re_extraction_status is not ReExtractionStatus.FOUND
        or result.verification_status is not ReVerificationStatus.COMPLETED
        or not isinstance(result.re_extracted_claim, Claim)
        or not isinstance(result.verification_finding, Finding)
        or result.verification_finding.claim != result.re_extracted_claim
        or result.verification_finding.verdict is not Verdict.TRUE
        or not isinstance(result.required_claims, tuple)
        or not result.required_claims
        or not isinstance(result.claim_verifications, tuple)
        or len(result.required_claims) != len(result.claim_verifications)
    ):
        return False

    required_ids = [
        claim.id
        for claim in result.required_claims
        if isinstance(claim, Claim) and isinstance(claim.id, str) and claim.id
    ]
    if len(required_ids) != len(result.required_claims) or len(set(required_ids)) != len(required_ids):
        return False
    verified_by_id: dict[str, ClaimReverification] = {}
    for item in result.claim_verifications:
        if (
            not isinstance(item, ClaimReverification)
            or item.completed is not True
            or not isinstance(item.claim, Claim)
            or not isinstance(item.claim.id, str)
            or not item.claim.id
            or not isinstance(item.finding, Finding)
            or item.finding.claim != item.claim
            or item.finding.verdict is not Verdict.TRUE
            or item.claim.id in verified_by_id
        ):
            return False
        verified_by_id[item.claim.id] = item
    target = result.re_extracted_claim
    target_result = verified_by_id.get(target.id)
    return (
        set(verified_by_id) == set(required_ids)
        and target in result.required_claims
        and target_result is not None
        and target_result.claim == target
        and target_result.finding == result.verification_finding
    )


def _patch_matches_verification(
    patch: object,
    result: FixReverificationResult,
    affected_claim: object,
) -> bool:
    if (
        not isinstance(patch, DocumentationPatch)
        or not isinstance(affected_claim, Claim)
        or result.patched_document != patch.proposed_text
        or result.re_extracted_claim is None
        or result.re_extracted_claim.document_path != affected_claim.document_path
        or result.re_extracted_claim.type is not affected_claim.type
    ):
        return False
    try:
        limits = PatchLimits(
            max_changed_characters=max(1, patch.changed_characters),
            max_changed_lines=max(1, patch.changed_lines),
        )
        validated_patch = create_span_patch(
            patch.original_text,
            start_offset=patch.start_offset,
            end_offset=patch.end_offset,
            expected_original=patch.expected_original,
            replacement_text=patch.replacement_text,
            limits=limits,
        )
        claim_patch = create_claim_patch(
            patch.original_text,
            affected_claim,
            expected_original=patch.expected_original,
            replacement_text=patch.replacement_text,
            limits=limits,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return validated_patch == patch and claim_patch == patch


def _valid_added_line_map(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        for path, lines in value.items():
            if not isinstance(lines, AbstractSet) or not is_valid_inline_location(path, 1, 1):
                return False
            if any(
                isinstance(line, bool) or not isinstance(line, int) or line < 1
                for line in lines
            ):
                return False
    except Exception:  # noqa: BLE001 - malformed caller data must fail closed.
        return False
    return True


def plan_verified_fix_delivery(
    verification: FixReverificationResult,
    patch: DocumentationPatch,
    affected_claim: Claim,
    *,
    analyzed_head_sha: str,
    current_head_sha: str,
    added_markdown_lines: Mapping[str, AbstractSet[int]] | None,
) -> FixDeliveryDecision:
    """Plan an inline suggestion only for a fully verified fix at a current diff line.

    The function performs no file access, network calls, or publication. A missing or
    invalid location returns annotation-only/refused status without carrying the patch.
    """
    if not _complete_verified_result(verification):
        return _decision(
            FixDeliveryMode.ANNOTATION_ONLY,
            FixDeliveryStatus.REFUSED,
            "Full edited-region verification did not explicitly accept every claim as true.",
        )
    if not _patch_matches_verification(patch, verification, affected_claim):
        return _decision(
            FixDeliveryMode.ANNOTATION_ONLY,
            FixDeliveryStatus.REFUSED,
            "The candidate patch does not match the fully verified claim and document.",
        )
    if (
        not isinstance(analyzed_head_sha, str)
        or not _COMMIT_SHA.fullmatch(analyzed_head_sha)
        or not isinstance(current_head_sha, str)
        or not _COMMIT_SHA.fullmatch(current_head_sha)
        or analyzed_head_sha.lower() != current_head_sha.lower()
    ):
        return _decision(
            FixDeliveryMode.ANNOTATION_ONLY,
            FixDeliveryStatus.REFUSED,
            "The pull request head is stale, invalid, or differs from the analyzed revision.",
        )
    if added_markdown_lines is None or not _valid_added_line_map(added_markdown_lines):
        return _decision(
            FixDeliveryMode.ANNOTATION_ONLY,
            FixDeliveryStatus.REFUSED,
            "The changed-line map is unavailable or invalid; patch delivery was refused.",
        )

    path = affected_claim.document_path
    line = affected_claim.line
    if not is_valid_inline_location(path, line, line):
        return _decision(
            FixDeliveryMode.ANNOTATION_ONLY,
            FixDeliveryStatus.REFUSED,
            "The target path or line is not a valid inline review location.",
        )
    if line not in added_markdown_lines.get(path, frozenset()):
        return _decision(
            FixDeliveryMode.ANNOTATION_ONLY,
            FixDeliveryStatus.FALLBACK,
            "The target line is outside the pull request diff; use annotation-only delivery.",
            document_path=path,
            document_line=line,
            head_sha=current_head_sha.lower(),
        )
    return _decision(
        FixDeliveryMode.INLINE_SUGGESTION,
        FixDeliveryStatus.READY,
        "Every edited-region claim is verified true and the target line is in the current PR diff.",
        patch=patch,
        document_path=path,
        document_line=line,
        head_sha=current_head_sha.lower(),
    )
