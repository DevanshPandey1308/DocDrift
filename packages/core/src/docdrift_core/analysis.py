"""Repository analysis pipeline and reusable exact-name impact index."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .checkers import check_claim
from .extract import extract_documents
from .index import CodeIndex, build_index
from .models import AnalysisError, Claim, ClaimType, Finding


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


def select_impacted_claims(
    claims: list[Claim], index: CodeIndex, changed_paths: tuple[str, ...]
) -> list[Claim] | None:
    """Select claims linked to changed indexed source files, or return ``None`` to scan all.

    This intentionally supports only exact static symbol links. A missing path, unsupported
    file, index error, or empty mapping means impact is uncertain and the caller must retain
    the full-scan result.
    """
    if not changed_paths or index.errors:
        return None

    changed = set(changed_paths)
    if any(path not in index.files for path in changed):
        return None

    claim_index = build_impact_index(claims)
    claims_by_id = {claim.id: claim for claim in claims}
    impacted_ids: set[str] = set()
    for path in sorted(changed):
        symbols: list[tuple[str, frozenset[ClaimType]]] = []
        symbols.extend(
            (name, frozenset({ClaimType.FUNCTION, ClaimType.DEFAULT}))
            for name, items in index.functions.items()
            if any(item.path == path for item in items)
        )
        symbols.extend(
            (name, frozenset({ClaimType.CLI_FLAG}))
            for name, items in index.flags.items()
            if any(item.path == path for item in items)
        )
        symbols.extend(
            (name, frozenset({ClaimType.ENV_VAR}))
            for name, items in index.environment.items()
            if any(item.path == path for item in items)
        )
        symbols.extend(
            (name, frozenset({ClaimType.ROUTE}))
            for name, items in index.routes.items()
            if any(item.source_path == path for item in items)
        )

        path_claim_ids = set(claim_index.get(path, ()))
        path_claim_ids = {
            claim_id
            for claim_id in path_claim_ids
            if claims_by_id[claim_id].type is ClaimType.FILE_PATH
        }
        if not symbols and not path_claim_ids:
            return None
        impacted_ids.update(path_claim_ids)

        for symbol, claim_types in symbols:
            symbol_claim_ids = {
                claim_id
                for claim_id in claim_index.get(symbol, ())
                if claims_by_id[claim_id].type in claim_types
            }
            # An indexed symbol with no exact documentation link may have a loose prose
            # reference; until fallback retrieval exists, retain the full-scan result.
            if not symbol_claim_ids:
                return None
            impacted_ids.update(symbol_claim_ids)

    impacted = [claim for claim in claims if claim.id in impacted_ids]
    return impacted or None


def analyze(
    root: Path,
    docs_dirs: tuple[str, ...] = (".",),
    source_dirs: tuple[str, ...] = (".",),
    *,
    changed_paths: tuple[str, ...] | None = None,
) -> tuple[list[Finding], CodeIndex, list[Claim]]:
    root = root.resolve()
    claims = extract_documents(discover_markdown(root, docs_dirs), root)
    index = build_index(root, source_dirs)
    for scope in docs_dirs:
        candidate = (root / scope).resolve()
        if not candidate.is_relative_to(root) or not candidate.exists():
            index.errors.append(AnalysisError(scope, "documentation scope is missing or escapes repository", kind="configuration_error"))
    selected_claims = (
        select_impacted_claims(claims, index, changed_paths)
        if changed_paths is not None
        else None
    )
    findings = [check_claim(claim, index) for claim in (selected_claims or claims)]
    findings.sort(key=lambda f: (f.claim.document_path, f.claim.line, f.claim.span_start, f.claim.id))
    return findings, index, claims
