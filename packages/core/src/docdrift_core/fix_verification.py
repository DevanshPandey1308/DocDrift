"""In-memory candidate patch re-extraction and verification workflow."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from markdown_it import MarkdownIt

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


class MarkdownValidationStatus(StrEnum):
    """Whether the candidate document was parsed as Markdown."""

    NOT_RUN = "not_run"
    VALID = "valid"
    INVALID = "invalid"


class ExtractionCoverageStatus(StrEnum):
    """Whether all supported claims in the edited region were located safely."""

    NOT_RUN = "not_run"
    COMPLETE = "complete"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ClaimReverification:
    """Verification outcome for one supported claim in the edited region."""

    claim: Claim
    finding: Finding | None
    completed: bool
    explanation: str


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
    markdown_validation_status: MarkdownValidationStatus = MarkdownValidationStatus.NOT_RUN
    markdown_validation_error: str | None = None
    extraction_coverage_status: ExtractionCoverageStatus = ExtractionCoverageStatus.NOT_RUN
    required_claims: tuple[Claim, ...] = ()
    claim_verifications: tuple[ClaimReverification, ...] = ()


def _result(
    *,
    patch_status: CandidatePatchStatus = CandidatePatchStatus.REJECTED,
    re_extraction_status: ReExtractionStatus = ReExtractionStatus.NOT_RUN,
    verification_status: ReVerificationStatus = ReVerificationStatus.NOT_RUN,
    re_extracted_claim: Claim | None = None,
    verification_finding: Finding | None = None,
    patched_document: str | None = None,
    markdown_validation_status: MarkdownValidationStatus = MarkdownValidationStatus.NOT_RUN,
    markdown_validation_error: str | None = None,
    extraction_coverage_status: ExtractionCoverageStatus = ExtractionCoverageStatus.NOT_RUN,
    required_claims: tuple[Claim, ...] = (),
    claim_verifications: tuple[ClaimReverification, ...] = (),
    fix_verified: bool = False,
    explanation: str,
) -> FixReverificationResult:
    return FixReverificationResult(
        patch_status=patch_status,
        re_extraction_status=re_extraction_status,
        verification_status=verification_status,
        re_extracted_claim=re_extracted_claim,
        verification_finding=verification_finding,
        patched_document=patched_document,
        fix_verified=fix_verified,
        explanation=explanation,
        markdown_validation_status=markdown_validation_status,
        markdown_validation_error=markdown_validation_error,
        extraction_coverage_status=extraction_coverage_status,
        required_claims=required_claims,
        claim_verifications=claim_verifications,
    )


def _validate_markdown(document_text: str) -> str | None:
    """Parse CommonMark and return a safe diagnostic when parsing fails.

    CommonMark is intentionally permissive; a successful parse means the parser
    accepted the document, not that every construct is stylistically well formed.
    """
    try:
        MarkdownIt("commonmark").parse(document_text)
    except Exception as exc:  # noqa: BLE001 - parser failures must fail closed.
        return f"Markdown parser rejected the candidate: {type(exc).__name__}."
    return None


def _edited_line_range(
    start: int,
    replacement_length: int,
    line_starts: Sequence[int],
) -> tuple[int, int]:
    """Return one-based physical lines touched by the inserted/replacement text."""
    end_probe = start + max(replacement_length - 1, 0)
    start_line = bisect_right(line_starts, start) or 1
    end_line = bisect_right(line_starts, end_probe) or 1
    return start_line, end_line


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


def _mapped_other_original_claims(
    original_document: str,
    patched_document: str,
    original_claims: Sequence[Claim],
    patched_claims: Sequence[Claim],
    affected_claim: Claim,
    patch: DocumentationPatch,
) -> tuple[Claim, ...] | None:
    """Map all unchanged original claims to unique extracted claims after the edit."""
    delta = len(patch.replacement_text) - len(patch.expected_original)
    mapped_claims: list[Claim] = []
    for original_claim in original_claims:
        if original_claim == affected_claim:
            continue
        original_span = _claim_span_offsets(original_document, original_claim)
        if original_span is None:
            return None
        start, end = original_span
        overlaps_edit = max(start, patch.start_offset) < min(end, patch.end_offset)
        if overlaps_edit:
            return None
        mapped_start, mapped_end = (
            (start + delta, end + delta)
            if start >= patch.end_offset
            else (start, end)
        )
        matches = []
        for candidate in patched_claims:
            if (
                candidate.type is original_claim.type
                and candidate.subject == original_claim.subject
                and candidate.expected == original_claim.expected
                and _claim_span_offsets(patched_document, candidate)
                == (mapped_start, mapped_end)
            ):
                matches.append(candidate)
        if len(matches) != 1:
            return None
        mapped_claims.append(matches[0])
    return tuple(mapped_claims)


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
        original_extracted = extract_markdown(original_document, affected_claim.document_path)
        original_matches = [claim for claim in original_extracted if claim == affected_claim]
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
    original_starts = _line_start_offsets(original_document)
    original_line_start, original_line_end = _edited_line_range(
        patch.start_offset,
        patch.end_offset - patch.start_offset,
        original_starts,
    )
    original_region_claims = [
        claim
        for claim in original_extracted
        if claim.document_path == affected_claim.document_path
        and claim.line <= len(original_starts)
        and original_line_start <= claim.line <= original_line_end
    ]
    markdown_error = _validate_markdown(patched_document)
    if markdown_error is not None:
        return _result(
            patch_status=patch_status,
            patched_document=patched_document,
            markdown_validation_status=MarkdownValidationStatus.INVALID,
            markdown_validation_error=markdown_error,
            explanation="The candidate Markdown could not be parsed; the fix was not verified.",
        )

    try:
        extracted = extract_markdown(patched_document, affected_claim.document_path)
    except Exception:  # noqa: BLE001 - unsupported or failed extraction cannot pass.
        return _result(
            patch_status=patch_status,
            re_extraction_status=ReExtractionStatus.FAILED,
            patched_document=patched_document,
            markdown_validation_status=MarkdownValidationStatus.VALID,
            extraction_coverage_status=ExtractionCoverageStatus.FAILED,
            explanation="The edited claim could not be re-extracted.",
        )

    replacement_start = patch.start_offset
    starts = _line_start_offsets(patched_document)
    line_start, line_end = _edited_line_range(
        replacement_start, len(patch.replacement_text), starts
    )
    line_claims = [
        claim
        for claim in extracted
        if claim.document_path == affected_claim.document_path
        and claim.line <= len(starts)
        and line_start <= claim.line <= line_end
    ]
    mapped_original_claims = _mapped_other_original_claims(
        original_document,
        patched_document,
        original_region_claims,
        extracted,
        affected_claim,
        patch,
    )
    if mapped_original_claims is None:
        return _result(
            patch_status=patch_status,
            re_extraction_status=ReExtractionStatus.MISSING,
            patched_document=patched_document,
            markdown_validation_status=MarkdownValidationStatus.VALID,
            extraction_coverage_status=ExtractionCoverageStatus.MISSING,
            explanation="An existing supported claim in the edited region was lost or changed ambiguously.",
        )
    required_by_id = {claim.id: claim for claim in (*line_claims, *mapped_original_claims)}
    required_claims = sorted(
        required_by_id.values(), key=lambda claim: (claim.line, claim.span_start, claim.id)
    )
    if not required_claims:
        return _result(
            patch_status=patch_status,
            re_extraction_status=ReExtractionStatus.MISSING,
            patched_document=patched_document,
            markdown_validation_status=MarkdownValidationStatus.VALID,
            extraction_coverage_status=ExtractionCoverageStatus.MISSING,
            explanation="No supported claims were re-extracted from the edited region.",
        )

    replacement_end = replacement_start + len(patch.replacement_text)
    intended_claims = [
        claim
        for claim in required_claims
        if claim.type is affected_claim.type
        and max(starts[claim.line - 1] + claim.span_start, replacement_start)
        < min(starts[claim.line - 1] + claim.span_end, replacement_end)
    ]
    if len(intended_claims) != 1 or any(
        _has_duplicate_extracted_text(claim) for claim in required_claims
    ):
        extraction_status = (
            ReExtractionStatus.MISSING
            if not intended_claims
            else ReExtractionStatus.AMBIGUOUS
        )
        coverage_status = (
            ExtractionCoverageStatus.MISSING
            if not intended_claims
            else ExtractionCoverageStatus.AMBIGUOUS
        )
        return _result(
            patch_status=patch_status,
            re_extraction_status=extraction_status,
            patched_document=patched_document,
            markdown_validation_status=MarkdownValidationStatus.VALID,
            extraction_coverage_status=coverage_status,
            required_claims=tuple(required_claims),
            explanation=(
                "The intended claim could not be uniquely located in the edited region."
                if len(intended_claims) != 1
                else "The edited region contains an ambiguous duplicate extracted claim."
            ),
        )

    updated_claim = intended_claims[0]
    extraction_status = ReExtractionStatus.FOUND
    verification_results: list[ClaimReverification] = []
    for claim in required_claims:
        try:
            finding = check_claim(claim, code_index)
            if not isinstance(finding, Finding) or finding.claim != claim:
                verification_results.append(
                    ClaimReverification(
                        claim, None, False, "The deterministic checker returned a mismatched result."
                    )
                )
                continue
            if finding.verdict is Verdict.UNVERIFIABLE and client is not None:
                supplied_evidence = (*finding.evidence, *evidence)
                if not all(
                    _current_repository_evidence(code_index, item)
                    for item in supplied_evidence
                ):
                    verification_results.append(
                        ClaimReverification(
                            claim,
                            finding,
                            False,
                            "Verification evidence does not match current in-repository source lines.",
                        )
                    )
                    continue
                finding = await orchestrate_verification(claim, finding, evidence, client)
                if finding.claim != claim:
                    verification_results.append(
                        ClaimReverification(
                            claim, finding, False, "Grounded verification returned a mismatched claim."
                        )
                    )
                    continue
            verification_results.append(
                ClaimReverification(claim, finding, True, finding.explanation)
            )
        except Exception:  # noqa: BLE001 - checker/client failures must fail closed.
            verification_results.append(
                ClaimReverification(
                    claim, None, False, "Re-verification failed for this extracted claim."
                )
            )

    intended_result = next(
        (item for item in verification_results if item.claim == updated_claim), None
    )
    all_checks_completed = all(item.completed for item in verification_results)
    all_true = all(
        item.finding is not None and item.finding.verdict is Verdict.TRUE
        for item in verification_results
    )
    verified = all_checks_completed and all_true and intended_result is not None
    status = (
        ReVerificationStatus.COMPLETED
        if all_checks_completed
        else ReVerificationStatus.FAILED
    )
    failed_claim = next((item for item in verification_results if not item.completed), None)
    return _result(
        patch_status=patch_status,
        re_extraction_status=extraction_status,
        verification_status=status,
        re_extracted_claim=updated_claim,
        verification_finding=intended_result.finding if intended_result is not None else None,
        patched_document=patched_document,
        markdown_validation_status=MarkdownValidationStatus.VALID,
        extraction_coverage_status=ExtractionCoverageStatus.COMPLETE,
        required_claims=tuple(required_claims),
        claim_verifications=tuple(verification_results),
        fix_verified=verified,
        explanation=(
            "Every supported claim extracted from the edited region was verified true."
            if verified
            else (
                failed_claim.explanation
                if failed_claim is not None
                else "At least one claim in the edited region was not verified true."
            )
        ),
    )
