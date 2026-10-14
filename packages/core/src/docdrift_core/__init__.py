"""DocDrift deterministic analysis core."""

from .analysis import analyze, build_impact_index
from .extract import extract_markdown
from .index import build_index
from .models import AnalysisError, Claim, ClaimType, Evidence, Finding, Verdict

__all__ = ["AnalysisError", "Claim", "ClaimType", "Evidence", "Finding", "Verdict", "analyze", "build_impact_index", "build_index", "extract_markdown"]
