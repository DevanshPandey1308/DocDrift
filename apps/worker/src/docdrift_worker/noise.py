"""Validated, non-executable repository finding exclusion configuration."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_CONFIG_BYTES = 64 * 1024
_PLAIN_SCALAR = re.compile(r"^[A-Za-z0-9_./*() -]+$")


class RepositoryConfigError(ValueError):
    """The repository's optional .docdrift.yml is malformed or unsupported."""


@dataclass(frozen=True, slots=True)
class FindingExclusions:
    document_patterns: tuple[str, ...] = ()
    claim_reasons: dict[str, str] | None = None
    inline_min: str = "breaking"
    max_inline_comments: int = 5

    def reason_for(self, claim_id: str, document_path: str) -> str | None:
        reasons = self.claim_reasons or {}
        if claim_id in reasons:
            return reasons[claim_id]
        for pattern in self.document_patterns:
            if PurePosixPath(document_path).match(pattern):
                return f"excluded by docs.exclude pattern {pattern!r}"
        return None


def _split_field(text: str) -> tuple[str, str]:
    key, separator, value = text.partition(":")
    if not separator or not re.fullmatch(r"[a-z_]+", key):
        raise RepositoryConfigError(".docdrift.yml contains an unsupported field.")
    return key, value.strip()


def _scalar(value: str) -> str:
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise RepositoryConfigError(".docdrift.yml contains an invalid quoted string.") from error
        if not isinstance(parsed, str):
            raise RepositoryConfigError(".docdrift.yml values must be strings.")
        return parsed
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        return value[1:-1].replace("''", "'")
    if not _PLAIN_SCALAR.fullmatch(value):
        raise RepositoryConfigError(
            ".docdrift.yml plain strings may contain only letters, digits, spaces, and path punctuation; quote other values."
        )
    return value


def _list_value(
    lines: list[tuple[int, str]], index: int, inline: str, child_indent: int
) -> tuple[list[str], int]:
    if inline == "[]":
        return [], index
    if inline.startswith("["):
        try:
            result = json.loads(inline)
        except json.JSONDecodeError as error:
            raise RepositoryConfigError("Flow lists in .docdrift.yml must use JSON string syntax.") from error
        if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
            raise RepositoryConfigError(".docdrift.yml list values must be strings.")
        return result, index
    if inline:
        raise RepositoryConfigError(".docdrift.yml expected a list or an indented list.")

    values: list[str] = []
    while index < len(lines) and lines[index][0] == child_indent:
        content = lines[index][1]
        if not content.startswith("- "):
            raise RepositoryConfigError(".docdrift.yml list items must start with '- '.")
        values.append(_scalar(content[2:].strip()))
        index += 1
    return values, index


def _parse_subset(content: str) -> dict[str, object]:
    """Parse the deliberately small YAML subset documented for this repository."""
    lines: list[tuple[int, str]] = []
    for raw in content.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw:
            raise RepositoryConfigError(".docdrift.yml indentation must use spaces, not tabs.")
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise RepositoryConfigError(".docdrift.yml indentation must use two-space levels.")
        lines.append((indent, raw.strip()))

    result: dict[str, object] = {}
    index = 0
    while index < len(lines):
        indent, content = lines[index]
        if indent != 0:
            raise RepositoryConfigError(".docdrift.yml has an unexpected indentation level.")
        key, value = _split_field(content)
        if key in result:
            raise RepositoryConfigError(".docdrift.yml contains a duplicate key.")
        index += 1
        if key == "version":
            if value != "1":
                raise RepositoryConfigError(".docdrift.yml requires version: 1.")
            result[key] = 1
        elif key == "docs":
            if value:
                raise RepositoryConfigError("docs must be a mapping.")
            docs: dict[str, object] = {}
            while index < len(lines) and lines[index][0] == 2:
                child_key, child_value = _split_field(lines[index][1])
                index += 1
                if child_key != "exclude" or child_key in docs:
                    raise RepositoryConfigError("docs supports one exclude list.")
                docs[child_key], index = _list_value(lines, index, child_value, 4)
            result[key] = docs
        elif key == "findings":
            if value:
                raise RepositoryConfigError("findings must be a mapping.")
            findings: dict[str, object] = {}
            while index < len(lines) and lines[index][0] == 2:
                child_key, child_value = _split_field(lines[index][1])
                index += 1
                if child_key != "ignore" or child_key in findings:
                    raise RepositoryConfigError("findings supports one ignore list.")
                if child_value == "[]":
                    findings[child_key] = []
                    continue
                if child_value:
                    raise RepositoryConfigError("findings.ignore must be an indented list.")
                rules: list[dict[str, str]] = []
                while index < len(lines) and lines[index][0] == 4:
                    first = lines[index][1]
                    if not first.startswith("- "):
                        raise RepositoryConfigError("Ignore rules must start with '- claim_id:'.")
                    field, scalar = _split_field(first[2:].strip())
                    if field != "claim_id" or not scalar:
                        raise RepositoryConfigError("Each ignore rule must begin with claim_id.")
                    rule = {field: _scalar(scalar)}
                    index += 1
                    if index >= len(lines) or lines[index][0] != 6:
                        raise RepositoryConfigError("Each ignore rule requires an indented reason.")
                    reason_key, reason_value = _split_field(lines[index][1])
                    if reason_key != "reason":
                        raise RepositoryConfigError("Each ignore rule requires a reason.")
                    rule[reason_key] = _scalar(reason_value)
                    rules.append(rule)
                    index += 1
                findings[child_key] = rules
            result[key] = findings
        elif key == "severity":
            if value:
                raise RepositoryConfigError("severity must be a mapping.")
            severity: dict[str, object] = {}
            while index < len(lines) and lines[index][0] == 2:
                child_key, child_value = _split_field(lines[index][1])
                index += 1
                if child_key in severity:
                    raise RepositoryConfigError("severity contains a duplicate key.")
                if child_key == "inline_min":
                    severity[child_key] = _scalar(child_value)
                elif child_key == "max_inline_comments":
                    if not child_value.isdecimal():
                        raise RepositoryConfigError("severity.max_inline_comments must be an integer.")
                    severity[child_key] = int(child_value)
                else:
                    raise RepositoryConfigError("severity supports only inline_min and max_inline_comments.")
            result[key] = severity
        else:
            raise RepositoryConfigError(".docdrift.yml supports only version, docs, and findings.")

    return result


def _path_pattern(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise RepositoryConfigError("docs.exclude entries must be non-empty relative path patterns.")
    if "\\" in value or "\x00" in value or ":" in value:
        raise RepositoryConfigError("docs.exclude patterns must use safe repository-relative paths.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise RepositoryConfigError("docs.exclude patterns cannot escape the repository root.")
    return value


def load_finding_exclusions(repository_root: Path) -> FindingExclusions:
    """Read and validate .docdrift.yml without supporting executable YAML features."""
    root = repository_root.resolve()
    config_path = root / ".docdrift.yml"
    try:
        resolved_path = config_path.resolve(strict=True)
    except FileNotFoundError:
        return FindingExclusions()
    except OSError as error:
        raise RepositoryConfigError(".docdrift.yml could not be resolved safely.") from error
    if not resolved_path.is_relative_to(root) or not resolved_path.is_file():
        raise RepositoryConfigError(".docdrift.yml must be a regular file inside the repository.")
    try:
        if resolved_path.stat().st_size > MAX_CONFIG_BYTES:
            raise RepositoryConfigError(".docdrift.yml exceeds the 64 KiB size limit.")
        content = resolved_path.read_text(encoding="utf-8")
    except RepositoryConfigError:
        raise
    except (OSError, UnicodeError) as error:
        raise RepositoryConfigError(".docdrift.yml could not be read as UTF-8.") from error
    data = _parse_subset(content)

    if "version" not in data or set(data) - {"version", "docs", "findings", "severity"}:
        raise RepositoryConfigError(".docdrift.yml requires version: 1 and supports only docs/findings/severity.")
    docs = data.get("docs", {})
    if not isinstance(docs, dict):
        raise RepositoryConfigError("docs must be a mapping.")
    patterns_value = docs.get("exclude", [])
    if not isinstance(patterns_value, list):
        raise RepositoryConfigError("docs.exclude must be a list of path patterns.")
    patterns = tuple(_path_pattern(item) for item in patterns_value)

    findings = data.get("findings", {})
    if not isinstance(findings, dict):
        raise RepositoryConfigError("findings must be a mapping.")
    ignore_value = findings.get("ignore", [])
    if not isinstance(ignore_value, list):
        raise RepositoryConfigError("findings.ignore must be a list.")
    reasons: dict[str, str] = {}
    for rule in ignore_value:
        if not isinstance(rule, dict) or set(rule) != {"claim_id", "reason"}:
            raise RepositoryConfigError("Each findings.ignore rule requires claim_id and reason.")
        claim_id, reason = rule["claim_id"], rule["reason"]
        if (
            not isinstance(claim_id, str)
            or not re.fullmatch(r"[A-Fa-f0-9]{16}", claim_id)
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 500
        ):
            raise RepositoryConfigError("Ignore rules require a 16-character claim ID and a short reason.")
        if claim_id in reasons:
            raise RepositoryConfigError(".docdrift.yml contains duplicate ignored claim IDs.")
        reasons[claim_id] = reason.strip()
    severity = data.get("severity", {})
    if not isinstance(severity, dict):
        raise RepositoryConfigError("severity must be a mapping.")
    inline_min = severity.get("inline_min", "breaking")
    if inline_min not in {"breaking", "misleading", "cosmetic"}:
        raise RepositoryConfigError("severity.inline_min must be breaking, misleading, or cosmetic.")
    max_inline_comments = severity.get("max_inline_comments", 5)
    if (
        isinstance(max_inline_comments, bool)
        or not isinstance(max_inline_comments, int)
        or not 0 <= max_inline_comments <= 50
    ):
        raise RepositoryConfigError("severity.max_inline_comments must be an integer from 0 to 50.")
    return FindingExclusions(patterns, reasons, inline_min, max_inline_comments)
