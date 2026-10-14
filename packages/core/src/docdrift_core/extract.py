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
_TUTORIAL_FILE_INSTRUCTIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:[-*+]\s+)?(?:now\s+)?create\s+(?:(?:a|an|another|the|new)\s+)?(?:empty\s+|demo\s+)?(?:file|module)(?:/module)?\s+`([^`]+)`(?:\s+(?:with|containing)\b.*|[.:]?\s*$)", re.I),
    re.compile(r"^\s*(?:[-*+]\s+)?(?:now\s+)?create\s+(?:(?:a|an|another|new)\s+)?`([^`]+)`(?:\s+(?:file|module))?(?:\s+(?:with|containing|in|inside|under)\b.*|[.:]?\s*$)", re.I),
    re.compile(r"^\s*(?:finally,\s*)?we need to create\s+(?:a|an|the|new)?\s*`([^`]+)`\s+(?:file|module)(?:\s+(?:in|inside|under)\b.*|[.:]?\s*$)", re.I),
    re.compile(r"^\s*(?:and\s+)?let'?s start with a file\s+`([^`]+)`\s*:\s*$", re.I),
    re.compile(r"^\s*(?:and\s+)?now the equivalent\b.*\bin\s+`([^`]+)`\s*:\s*$", re.I),
    re.compile(r"^\s*now let'?s put\b.{0,180}\btogether\b.{0,120}\bin\s+`([^`]+)`\s*:\s*$", re.I),
    re.compile(r"^\s*(?:[-*+]\s+)?(?:now\s+)?(?:modify|update|edit)\s+(?:the\s+)?file\s+`([^`]+)`(?:\s+(?:with|to)\b.*|[.!:]?\s*$)", re.I),
    re.compile(r"^\s*but to achieve that, we just have to add\b.{0,180}\bto the same\s+`([^`]+)`\s+file we already had\s*:\s*$", re.I),
    re.compile(r"^\s*(?:[-*+]\s+)?(?:and then|now|then)\s+update\s+(?:your|the)\s+`([^`]+)`\s+with\s*:\s*$", re.I),
    re.compile(r"^\s*(?:[-*+]\s+)?now\s+let'?s\s+start\b.{0,180}?,\s*(?:update|modify|edit)\s+(?:the\s+)?(?:file\s+)?`([^`]+)`\s+with\s*:\s*$", re.I),
    re.compile(r"^\s*(?:[-*+]\s+)?(?:now\s+)?you could create\b.{0,160}?\bfor example\s+`([^`]+)`\s*[:.]?\s*$", re.I),
    re.compile(r"^\s*(?:[-*+]\s+)?(?:then\s+)?you can write a template at\s+`([^`]+)`\s+with,\s+for example\s*:\s*$", re.I),
    re.compile(r"^\s*(?:to run any of the examples,\s*)?copy\s+(?:that(?:\s+example(?:\s+from\s+above)?)?|the code|the example)\s+to a file\s+`([^`]+)`(?:,\s*and\s+(?:run|start|execute)\b.*)?[.:]?\s*$", re.I),
    re.compile(r"^\s*here's what we do in\s+`([^`]+)`\s*:\s*$", re.I),
)
_TUTORIAL_SIBLING_FILES = re.compile(r"^\s*the file would live right beside\b.*:\s*$", re.I)


def _is_tutorial_file_instruction(line: str, path: str) -> bool:
    """Recognize only explicit file creation/edit tutorial instruction forms."""
    if _TUTORIAL_SIBLING_FILES.search(line):
        return path in re.findall(r"`([^`]+)`", line)
    return any(match.group(1) == path for pattern in _TUTORIAL_FILE_INSTRUCTIONS if (match := pattern.search(line)))


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
                    if _is_tutorial_file_instruction(line, match.group(1)):
                        continue
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
