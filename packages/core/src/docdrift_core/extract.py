"""Small deterministic Markdown grammar; unsupported prose is ignored."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from pathlib import Path

from .models import Claim, ClaimType

_PATTERNS: tuple[tuple[ClaimType, re.Pattern[str]], ...] = (
    (ClaimType.ROUTE, re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+`([^`]+)`\s+(?:endpoint|route)\b", re.IGNORECASE)),
    (ClaimType.FUNCTION, re.compile(r"`?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\(([^()]*)\)`?\s+(?:accepts|takes|has parameters)\b", re.IGNORECASE)),
    (ClaimType.DEFAULT, re.compile(r"(?:the\s+)?default\s+(?:for\s+)?`?([A-Za-z_]\w*)`?\s+is\s+(`[^`]+`|[^.,;]+)", re.IGNORECASE)),
    (ClaimType.CLI_FLAG, re.compile(r"(?:(?:run|invoke|use)(?:\s+the)?\s+(?:CLI|command)(?:\s+with)?\s+)?`(--[A-Za-z][\w-]*|-h|-V)`", re.IGNORECASE)),
    (ClaimType.ENV_VAR, re.compile(r"(?:environment variable\s+)?`([A-Z][A-Z0-9_]*)`(?:\s+environment variable)?", re.IGNORECASE)),
    (ClaimType.FILE_PATH, re.compile(r"`([^`]+)`")),
    (ClaimType.PYTHON_VERSION, re.compile(r"requires\s+Python\s+([0-9]+(?:\.[0-9]+){0,2})\s+or newer", re.IGNORECASE)),
)
_PARAMS = re.compile(r"\s*\*{0,2}([A-Za-z_]\w*)\s*(?:=.*)?\s*")
_LITERAL = re.compile(r"^(?:None|True|False|-?\d+(?:\.\d+)?|['\"][^'\"]*['\"]|`(?:None|True|False|-?\d+(?:\.\d+)?|[^`]+)`)$")
_UNIT_VALUE = re.compile(
    r"^\s*(`?-?\d+(?:\.\d+)?`?)\s+(seconds?|s|ms|milliseconds?|minutes?|hours?|bytes?|KB|MB)\s*$",
    re.IGNORECASE,
)
_INLINE_DEFAULT = re.compile(
    r"`([A-Za-z_]\w*)`\s*(?:\(\s*default\s*:?[ \t]*([^)]*)\)|defaults?\s+to\s+([^.,;]+))",
    re.IGNORECASE,
)
_CALL_SIGNATURE = re.compile(r"`([A-Za-z_]\w*)\(([^()]*)\)`")
_VERSION_FORMS = re.compile(
    r"\bPython\s*(?:>=\s*(\d+\.\d+)|(\d+\.\d+)\s*(?:\+|or\s+(?:higher|later)))(?!\w|\.\d)",
    re.IGNORECASE,
)
_TABLE_IDENTIFIER = re.compile(r"^`([A-Za-z_]\w*)`$")
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")
_PATH_EXTENSION = re.compile(r"\.[A-Za-z0-9_-]+$")
_NOISE_PATHS = {"n/a", "i/o", "and/or", "w/o", "tcp/ip"}
_TUTORIAL_FILE_INSTRUCTIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:[-*+]\s+)?(?:now\s+)?create\s+(?:(?:a|an|another|the|new)\s+)?(?:empty\s+|demo\s+)?(?:file|module)(?:/module)?\s+`([^`]+)`(?:\s+(?:with|containing)\b.*|[.:]?\s*$)", re.IGNORECASE),
    re.compile(r"^\s*(?:[-*+]\s+)?(?:now\s+)?create\s+(?:(?:a|an|another|new)\s+)?`([^`]+)`(?:\s+(?:file|module))?(?:\s+(?:with|containing|in|inside|under)\b.*|[.:]?\s*$)", re.IGNORECASE),
    re.compile(r"^\s*(?:finally,\s*)?we need to create\s+(?:a|an|the|new)?\s*`([^`]+)`\s+(?:file|module)(?:\s+(?:in|inside|under)\b.*|[.:]?\s*$)", re.IGNORECASE),
    re.compile(r"^\s*(?:and\s+)?let'?s start with a file\s+`([^`]+)`\s*:\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:and\s+)?now the equivalent\b.*\bin\s+`([^`]+)`\s*:\s*$", re.IGNORECASE),
    re.compile(r"^\s*now let'?s put\b.{0,180}\btogether\b.{0,120}\bin\s+`([^`]+)`\s*:\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:[-*+]\s+)?(?:now\s+)?(?:modify|update|edit)\s+(?:the\s+)?file\s+`([^`]+)`(?:\s+(?:with|to)\b.*|[.!:]?\s*$)", re.IGNORECASE),
    re.compile(r"^\s*but to achieve that, we just have to add\b.{0,180}\bto the same\s+`([^`]+)`\s+file we already had\s*:\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:[-*+]\s+)?(?:and then|now|then)\s+update\s+(?:your|the)\s+`([^`]+)`\s+with\s*:\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:[-*+]\s+)?now\s+let'?s\s+start\b.{0,180}?,\s*(?:update|modify|edit)\s+(?:the\s+)?(?:file\s+)?`([^`]+)`\s+with\s*:\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:[-*+]\s+)?(?:now\s+)?you could create\b.{0,160}?\bfor example\s+`([^`]+)`\s*[:.]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:[-*+]\s+)?(?:then\s+)?you can write a template at\s+`([^`]+)`\s+with,\s+for example\s*:\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:to run any of the examples,\s*)?copy\s+(?:that(?:\s+example(?:\s+from\s+above)?)?|the code|the example)\s+to a file\s+`([^`]+)`(?:,\s*and\s+(?:run|start|execute)\b.*)?[.:]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*here's what we do in\s+`([^`]+)`\s*:\s*$", re.IGNORECASE),
)
_TUTORIAL_SIBLING_FILES = re.compile(r"^\s*the file would live right beside\b.*:\s*$", re.IGNORECASE)
_THIRD_PARTY_COMMAND_FLAG = re.compile(
    r"`(?:pytest|pip|docker|git|npm)\b[^`]*?((?:--[A-Za-z][\w-]*|-h|-V))[^`]*`",
    re.IGNORECASE,
)


def _is_tutorial_file_instruction(line: str, path: str) -> bool:
    """Recognize only explicit file creation/edit tutorial instruction forms."""
    if _TUTORIAL_SIBLING_FILES.search(line):
        return path in re.findall(r"`([^`]+)`", line)
    return any(match.group(1) == path for pattern in _TUTORIAL_FILE_INSTRUCTIONS if (match := pattern.search(line)))


def _is_path_like(token: str, root: Path | None) -> bool:
    normalized = token.replace("\\", "/")
    if (not normalized or normalized.casefold() in _NOISE_PATHS
            or "://" in normalized or any(char.isspace() or char in "*<>{}" for char in normalized)):
        return False
    if "/" not in normalized:
        # Preserve the existing explicit .py filename grammar.
        return normalized.lower().endswith(".py") and normalized.lower() != ".py"
    first = normalized.split("/", 1)[0]
    return bool(
        _PATH_EXTENSION.search(normalized.rstrip("/"))
        or normalized.endswith("/")
        or (root is not None and (root / first).is_dir())
    )


def _sentence_has_environment_context(line: str, start: int, end: int) -> bool:
    left = max(line.rfind(mark, 0, start) for mark in ".!?") + 1
    right_candidates = [line.find(mark, end) for mark in ".!?" if line.find(mark, end) >= 0]
    right = min(right_candidates) if right_candidates else len(line)
    sentence = line[left:right]
    return bool(re.search(r"\b(?:environment\s+variable|env\s+var)\b|\benv\s*:", sentence, re.IGNORECASE))


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
        unit = _UNIT_VALUE.fullmatch(val)
        if unit:
            return {"value": unit.group(1).strip("`"), "unit": unit.group(2)}
        if not _LITERAL.fullmatch(match.group(2).strip()):
            return {"value": val, "ambiguous": True}
        return {"value": val}
    if kind is ClaimType.PYTHON_VERSION:
        return {"version": match.group(1)}
    if kind is ClaimType.ROUTE:
        return {"method": match.group(1).upper()}
    return {}


def extract_markdown(text: str, document_path: str, root: Path | None = None) -> list[Claim]:
    """Extract only documented supported patterns, excluding fenced blocks."""
    claims: list[Claim] = []
    fenced = False
    seen: set[tuple[str, int, str]] = set()
    table_header: list[str] | None = None
    table_separator_pending = False

    def add_claim(kind: ClaimType, subject: str, expected: object, line: str,
                  line_no: int, start: int, end: int, match_text: str) -> None:
        key = (kind.value, line_no, match_text)
        if key in seen:
            return
        seen.add(key)
        raw = f"{document_path}:{line_no}:{kind.value}:{subject}:{match_text}"
        claim_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        claims.append(Claim(claim_id, kind, subject, expected, line, document_path,
                            line_no, start, end))

    for line_no, line in enumerate(text.splitlines(), 1):
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
            table_header = None
            table_separator_pending = False
            continue
        if fenced:
            continue

        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if table_header is not None and "|" in line:
            if all(_TABLE_SEPARATOR.fullmatch(cell) for cell in cells):
                if table_separator_pending:
                    table_separator_pending = False
                else:
                    table_header = None
                continue
            is_new_header = (
                len(cells) >= 2
                and cells[0].strip("` ").casefold() in {"name", "parameter", "option"}
            )
            if is_new_header:
                table_header = None
                table_separator_pending = False
            elif table_separator_pending:
                table_header = None
            elif not table_header:
                continue
            elif len(cells) == len(table_header):
                first = _TABLE_IDENTIFIER.fullmatch(cells[0])
                default_value = cells[table_header.index("default")].strip("` ")
                if first and default_value.casefold() not in {"", "-", "—", "required", "n/a"}:
                    value_match = _UNIT_VALUE.fullmatch(default_value)
                    value = value_match.group(1).strip("`") if value_match else default_value
                    table_expected: dict[str, object] = {"value": value}
                    if value_match:
                        table_expected["unit"] = value_match.group(2)
                    elif not _LITERAL.fullmatch(default_value):
                        table_expected["ambiguous"] = True
                    add_claim(ClaimType.DEFAULT, first.group(1), table_expected, line,
                              line_no, 0, len(line), f"table-default:{first.group(1)}")
                continue
            else:
                table_header = None
        elif table_header is not None:
            table_header = None
            table_separator_pending = False

        if "|" in line:
            header_cells = [cell.strip().casefold() for cell in cells]
            if len(header_cells) >= 2:
                table_header = header_cells if "default" in header_cells else []
                table_separator_pending = True

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
                if kind is ClaimType.ENV_VAR and "_" not in subject and not _sentence_has_environment_context(line, match.start(), match.end()):
                    continue
                if kind is ClaimType.FILE_PATH:
                    subject = subject.replace("\\", "/")
                    if not _is_path_like(subject, root):
                        continue
                    if _is_tutorial_file_instruction(line, match.group(1)):
                        continue
                expected = _expected(kind, match)
                add_claim(kind, subject, expected, line, line_no, match.start(),
                          match.end(), match.group(0))

        for match in _THIRD_PARTY_COMMAND_FLAG.finditer(line):
            add_claim(ClaimType.CLI_FLAG, match.group(1), {}, line, line_no,
                      match.start(), match.end(), match.group(0))

        for match in _INLINE_DEFAULT.finditer(line):
            raw_value = (match.group(2) or match.group(3) or "").strip()
            value = raw_value.strip("` ")
            span_end = match.end()
            if match.group(3) and re.fullmatch(r"-?\d+", value):
                suffix = re.match(r",\d{3}(?:,\d{3})*(?!\d)", line[span_end:])
                if suffix:
                    value += suffix.group(0)
                    span_end += len(suffix.group(0))
            unit_match = _UNIT_VALUE.fullmatch(value)
            expected = {"value": unit_match.group(1).strip("`") if unit_match else value}
            if unit_match:
                expected["unit"] = unit_match.group(2)
            elif not _LITERAL.fullmatch(raw_value):
                expected["ambiguous"] = True
            add_claim(ClaimType.DEFAULT, match.group(1), expected, line, line_no,
                      match.start(), span_end, match.group(0))

        for match in _CALL_SIGNATURE.finditer(line):
            for parameter in match.group(2).split(","):
                name_value = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*=\s*(.*?)\s*", parameter)
                if not name_value:
                    continue
                raw_value = name_value.group(2)
                if not _LITERAL.fullmatch(raw_value):
                    continue
                add_claim(
                    ClaimType.DEFAULT,
                    name_value.group(1),
                    {"value": raw_value.strip("`"), "function": match.group(1)},
                    line,
                    line_no,
                    match.start(),
                    match.end(),
                    f"{match.group(0)}:{name_value.group(1)}",
                )

        for match in _VERSION_FORMS.finditer(line):
            version = match.group(1) or match.group(2)
            add_claim(ClaimType.PYTHON_VERSION, version, {"version": version}, line,
                      line_no, match.start(), match.end(), match.group(0))
    return claims


def extract_documents(paths: Iterable[Path], root: Path) -> list[Claim]:
    claims: list[Claim] = []
    for path in sorted(paths):
        try:
            relative = path.relative_to(root).as_posix()
            claims.extend(extract_markdown(path.read_text(encoding="utf-8"), relative, root))
        except (OSError, UnicodeError):
            continue
    return claims
