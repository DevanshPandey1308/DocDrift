"""Small deterministic Markdown grammar; unsupported prose is ignored."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from collections.abc import Iterable

from .models import Claim, ClaimType

_PATTERNS: tuple[tuple[ClaimType, re.Pattern[str]], ...] = (
    (ClaimType.ROUTE, re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+`([^`]+)`\s+(?:endpoint|route)\b", re.I)),
    (ClaimType.FUNCTION, re.compile(r"`?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\(([^()]*)\)`?\s+(?:accepts|takes|has parameters)\b", re.I)),
    (ClaimType.DEFAULT, re.compile(r"(?:the\s+)?default\s+(?:for\s+)?`?([A-Za-z_]\w*)`?\s+is\s+(`[^`]+`|[^.,;]+)", re.I)),
    (ClaimType.CLI_FLAG, re.compile(r"(?:(?:run|invoke|use)(?:\s+the)?\s+(?:CLI|command)(?:\s+with)?\s+)?(`--[A-Za-z][\w-]*)`", re.I)),
    (ClaimType.ENV_VAR, re.compile(r"(?:environment variable\s+)?`([A-Z][A-Z0-9_]*)`(?:\s+environment variable)?", re.I)),
    (ClaimType.FILE_PATH, re.compile(r"`((?:[\w.-]+/)+[\w.-]+|[\w.-]+\.py)`")),
    (ClaimType.PYTHON_VERSION, re.compile(r"requires\s+Python\s+([0-9]+(?:\.[0-9]+){0,2})\s+or newer", re.I)),
)
_PARAMS = re.compile(r"\s*\*{0,2}([A-Za-z_]\w*)\s*(?:=.*)?\s*")
_LITERAL = re.compile(r"^(?:None|True|False|-?\d+(?:\.\d+)?|['\"][^'\"]*['\"]|`(?:None|True|False|-?\d+(?:\.\d+)?|[^`]+)`)$")


def _expected(kind: ClaimType, match: re.Match[str]) -> object:
    if kind is ClaimType.FUNCTION:
        raw = match.group(2).strip()
        if not raw:
            params: list[str] = []
        else:
            params = []
            for part in raw.split(","):
                # This grammar does not encode calling convention or optionality.
                # Preserve uncertainty instead of silently stripping those markers.
                if "*" in part or "=" in part:
                    return {"parameters": None, "ambiguous": True}
                found = _PARAMS.fullmatch(part)
                if not found:
                    return {"parameters": None, "ambiguous": True}
                params.append(found.group(1))
        return {"parameters": params}
    if kind is ClaimType.DEFAULT:
        val = match.group(2).strip().strip("` ")
        if not _LITERAL.fullmatch(match.group(2).strip()):
            return {"value": val, "ambiguous": True}
        return {"value": val}
    if kind is ClaimType.PYTHON_VERSION:
        return {"version": match.group(1)}
    if kind is ClaimType.ROUTE:
        return {"method": match.group(1).upper()}
    return {}


def extract_markdown(text: str, document_path: str) -> list[Claim]:
    """Extract only documented supported patterns, excluding fenced blocks."""
    claims: list[Claim] = []
    fenced = False
    seen: set[tuple[str, int, str]] = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
            continue
        if fenced:
            continue
        for kind, pattern in _PATTERNS:
            for match in pattern.finditer(line):
                subject = match.group(1)
                if kind is ClaimType.CLI_FLAG:
                    subject = subject.strip("`")
                if kind is ClaimType.ROUTE:
                    subject = match.group(2)
                # Environment tokens are case-sensitive and all caps; ignore unrelated inline code.
                if kind is ClaimType.ENV_VAR and not re.fullmatch(r"[A-Z][A-Z0-9_]*", subject):
                    continue
                if kind is ClaimType.FILE_PATH:
                    subject = subject.replace("\\", "/")
                expected = _expected(kind, match)
                key = (kind.value, line_no, match.group(0))
                if key in seen:
                    continue
                seen.add(key)
                raw = f"{document_path}:{line_no}:{kind.value}:{subject}:{match.group(0)}"
                claim_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
                claims.append(Claim(claim_id, kind, subject, expected, line, document_path,
                                    line_no, match.start(), match.end()))
    return claims


def extract_documents(paths: Iterable[Path], root: Path) -> list[Claim]:
    claims: list[Claim] = []
    for path in sorted(paths):
        try:
            relative = path.relative_to(root).as_posix()
            claims.extend(extract_markdown(path.read_text(encoding="utf-8"), relative))
        except (OSError, UnicodeError):
            continue
    return claims
