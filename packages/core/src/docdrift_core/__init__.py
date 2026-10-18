"""DocDrift deterministic analysis core."""

from .analysis import analyze, build_impact_index, select_impacted_claims
from .extract import extract_markdown
from .index import build_index
from .llm_client import (
    GroundedLLMClient,
    LLMProvider,
    VerificationRequest,
    adapt_provider_response,
)
from .models import AnalysisError, Claim, ClaimType, Evidence, Finding, Verdict
from .verification import (
    EvidenceCitation,
    EvidenceSufficiencyGate,
    VerificationResult,
    VerificationSnippet,
    VerificationVerdict,
    validate_verification_output,
)

__all__ = [
    "AnalysisError",
    "Claim",
    "ClaimType",
    "Evidence",
    "EvidenceCitation",
    "EvidenceSufficiencyGate",
    "Finding",
    "GroundedLLMClient",
    "LLMProvider",
    "Verdict",
    "VerificationRequest",
    "VerificationResult",
    "VerificationSnippet",
    "VerificationVerdict",
    "adapt_provider_response",
    "analyze",
    "build_impact_index",
    "build_index",
    "extract_markdown",
    "select_impacted_claims",
    "validate_verification_output",
]
