"""Repository analysis pipeline and reusable exact-name impact index."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .checkers import check_claim
from .extract import extract_documents
from .index import CodeIndex, build_index
from .models import AnalysisError, Claim, Finding


def discover_markdown(root: Path, docs_dirs: tuple[str, ...]) -> list[Path]:
    paths: list[Path] = []
    for scope in docs_dirs:
        base = (root / scope).resolve()
        if not base.is_relative_to(root.resolve()) or not base.exists():
            continue
        if base.is_file() and base.suffix.lower() in {".md", ".markdown"}:
            paths.append(base)
        elif base.is_dir():
            paths.extend(p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".markdown"} and not any(part in {".git", ".venv", "node_modules"} for part in p.parts))
    return sorted(set(paths))


def build_impact_index(claims: list[Claim]) -> dict[str, tuple[str, ...]]:
    """Map exact claim subjects to stable claim IDs for later changed-symbol selection."""
    result: dict[str, list[str]] = defaultdict(list)
    for claim in claims:
        result[claim.subject].append(claim.id)
    return {key: tuple(sorted(set(value))) for key, value in sorted(result.items())}


def analyze(root: Path, docs_dirs: tuple[str, ...] = (".",), source_dirs: tuple[str, ...] = (".",)) -> tuple[list[Finding], CodeIndex, list[Claim]]:
    root = root.resolve()
    claims = extract_documents(discover_markdown(root, docs_dirs), root)
    index = build_index(root, source_dirs)
    for scope in docs_dirs:
        candidate = (root / scope).resolve()
        if not candidate.is_relative_to(root) or not candidate.exists():
            index.errors.append(AnalysisError(scope, "documentation scope is missing or escapes repository", kind="configuration_error"))
    findings = [check_claim(claim, index) for claim in claims]
    findings.sort(key=lambda f: (f.claim.document_path, f.claim.line, f.claim.span_start, f.claim.id))
    return findings, index, claims
