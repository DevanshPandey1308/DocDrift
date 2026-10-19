"""Read-only, bounded application boundary for local MCP tools."""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import uuid4

from docdrift_core import analyze
from docdrift_core.models import AnalysisError, Finding

MAX_SCOPES = 32
MAX_SCOPE_LENGTH = 500
MAX_PAGE_SIZE = 50
MAX_EVIDENCE_ITEMS = 20
MAX_INLINE_EVIDENCE = 3
MAX_INLINE_SNIPPET_LENGTH = 400
MAX_TEXT_LENGTH = 2_000
MAX_SNIPPET_LENGTH = 1_000
MAX_CACHED_CHECKS = 8
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b([\w-]*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|"
    r"private[_-]?key|credentials?))\b\s*([=:])\s*(?:['\"][^'\"]*['\"]|[^\s,;]+)"
)
_PRIVATE_KEY_HEADER = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE)


def _safe_snippet(text: str, limit: int) -> tuple[str, bool, bool]:
    """Bound evidence and hide obvious credential assignments/private-key blocks."""
    if _SENSITIVE_ASSIGNMENT.search(text) or _PRIVATE_KEY_HEADER.search(text):
        return "[REDACTED sensitive-looking source line]", False, True
    return text[:limit], len(text) > limit, False


def _evidence_summary(path: str, line: int, text: str, limit: int) -> dict[str, Any]:
    snippet, truncated, redacted = _safe_snippet(text, limit)
    return {
        "path": path,
        "line": line,
        "snippet": snippet,
        "truncated": truncated,
        "redacted": redacted,
    }


@dataclass(frozen=True, slots=True)
class _Snapshot:
    findings: tuple[Finding, ...]
    errors: tuple[AnalysisError, ...]


class MCPService:
    """Validate local inputs and expose bounded results from the existing core."""

    def __init__(self) -> None:
        self._checks: OrderedDict[str, _Snapshot] = OrderedDict()

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {"ok": False, "error_code": code, "message": message}

    @staticmethod
    def _repo_root(repo_path: str) -> Path | None:
        if not repo_path or len(repo_path) > MAX_SCOPE_LENGTH:
            return None
        try:
            root = Path(repo_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return None
        return root if root.is_dir() else None

    @staticmethod
    def _scopes(root: Path, values: list[str] | None) -> tuple[str, ...] | None:
        if values is None:
            return (".",)
        if not values or len(values) > MAX_SCOPES:
            return None
        normalized: list[str] = []
        for value in values:
            if not value or len(value) > MAX_SCOPE_LENGTH:
                return None
            posix = PurePosixPath(value)
            windows = PureWindowsPath(value)
            if posix.is_absolute() or windows.is_absolute() or windows.drive:
                return None
            if ".." in posix.parts or ".." in windows.parts:
                return None
            try:
                candidate = (root / value).resolve(strict=True)
            except (OSError, RuntimeError, ValueError):
                return None
            if not candidate.is_relative_to(root):
                return None
            if not candidate.is_file() and not candidate.is_dir():
                return None
            if candidate.is_dir():
                try:
                    for descendant in candidate.rglob("*"):
                        if descendant.is_symlink() and not descendant.resolve(strict=True).is_relative_to(root):
                            return None
                except (OSError, RuntimeError, ValueError):
                    return None
            normalized.append(candidate.relative_to(root).as_posix() or ".")
        return tuple(normalized)

    def check_repository(
        self,
        repo_path: str,
        docs_dirs: list[str] | None = None,
        source_dirs: list[str] | None = None,
    ) -> dict[str, Any]:
        root = self._repo_root(repo_path)
        if root is None:
            return self._error("invalid_repository", "Repository path must name an accessible directory.")
        docs = self._scopes(root, docs_dirs)
        sources = self._scopes(root, source_dirs)
        if docs is None or sources is None:
            return self._error(
                "invalid_scope",
                "Scopes must be existing repository-relative paths that resolve within the repository.",
            )
        try:
            findings, index, _ = analyze(root, docs, sources)
        except Exception:  # noqa: BLE001 - keep unexpected core failures predictable at the adapter boundary.
            return self._error("analysis_failed", "Repository analysis failed; no result was retained.")

        check_id = uuid4().hex
        snapshot = _Snapshot(tuple(findings), tuple(index.errors))
        self._checks[check_id] = snapshot
        self._checks.move_to_end(check_id)
        while len(self._checks) > MAX_CACHED_CHECKS:
            self._checks.popitem(last=False)
        return {
            "ok": True,
            "error_code": None,
            "message": "Deterministic local analysis completed.",
            "check_id": check_id,
            "finding_count": len(findings),
            "true_count": sum(item.verdict.value == "true" for item in findings),
            "false_count": sum(item.verdict.value == "false" for item in findings),
            "unverifiable_count": sum(item.verdict.value == "unverifiable" for item in findings),
            "error_count": len(index.errors),
        }

    def get_findings(
        self, check_id: str, offset: int = 0, limit: int = 25
    ) -> dict[str, Any]:
        snapshot = self._checks.get(check_id)
        if snapshot is None:
            return self._error("check_not_found", "Check result is unavailable or has expired.")
        if offset < 0 or limit < 1 or limit > MAX_PAGE_SIZE:
            return self._error("invalid_pagination", f"Use offset >= 0 and limit from 1 to {MAX_PAGE_SIZE}.")
        end = min(offset + limit, len(snapshot.findings))
        findings = [self._finding(item) for item in snapshot.findings[offset:end]]
        return {
            "ok": True,
            "error_code": None,
            "message": f"Returned findings {offset} through {end} of {len(snapshot.findings)}.",
            "check_id": check_id,
            "total": len(snapshot.findings),
            "offset": offset,
            "findings": findings,
            "errors": [self._analysis_error(error) for error in snapshot.errors[:MAX_EVIDENCE_ITEMS]],
            "errors_truncated": len(snapshot.errors) > MAX_EVIDENCE_ITEMS,
        }

    def inspect_finding_evidence(self, check_id: str, finding_id: str) -> dict[str, Any]:
        snapshot = self._checks.get(check_id)
        if snapshot is None:
            return self._error("check_not_found", "Check result is unavailable or has expired.")
        finding = next((item for item in snapshot.findings if item.claim.id == finding_id), None)
        if finding is None:
            return self._error("finding_not_found", "Finding does not belong to this check.")
        evidence = []
        for item in finding.evidence[:MAX_EVIDENCE_ITEMS]:
            evidence.append(_evidence_summary(item.path, item.line, item.snippet, MAX_SNIPPET_LENGTH))
        return {
            "ok": True,
            "error_code": None,
            "message": finding.explanation[:MAX_TEXT_LENGTH],
            "check_id": check_id,
            "finding_id": finding_id,
            "claim": self._finding(finding),
            "evidence": evidence,
            "evidence_truncated": len(finding.evidence) > MAX_EVIDENCE_ITEMS,
        }

    @staticmethod
    def _finding(finding: Finding) -> dict[str, Any]:
        evidence = [
            _evidence_summary(item.path, item.line, item.snippet, MAX_INLINE_SNIPPET_LENGTH)
            for item in finding.evidence[:MAX_INLINE_EVIDENCE]
        ]
        return {
            "finding_id": finding.claim.id,
            "verdict": finding.verdict.value,
            "claim_type": finding.claim.type.value,
            "claim": finding.claim.text[:MAX_TEXT_LENGTH],
            "claim_truncated": len(finding.claim.text) > MAX_TEXT_LENGTH,
            "document_path": finding.claim.document_path,
            "line": finding.claim.line,
            "explanation": finding.explanation[:MAX_TEXT_LENGTH],
            "verification_method": finding.verification_method,
            "severity": None,
            "evidence": evidence,
            "evidence_truncated": len(finding.evidence) > MAX_INLINE_EVIDENCE,
        }

    @staticmethod
    def _analysis_error(error: AnalysisError) -> dict[str, Any]:
        return {
            "path": error.path[:MAX_TEXT_LENGTH],
            "line": error.line,
            "column": error.column,
            "kind": error.kind,
            "message": (
                error.message[:MAX_TEXT_LENGTH]
                if error.kind == "syntax_error"
                else "The repository file or scope could not be analyzed."
            ),
        }
