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
from .orchestration import (
    LLM_ELIGIBLE_CLAIM_TYPES,
    GroundedVerificationClient,
    orchestrate_verification,
)
from .patches import (
    DocumentationPatch,
    PatchLimits,
    PatchValidationError,
    create_claim_patch,
    create_span_patch,
    create_unique_text_patch,
)
from .verification import (
    EvidenceCitation,
    EvidenceSufficiencyGate,
    VerificationResult,
    VerificationSnippet,
    VerificationVerdict,
    validate_verification_output,
)

__all__ = [
    "LLM_ELIGIBLE_CLAIM_TYPES",
    "AnalysisError",
    "Claim",
    "ClaimType",
    "DocumentationPatch",
    "Evidence",
    "EvidenceCitation",
    "EvidenceSufficiencyGate",
    "Finding",
    "GroundedLLMClient",
    "GroundedVerificationClient",
    "LLMProvider",
    "PatchLimits",
    "PatchValidationError",
    "Verdict",
    "VerificationRequest",
    "VerificationResult",
    "VerificationSnippet",
    "VerificationVerdict",
    "adapt_provider_response",
    "analyze",
    "build_impact_index",
    "build_index",
    "create_claim_patch",
    "create_span_patch",
    "create_unique_text_patch",
    "extract_markdown",
    "orchestrate_verification",
    "select_impacted_claims",
    "validate_verification_output",
]
