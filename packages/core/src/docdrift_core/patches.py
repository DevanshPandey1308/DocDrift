"""Construct guarded in-memory documentation span patches without writing files."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Claim

DEFAULT_MAX_CHANGED_CHARACTERS = 1_000
DEFAULT_MAX_CHANGED_LINES = 5


class PatchValidationError(ValueError):
    """Raised when a patch target is stale, ambiguous, or exceeds its guards."""


@dataclass(frozen=True, slots=True)
class PatchLimits:
    """Maximum size of the replaced span, configurable for a caller or test."""

    max_changed_characters: int = DEFAULT_MAX_CHANGED_CHARACTERS
    max_changed_lines: int = DEFAULT_MAX_CHANGED_LINES

    def __post_init__(self) -> None:
        for name, value in (
            ("max_changed_characters", self.max_changed_characters),
            ("max_changed_lines", self.max_changed_lines),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


DEFAULT_PATCH_LIMITS = PatchLimits()


@dataclass(frozen=True, slots=True)
class DocumentationPatch:
    """An immutable proposed edit with its source and resulting document text.

    Character offsets are zero-based and the end offset is exclusive. The changed
    character count is the larger of the removed and inserted span lengths; changed
    lines is the larger physical-line count of those spans.
    """

    original_text: str
    proposed_text: str
    start_offset: int
    end_offset: int
    expected_original: str
    replacement_text: str
    changed_characters: int
    changed_lines: int


def _physical_line_count(text: str) -> int:
    if not text:
        return 0
    line_breaks = text.count("\n") + text.count("\r") - text.count("\r\n")
    return line_breaks + 1


def _without_line_ending(line: str) -> str:
    for ending in ("\r\n", "\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
        if line.endswith(ending):
            return line[: -len(ending)]
    return line


def create_span_patch(
    document_text: str,
    *,
    start_offset: int,
    end_offset: int,
    expected_original: str,
    replacement_text: str,
    limits: PatchLimits = DEFAULT_PATCH_LIMITS,
) -> DocumentationPatch:
    """Create a patch only when exact offsets still contain the expected text.

    No file is read or written. An empty expected span represents an insertion at the
    explicit offset; an empty replacement represents deletion of the exact span.
    """
    if not isinstance(document_text, str):
        raise TypeError("document_text must be a string")
    if not isinstance(start_offset, int) or isinstance(start_offset, bool):
        raise PatchValidationError("start_offset must be an integer")
    if not isinstance(end_offset, int) or isinstance(end_offset, bool):
        raise PatchValidationError("end_offset must be an integer")
    if start_offset < 0 or end_offset < start_offset or end_offset > len(document_text):
        raise PatchValidationError("patch offsets are negative, reversed, or outside the document")
    if not isinstance(expected_original, str) or not isinstance(replacement_text, str):
        raise TypeError("expected_original and replacement_text must be strings")
    if not isinstance(limits, PatchLimits):
        raise TypeError("limits must be PatchLimits")

    actual_original = document_text[start_offset:end_offset]
    if actual_original != expected_original:
        raise PatchValidationError("patch target is stale: text at the requested span has changed")

    changed_characters = max(len(expected_original), len(replacement_text))
    changed_lines = max(
        _physical_line_count(expected_original), _physical_line_count(replacement_text)
    )
    if changed_characters > limits.max_changed_characters:
        raise PatchValidationError("patch exceeds the changed-character limit")
    if changed_lines > limits.max_changed_lines:
        raise PatchValidationError("patch exceeds the changed-line limit")

    proposed_text = (
        document_text[:start_offset] + replacement_text + document_text[end_offset:]
    )
    return DocumentationPatch(
        original_text=document_text,
        proposed_text=proposed_text,
        start_offset=start_offset,
        end_offset=end_offset,
        expected_original=expected_original,
        replacement_text=replacement_text,
        changed_characters=changed_characters,
        changed_lines=changed_lines,
    )


def create_unique_text_patch(
    document_text: str,
    *,
    expected_original: str,
    replacement_text: str,
    limits: PatchLimits = DEFAULT_PATCH_LIMITS,
) -> DocumentationPatch:
    """Create a patch by exact text only when that text has one unambiguous location."""
    if not isinstance(document_text, str):
        raise TypeError("document_text must be a string")
    if not isinstance(expected_original, str) or not expected_original:
        raise PatchValidationError("text-only patch targets must be non-empty")

    occurrences: list[int] = []
    offset = document_text.find(expected_original)
    while offset >= 0:
        occurrences.append(offset)
        offset = document_text.find(expected_original, offset + 1)
    if not occurrences:
        raise PatchValidationError("patch target is stale: expected text was not found")
    if len(occurrences) > 1:
        raise PatchValidationError("patch target is ambiguous: expected text occurs more than once")

    start_offset = occurrences[0]
    return create_span_patch(
        document_text,
        start_offset=start_offset,
        end_offset=start_offset + len(expected_original),
        expected_original=expected_original,
        replacement_text=replacement_text,
        limits=limits,
    )


def create_claim_patch(
    document_text: str,
    claim: Claim,
    *,
    expected_original: str,
    replacement_text: str,
    limits: PatchLimits = DEFAULT_PATCH_LIMITS,
) -> DocumentationPatch:
    """Translate an existing claim's one-based line and line-local span into offsets.

    The complete source line must still match ``claim.text``. This prevents applying a
    claim span to a document version that changed after extraction.
    """
    if not isinstance(claim, Claim):
        raise TypeError("claim must be a core Claim")
    if not isinstance(document_text, str):
        raise TypeError("document_text must be a string")
    if (
        not isinstance(claim.line, int)
        or isinstance(claim.line, bool)
        or claim.line < 1
    ):
        raise PatchValidationError("claim line must be one-based and positive")
    if not isinstance(claim.text, str):
        raise PatchValidationError("claim source text must be a string")

    line_start = 0
    for line_number, line_with_ending in enumerate(document_text.splitlines(keepends=True), 1):
        line_text = _without_line_ending(line_with_ending)
        if line_number == claim.line:
            if line_text != claim.text:
                raise PatchValidationError("patch target is stale: claim source line has changed")
            if (
                not isinstance(claim.span_start, int)
                or isinstance(claim.span_start, bool)
                or not isinstance(claim.span_end, int)
                or isinstance(claim.span_end, bool)
                or claim.span_start < 0
                or claim.span_end < claim.span_start
                or claim.span_end > len(line_text)
            ):
                raise PatchValidationError("claim span is outside its source line")
            if line_text[claim.span_start : claim.span_end] != expected_original:
                raise PatchValidationError("patch target is stale: claim span differs from expected text")
            return create_span_patch(
                document_text,
                start_offset=line_start + claim.span_start,
                end_offset=line_start + claim.span_end,
                expected_original=expected_original,
                replacement_text=replacement_text,
                limits=limits,
            )
        line_start += len(line_with_ending)

    raise PatchValidationError("patch target is stale: claim line is outside the document")
