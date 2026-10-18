"""Explicit opt-in orchestration for grounded fallback verification."""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from typing import Protocol

from .llm_client import (
    MAX_EVIDENCE_SNIPPET_CHARS,
    MAX_EVIDENCE_SNIPPETS,
    MAX_TOTAL_EVIDENCE_CHARS,
    VerificationRequest,
)
from .models import Claim, ClaimType, Evidence, Finding, Verdict
from .verification import (
    EvidenceCitation,
    VerificationResult,
    VerificationSnippet,
    VerificationVerdict,
)

LLM_ELIGIBLE_CLAIM_TYPES = frozenset(
    {
        ClaimType.FUNCTION,
        ClaimType.DEFAULT,
        ClaimType.CLI_FLAG,
        ClaimType.ENV_VAR,
        ClaimType.PYTHON_VERSION,
        ClaimType.ROUTE,
    }
)
_LLM_METHOD = "grounded_llm"


class GroundedVerificationClient(Protocol):
    """Client boundary that returns results already checked by the grounding contract."""

    def verify(self, request: VerificationRequest) -> Awaitable[VerificationResult]:
        """Verify one claim using only its bounded evidence request."""


def _bounded_evidence(
    existing: tuple[Evidence, ...], relevant: Sequence[Evidence]
) -> tuple[VerificationSnippet, ...]:
    """Keep ordered, caller-relevant repository evidence within provider request limits."""
    snippets: list[VerificationSnippet] = []
    seen: set[str] = set()
    used_chars = 0
    for item in (*existing, *relevant):
        if len(snippets) >= MAX_EVIDENCE_SNIPPETS or used_chars >= MAX_TOTAL_EVIDENCE_CHARS:
            break
        if not isinstance(item, Evidence) or not isinstance(item.snippet, str):
            continue
        remaining = MAX_TOTAL_EVIDENCE_CHARS - used_chars
        text = item.snippet[: min(MAX_EVIDENCE_SNIPPET_CHARS, remaining)]
        if not text.strip():
            continue
        try:
            snippet = VerificationSnippet(item.path, item.line, text)
        except (TypeError, ValueError):
            continue
        if snippet.reference in seen:
            continue
        seen.add(snippet.reference)
        snippets.append(snippet)
        used_chars += len(text)
    return tuple(snippets)


def _safe_abstention(
    claim: Claim, evidence: tuple[Evidence, ...], explanation: str
) -> Finding:
    return Finding(
        claim=claim,
        verdict=Verdict.UNVERIFIABLE,
        explanation=explanation,
        evidence=evidence,
        verification_method=_LLM_METHOD,
        deterministic=False,
    )


def _cited_evidence(
    result: VerificationResult,
    snippets: tuple[VerificationSnippet, ...],
) -> tuple[Evidence, ...] | None:
    """Resolve only exact citations back to the current request's evidence snippets."""
    if not isinstance(result.evidence_refs, tuple):
        return None
    supplied = {snippet.reference: snippet for snippet in snippets}
    cited: list[Evidence] = []
    seen: set[str] = set()
    for citation in result.evidence_refs:
        if not isinstance(citation, EvidenceCitation):
            return None
        snippet = supplied.get(citation.reference)
        if (
            snippet is None
            or citation.reference in seen
            or not isinstance(citation.quote, str)
            or not citation.quote.strip()
            or citation.quote not in snippet.text
        ):
            return None
        seen.add(citation.reference)
        cited.append(Evidence(snippet.path, snippet.line, snippet.text))
    if result.verdict in (VerificationVerdict.TRUE, VerificationVerdict.FALSE) and not cited:
        return None
    return tuple(cited)


async def orchestrate_verification(
    claim: Claim,
    deterministic_result: Finding,
    relevant_evidence: Sequence[Evidence],
    client: GroundedVerificationClient,
) -> Finding:
    """Use grounded LLM verification only for eligible deterministic abstentions.

    ``relevant_evidence`` must be selected by the repository analysis caller for this
    claim. The function performs no repository access. Deterministic conclusions and
    non-eligible or evidence-free claims are returned unchanged. The client is expected
    to return a ``VerificationResult`` accepted by the existing grounding validator.
    """
    if deterministic_result.verdict in (Verdict.TRUE, Verdict.FALSE):
        return deterministic_result
    if (
        deterministic_result.verdict is not Verdict.UNVERIFIABLE
        or not deterministic_result.deterministic
        or deterministic_result.claim != claim
        or not isinstance(claim.type, ClaimType)
        or claim.type not in LLM_ELIGIBLE_CLAIM_TYPES
    ):
        return deterministic_result

    snippets = _bounded_evidence(deterministic_result.evidence, relevant_evidence)
    if not snippets:
        return deterministic_result
    evidence_by_ref = {
        snippet.reference: Evidence(snippet.path, snippet.line, snippet.text)
        for snippet in snippets
    }
    try:
        request = VerificationRequest(claim, snippets)
    except (TypeError, ValueError):
        return deterministic_result

    try:
        result = await client.verify(request)
    except Exception:  # noqa: BLE001 - client failures must fail closed; cancellation propagates.
        return _safe_abstention(
            claim,
            tuple(evidence_by_ref.values()),
            "Grounded LLM verification failed; no verdict was established.",
        )

    if (
        not isinstance(result, VerificationResult)
        or not isinstance(result.verdict, VerificationVerdict)
        or not isinstance(result.rationale, str)
        or not result.rationale.strip()
    ):
        return _safe_abstention(
            claim,
            tuple(evidence_by_ref.values()),
            "Grounded LLM client returned an invalid result; no verdict was established.",
        )

    cited = _cited_evidence(result, snippets)
    if cited is None:
        return _safe_abstention(
            claim,
            tuple(evidence_by_ref.values()),
            "Grounded LLM client returned invalid evidence references; no verdict was established.",
        )
    if result.verdict is VerificationVerdict.ABSTAIN:
        return _safe_abstention(claim, cited or tuple(evidence_by_ref.values()), result.rationale.strip())

    mapped_verdict = (
        Verdict.TRUE if result.verdict is VerificationVerdict.TRUE else Verdict.FALSE
    )
    return Finding(
        claim=claim,
        verdict=mapped_verdict,
        explanation=result.rationale.strip(),
        evidence=cited,
        verification_method=_LLM_METHOD,
        deterministic=False,
    )
