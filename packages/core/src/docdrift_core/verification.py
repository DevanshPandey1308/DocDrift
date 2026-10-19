"""Provider-independent contract for grounded claim verification outputs.

This module validates structure and citations only. A caller must supply a trusted
deterministic evidence-sufficiency gate before a model's true/false verdict is accepted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from .models import Claim, Evidence


class VerificationVerdict(StrEnum):
    """Verdicts available to the grounded verification contract."""

    TRUE = "true"
    FALSE = "false"
    ABSTAIN = "abstain"


@dataclass(frozen=True, slots=True)
class VerificationSnippet:
    """A repository-relative evidence excerpt with a stable content-addressed reference."""

    path: str
    line: int
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str):
            raise TypeError("evidence path must be a string")
        path = PurePosixPath(self.path)
        if (
            not self.path
            or "\\" in self.path
            or path.is_absolute()
            or ".." in path.parts
            or str(path) in {"", "."}
            or path.as_posix() != self.path
        ):
            raise ValueError("evidence path must be a normalized repository-relative POSIX path")
        if not isinstance(self.line, int) or isinstance(self.line, bool) or self.line < 1:
            raise ValueError("evidence line must be a positive one-based line number")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("evidence text must be a non-empty string")

    @classmethod
    def from_evidence(cls, evidence: Evidence) -> VerificationSnippet:
        """Adapt an existing core evidence record without changing its source location."""
        return cls(evidence.path, evidence.line, evidence.snippet)

    @property
    def reference(self) -> str:
        """Return the same opaque reference for the same path, line, and exact text."""
        identity = json.dumps(
            [self.path, self.line, self.text], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return f"ev_{hashlib.sha256(identity).hexdigest()}"


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    """A model citation that has been resolved to supplied evidence."""

    reference: str
    quote: str


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Validated structured verification output."""

    verdict: VerificationVerdict
    evidence_refs: tuple[EvidenceCitation, ...]
    rationale: str
    input_tokens: int | None = None
    output_tokens: int | None = None


EvidenceSufficiencyGate = Callable[
    [Claim, tuple[VerificationSnippet, ...], tuple[EvidenceCitation, ...]], bool
]

_OUTPUT_KEYS = frozenset({"verdict", "evidence_refs", "rationale"})
_CITATION_KEYS = frozenset({"ref", "quote"})


def _abstain(reason: str) -> VerificationResult:
    return VerificationResult(VerificationVerdict.ABSTAIN, (), reason)


def validate_verification_output(
    claim: Claim,
    output: object,
    evidence: Sequence[VerificationSnippet],
    *,
    evidence_sufficient: EvidenceSufficiencyGate | None = None,
) -> VerificationResult:
    """Validate a structured model result against the exact evidence for this call.

    ``output`` must be a decoded JSON object with exactly ``verdict``, ``evidence_refs``,
    and ``rationale`` keys. Free-form strings and serialized JSON are intentionally not
    parsed. True/false results require exact citations and a trusted deterministic gate;
    all malformed, ungrounded, or insufficient cases fail closed as abstentions.
    """
    if not isinstance(output, Mapping) or set(output) != _OUTPUT_KEYS:
        return _abstain("Malformed verification result: expected exactly verdict, evidence_refs, and rationale.")

    raw_verdict = output["verdict"]
    if not isinstance(raw_verdict, str):
        return _abstain("Malformed verification result: verdict must be a string.")
    try:
        verdict = VerificationVerdict(raw_verdict)
    except ValueError:
        return _abstain("Malformed verification result: verdict must be true, false, or abstain.")

    rationale = output["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        return _abstain("Malformed verification result: rationale must be a non-empty string.")

    raw_citations = output["evidence_refs"]
    if not isinstance(raw_citations, list):
        return _abstain("Malformed verification result: evidence_refs must be a list.")

    supplied_by_ref: dict[str, VerificationSnippet] = {}
    for snippet in evidence:
        if not isinstance(snippet, VerificationSnippet):
            return _abstain("Invalid verification input: evidence must contain only supplied snippets.")
        if snippet.reference in supplied_by_ref:
            return _abstain("Invalid verification input: evidence references are not unique.")
        supplied_by_ref[snippet.reference] = snippet

    citations: list[EvidenceCitation] = []
    cited_snippets: list[VerificationSnippet] = []
    seen_refs: set[str] = set()
    for item in raw_citations:
        if not isinstance(item, Mapping) or set(item) != _CITATION_KEYS:
            return _abstain("Malformed verification result: each evidence reference must contain exactly ref and quote.")
        reference, quote = item["ref"], item["quote"]
        if not isinstance(reference, str) or not reference or not isinstance(quote, str) or not quote.strip():
            return _abstain("Malformed verification result: evidence references and quotes must be non-empty strings.")
        matched_snippet = supplied_by_ref.get(reference)
        if matched_snippet is None:
            return _abstain("Unrecognized evidence reference; the result cited text not supplied for this call.")
        if reference in seen_refs:
            return _abstain("Malformed verification result: evidence references must not be duplicated.")
        if quote not in matched_snippet.text:
            return _abstain("Evidence quote does not exactly match the referenced supplied snippet.")
        seen_refs.add(reference)
        citations.append(EvidenceCitation(reference, quote))
        cited_snippets.append(matched_snippet)

    if verdict is VerificationVerdict.ABSTAIN:
        return VerificationResult(verdict, tuple(citations), rationale.strip())
    if not evidence:
        return _abstain("No repository evidence was supplied for this claim.")
    if not citations:
        return _abstain("A true or false verdict requires at least one exact evidence citation.")
    if evidence_sufficient is None:
        return _abstain("No deterministic evidence-sufficiency gate was supplied; the claim cannot be established safely.")
    try:
        sufficient = evidence_sufficient(claim, tuple(cited_snippets), tuple(citations))
    except Exception:  # noqa: BLE001 - a failing sufficiency gate must fail closed.
        sufficient = False
    if type(sufficient) is not bool or not sufficient:
        return _abstain("The cited evidence was not established as sufficient for this claim.")
    return VerificationResult(verdict, tuple(citations), rationale.strip())
