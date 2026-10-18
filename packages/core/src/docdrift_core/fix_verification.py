"""In-memory candidate patch re-extraction and verification workflow."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from .checkers import check_claim
from .extract import extract_markdown
from .index import CodeIndex
from .models import Claim, Evidence, Finding, Verdict
from .orchestration import GroundedVerificationClient, orchestrate_verification
from .patches import (
    DEFAULT_PATCH_LIMITS,
    DocumentationPatch,
    PatchLimits,
    create_span_patch,
)


class CandidatePatchStatus(StrEnum):
    """Whether the supplied patch was checked and applied to an in-memory copy."""

    REJECTED = "rejected"
    APPLIED_IN_MEMORY = "applied_in_memory"


class ReExtractionStatus(StrEnum):
    """Outcome of locating the edited claim in the patched document."""

    NOT_RUN = "not_run"
    FOUND = "found"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"


class ReVerificationStatus(StrEnum):
    """Whether verification ran and whether it completed safely."""

    NOT_RUN = "not_run"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FixReverificationResult:
    """Phase-by-phase result; patch application alone never implies a verified fix."""

    patch_status: CandidatePatchStatus
    re_extraction_status: ReExtractionStatus
    verification_status: ReVerificationStatus
    re_extracted_claim: Claim | None
    verification_finding: Finding | None
    patched_document: str | None
    fix_verified: bool
    explanation: str


def _result(
    *,
    patch_status: CandidatePatchStatus = CandidatePatchStatus.REJECTED,
    re_extraction_status: ReExtractionStatus = ReExtractionStatus.NOT_RUN,
    verification_status: ReVerificationStatus = ReVerificationStatus.NOT_RUN,
    re_extracted_claim: Claim | None = None,
    verification_finding: Finding | None = None,
    patched_document: str | None = None,
    fix_verified: bool = False,
    explanation: str,
) -> FixReverificationResult:
    return FixReverificationResult(
        patch_status,
        re_extraction_status,
        verification_status,
        re_extracted_claim,
        verification_finding,
        patched_document,
        fix_verified,
        explanation,
    )


def _claim_start_offset(document_text: str, claim: Claim) -> int | None:
    if (
        not isinstance(claim.line, int)
        or isinstance(claim.line, bool)
        or claim.line < 1
        or not isinstance(claim.span_start, int)
        or isinstance(claim.span_start, bool)
        or not isinstance(claim.span_end, int)
        or isinstance(claim.span_end, bool)
    ):
        return None
    line_start = 0
    for line_number, line in enumerate(document_text.splitlines(keepends=True), 1):
        if line_number == claim.line:
            content = line.splitlines()[0] if line.splitlines() else ""
            if (
                content != claim.text
                or claim.span_start < 0
                or claim.span_end < claim.span_start
                or claim.span_end > len(content)
            ):
                return None
            return line_start + claim.span_start
        line_start += len(line)
    return None


def _line_start_offsets(document_text: str) -> list[int]:
    starts: list[int] = []
    offset = 0
    for line in document_text.splitlines(keepends=True):
        starts.append(offset)
        offset += len(line)
    return starts


def _has_duplicate_extracted_text(claim: Claim) -> bool:
    """Catch identical same-line matches deduplicated by the Markdown extractor."""
    matched_text = claim.text[claim.span_start : claim.span_end]
    if not matched_text:
        return False
    first = claim.text.find(matched_text)
    return first >= 0 and claim.text.find(matched_text, first + len(matched_text)) >= 0


def _claim_span_offsets(document_text: str, claim: Claim) -> tuple[int, int] | None:
    start = _claim_start_offset(document_text, claim)
    if start is None:
        return None
    return start, start + claim.span_end - claim.span_start


def _current_repository_evidence(index: CodeIndex, evidence: Evidence) -> bool:
    """Confirm an evidence record still names an exact in-root source line."""
    if (
        not isinstance(evidence, Evidence)
        or not isinstance(evidence.path, str)
        or not isinstance(evidence.line, int)
        or isinstance(evidence.line, bool)
        or evidence.line < 1
        or not isinstance(evidence.snippet, str)
    ):
        return False
    path = PurePosixPath(evidence.path)
    if (
        not evidence.path
        or "\\" in evidence.path
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != evidence.path
    ):
        return False
    try:
        root = index.root.resolve()
        resolved = (root / evidence.path).resolve()
        if not resolved.is_relative_to(root):
            return False
        return index.evidence(evidence.path, evidence.line) == evidence
    except (OSError, RuntimeError, ValueError):
        return False


async def reverify_candidate_patch(
    original_document: str,
    patch: DocumentationPatch,
    affected_claim: Claim,
    code_index: CodeIndex,
    *,
    evidence: Sequence[Evidence] = (),
    client: GroundedVerificationClient | None = None,
    limits: PatchLimits = DEFAULT_PATCH_LIMITS,
) -> FixReverificationResult:
    """Apply a candidate in memory, re-extract its claim, and verify the new claim.

    The claim must still be present in ``original_document`` and the patch must fall
    within its extracted span. Deterministic checkers remain authoritative; an optional
    grounded client is used only through the existing abstaining orchestration layer.
    This function performs no document writes and no publication.
    """
    if not isinstance(original_document, str):
        return _result(explanation="Original document must be text.")
    if not isinstance(patch, DocumentationPatch):
        return _result(explanation="Candidate patch has an unsupported type.")
    if not isinstance(affected_claim, Claim):
        return _result(explanation="Affected claim has an unsupported type.")
    if not isinstance(code_index, CodeIndex):
        return _result(explanation="A repository code index is required for verification.")

    try:
        checked_patch = create_span_patch(
            original_document,
            start_offset=patch.start_offset,
            end_offset=patch.end_offset,
            expected_original=patch.expected_original,
            replacement_text=patch.replacement_text,
            limits=limits,
        )
    except (TypeError, ValueError, AttributeError) as exc:
        return _result(explanation=f"Candidate patch failed validation: {exc}")
    if checked_patch != patch:
        return _result(explanation="Candidate patch metadata does not match its exact text edit.")
    if patch.proposed_text == original_document:
        return _result(explanation="Candidate patch does not change the document.")

    try:
        original_matches = [
            claim
            for claim in extract_markdown(original_document, affected_claim.document_path)
            if claim == affected_claim
        ]
    except Exception:  # noqa: BLE001 - extraction failure cannot establish a safe target.
        return _result(explanation="The affected claim could not be confirmed in the original document.")
    if len(original_matches) != 1:
        return _result(explanation="The patch does not identify one current original claim.")

    affected_span = _claim_span_offsets(original_document, affected_claim)
    if (
        affected_span is None
        or patch.start_offset < affected_span[0]
        or patch.end_offset > affected_span[1]
    ):
        return _result(explanation="Candidate patch is outside the affected claim span.")

    patched_document = patch.proposed_text
    patch_status = CandidatePatchStatus.APPLIED_IN_MEMORY
    if not patch.replacement_text:
        return _result(
            patch_status=patch_status,
            re_extraction_status=ReExtractionStatus.MISSING,
            patched_document=patched_document,
            explanation="Deleting the target produced no claim that can be re-verified.",
        )

    try:
        extracted = extract_markdown(patched_document, affected_claim.document_path)
    except Exception:  # noqa: BLE001 - unsupported or failed extraction cannot pass.
        return _result(
            patch_status=patch_status,
            re_extraction_status=ReExtractionStatus.FAILED,
            patched_document=patched_document,
            explanation="The edited claim could not be re-extracted.",
        )

    replacement_start = patch.start_offset
    replacement_end = replacement_start + len(patch.replacement_text)
    starts = _line_start_offsets(patched_document)
    impacted_claims = [
        claim
        for claim in extracted
        if claim.document_path == affected_claim.document_path
        and claim.type is affected_claim.type
        and claim.line <= len(starts)
        and max(starts[claim.line - 1] + claim.span_start, replacement_start)
        < min(starts[claim.line - 1] + claim.span_end, replacement_end)
    ]
    if not impacted_claims:
        return _result(
            patch_status=patch_status,
            re_extraction_status=ReExtractionStatus.MISSING,
            patched_document=patched_document,
            explanation="No supported claim was re-extracted at the edited span.",
        )
    if len(impacted_claims) != 1 or any(
        _has_duplicate_extracted_text(claim) for claim in impacted_claims
    ):
        return _result(
            patch_status=patch_status,
            re_extraction_status=ReExtractionStatus.AMBIGUOUS,
            patched_document=patched_document,
            explanation="More than one claim overlaps the edited span.",
        )

    updated_claim = impacted_claims[0]
    extraction_status = ReExtractionStatus.FOUND
    try:
        finding = check_claim(updated_claim, code_index)
        if not isinstance(finding, Finding) or finding.claim != updated_claim:
            return _result(
                patch_status=patch_status,
                re_extraction_status=extraction_status,
                verification_status=ReVerificationStatus.FAILED,
                re_extracted_claim=updated_claim,
                patched_document=patched_document,
                explanation="The deterministic checker returned a mismatched result.",
            )
        if finding.verdict is Verdict.UNVERIFIABLE and client is not None:
            supplied_evidence = (*finding.evidence, *evidence)
            if not all(_current_repository_evidence(code_index, item) for item in supplied_evidence):
                return _result(
                    patch_status=patch_status,
                    re_extraction_status=extraction_status,
                    verification_status=ReVerificationStatus.FAILED,
                    re_extracted_claim=updated_claim,
                    verification_finding=finding,
                    patched_document=patched_document,
                    explanation="Verification evidence does not match current in-repository source lines.",
                )
            finding = await orchestrate_verification(
                updated_claim,
                finding,
                evidence,
                client,
            )
            if finding.claim != updated_claim:
                return _result(
                    patch_status=patch_status,
                    re_extraction_status=extraction_status,
                    verification_status=ReVerificationStatus.FAILED,
                    re_extracted_claim=updated_claim,
                    verification_finding=finding,
                    patched_document=patched_document,
                    explanation="Grounded verification returned a mismatched claim.",
                )
    except Exception:  # noqa: BLE001 - checker/client failures must fail closed.
        return _result(
            patch_status=patch_status,
            re_extraction_status=extraction_status,
            verification_status=ReVerificationStatus.FAILED,
            re_extracted_claim=updated_claim,
            patched_document=patched_document,
            explanation="Re-verification failed; the candidate fix was not verified.",
        )

    verified = finding.verdict is Verdict.TRUE
    return _result(
        patch_status=patch_status,
        re_extraction_status=extraction_status,
        verification_status=ReVerificationStatus.COMPLETED,
        re_extracted_claim=updated_claim,
        verification_finding=finding,
        patched_document=patched_document,
        fix_verified=verified,
        explanation=(
            "The re-extracted claim was verified true."
            if verified
            else "The re-extracted claim was not verified true."
        ),
    )
