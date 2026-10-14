"""Stable data models shared by DocDrift adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ClaimType(StrEnum):
    FUNCTION = "function_signature"
    DEFAULT = "default_value"
    CLI_FLAG = "cli_flag"
    ENV_VAR = "environment_variable"
    FILE_PATH = "file_path"
    PYTHON_VERSION = "python_version"
    ROUTE = "route"


class Verdict(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True, slots=True)
class Claim:
    id: str
    type: ClaimType
    subject: str
    expected: Any
    text: str
    document_path: str
    line: int
    span_start: int
    span_end: int


@dataclass(frozen=True, slots=True)
class Evidence:
    path: str
    line: int
    snippet: str


@dataclass(frozen=True, slots=True)
class Finding:
    claim: Claim
    verdict: Verdict
    explanation: str
    evidence: tuple[Evidence, ...]
    verification_method: str
    deterministic: bool = True


@dataclass(frozen=True, slots=True)
class AnalysisError:
    path: str
    message: str
    line: int | None = None
    column: int | None = None
    kind: str = "analysis_error"


def to_dict(value: Any) -> Any:
    """Convert nested model values to JSON-ready stable values."""
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_dict(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_dict(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_dict(item) for item in value]
    return value
