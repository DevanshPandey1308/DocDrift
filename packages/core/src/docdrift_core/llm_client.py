"""Provider-independent asynchronous boundary for grounded LLM verification."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .models import Claim
from .verification import (
    EvidenceSufficiencyGate,
    VerificationResult,
    VerificationSnippet,
    VerificationVerdict,
    validate_verification_output,
)

MAX_CLAIM_TEXT_CHARS = 4_000
MAX_EVIDENCE_SNIPPETS = 16
MAX_EVIDENCE_SNIPPET_CHARS = 4_000
MAX_TOTAL_EVIDENCE_CHARS = 16_000
MAX_PROVIDER_TIMEOUT_SECONDS = 60.0
ProviderResponse = str | Mapping[str, object]


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    """A claim and bounded repository evidence supplied to one provider call."""

    claim: Claim
    evidence: tuple[VerificationSnippet, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.claim, Claim):
            raise TypeError("verification request requires a core Claim")
        if not isinstance(self.evidence, tuple):
            raise TypeError("verification evidence must be an immutable tuple")
        if len(self.claim.text) > MAX_CLAIM_TEXT_CHARS:
            raise ValueError("claim text exceeds the provider request bound")
        if len(self.evidence) > MAX_EVIDENCE_SNIPPETS:
            raise ValueError("verification request contains too many evidence snippets")
        if any(not isinstance(item, VerificationSnippet) for item in self.evidence):
            raise TypeError("verification request evidence must use VerificationSnippet")
        if any(len(item.text) > MAX_EVIDENCE_SNIPPET_CHARS for item in self.evidence):
            raise ValueError("an evidence snippet exceeds the provider request bound")
        if sum(len(item.text) for item in self.evidence) > MAX_TOTAL_EVIDENCE_CHARS:
            raise ValueError("total evidence text exceeds the provider request bound")
        references = [item.reference for item in self.evidence]
        if len(references) != len(set(references)):
            raise ValueError("verification request evidence references must be unique")


class LLMProvider(Protocol):
    """Minimal provider contract; implementations must return structured JSON output."""

    async def verify(self, request: VerificationRequest) -> ProviderResponse:
        """Return a JSON object or its JSON text representation for this request."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def adapt_provider_response(
    claim: Claim,
    response: object,
    evidence: tuple[VerificationSnippet, ...],
    *,
    evidence_sufficient: EvidenceSufficiencyGate,
) -> VerificationResult:
    """Decode strict JSON, then delegate all verdict/citation acceptance to core validation."""
    if isinstance(response, str):
        try:
            payload: object = json.loads(
                response,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonstandard_constant,
            )
        except (json.JSONDecodeError, ValueError, RecursionError):
            return VerificationResult(
                VerificationVerdict.ABSTAIN, (), "Provider returned malformed structured JSON."
            )
    elif isinstance(response, Mapping):
        payload = response
    else:
        return VerificationResult(
            VerificationVerdict.ABSTAIN,
            (),
            "Provider returned an unsupported structured response type.",
        )

    return validate_verification_output(
        claim,
        payload,
        evidence,
        evidence_sufficient=evidence_sufficient,
    )


class GroundedLLMClient:
    """Invoke a provider under a bounded timeout and fail closed on provider errors."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        evidence_sufficient: EvidenceSufficiencyGate,
        timeout_seconds: float,
    ) -> None:
        if not callable(evidence_sufficient):
            raise TypeError("a trusted evidence-sufficiency gate is required")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > MAX_PROVIDER_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"provider timeout must be greater than zero and at most {MAX_PROVIDER_TIMEOUT_SECONDS:g} seconds"
            )
        self._provider = provider
        self._evidence_sufficient = evidence_sufficient
        self._timeout_seconds = float(timeout_seconds)

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        """Return a validated result; provider errors and timeouts become abstentions.

        ``asyncio.CancelledError`` is intentionally not caught, so caller cancellation
        retains normal asyncio semantics.
        """
        if not request.evidence:
            return VerificationResult(
                VerificationVerdict.ABSTAIN, (), "No repository evidence was supplied for this claim."
            )
        try:
            response = await asyncio.wait_for(
                self._provider.verify(request), timeout=self._timeout_seconds
            )
        except TimeoutError:
            return VerificationResult(
                VerificationVerdict.ABSTAIN, (), "LLM provider timed out; verdict withheld."
            )
        except Exception:  # noqa: BLE001 - never turn a provider error into a verdict.
            # Provider exception strings can contain credentials or request details.
            return VerificationResult(
                VerificationVerdict.ABSTAIN, (), "LLM provider failed; verdict withheld."
            )
        return adapt_provider_response(
            request.claim,
            response,
            request.evidence,
            evidence_sufficient=self._evidence_sufficient,
        )
