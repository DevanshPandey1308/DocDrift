"""Deterministic checker registry and built-in claim verifiers."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import PurePosixPath

from .index import CodeIndex, FunctionInfo, RouteInfo
from .models import Claim, ClaimType, Evidence, Finding, Verdict

Checker = Callable[[Claim, CodeIndex], Finding]


def _finding(claim: Claim, verdict: Verdict, explanation: str, evidence: tuple[Evidence, ...] = (), method: str = "static_python") -> Finding:
    return Finding(claim, verdict, explanation, evidence, method)


def _funcs(claim: Claim, index: CodeIndex) -> list[FunctionInfo]:
    return index.functions.get(claim.subject, [])


_THIRD_PARTY_COMMAND = re.compile(r"\b(?:pytest|pip|docker|git|npm)\b", re.I)
_HISTORICAL_FRAMING = re.compile(
    r"\b(?:was|used\s+to|previously|formerly|deprecated)\b|"
    r"\bin\s+v\d+(?:\.\d+)*\b|->|→",
    re.I,
)
_NEGATED_FRAMING = re.compile(
    r"\b(?:not|never|no\s+longer|does\s+not|doesn't|do\s+not|don't|"
    r"is\s+not|isn't|are\s+not|aren't|cannot|can't|without)\b",
    re.I,
)
_FOREIGN_PACKAGE_FRAMING = re.compile(
    r"\b(?:another|different|other)\s+(?:project|package|module|library)\b",
    re.I,
)
_ABSENCE_FLAGS = {"--help", "-h", "--version", "-V"}


def _edit_distance(left: str, right: str, limit: int = 3) -> int:
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, 1):
        current = [row]
        for column, right_char in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_char != right_char),
            ))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _normalized_flag(flag: str) -> tuple[str, tuple[str, ...]]:
    body = flag.lstrip("-").casefold()
    body = re.sub(r"(?:[-_]?\d+)$", "", body)
    tokens = tuple(token for token in re.split(r"[-_]", body) if token)
    return body, tokens


def _likely_renamed_flag(claimed: str, declared: str) -> bool:
    normalized_claim, claim_tokens = _normalized_flag(claimed)
    normalized_declared, declared_tokens = _normalized_flag(declared)
    if any(len(token) >= 3 for token in set(claim_tokens) & set(declared_tokens)):
        return True
    return _edit_distance(normalized_claim, normalized_declared) <= 3


def _source_scope_label(index: CodeIndex) -> str:
    return ", ".join(index.source_scopes) if index.source_scopes else "(no source scope)"


def _flag_own_cli_context(claim: Claim, index: CodeIndex) -> bool:
    if re.search(r"\b(?:the|this)\s+CLI\b|\bcommand\s+line\b", claim.text, re.I):
        return True
    names = index.project_script_names | index.parser_programs
    return any(re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", claim.text, re.I)
               for name in names if name)


def _check_absent_flag(claim: Claim, index: CodeIndex) -> Finding:
    flag = claim.subject
    scope = _source_scope_label(index)
    if flag in _ABSENCE_FLAGS:
        return _finding(claim, Verdict.UNVERIFIABLE,
                        f"Built-in flag {flag} is not checked for absence in scanned scope {scope}.")
    if _THIRD_PARTY_COMMAND.search(claim.text):
        return _finding(claim, Verdict.UNVERIFIABLE,
                        f"The command is third-party context; DocDrift does not infer its flags from scanned scope {scope}.")
    if not index.cli_declarations:
        return _finding(claim, Verdict.UNVERIFIABLE,
                        f"No recognized CLI declaration was found in scanned source scope {scope}.")
    if index.dynamic_cli_declarations:
        return _finding(claim, Verdict.UNVERIFIABLE,
                        f"A computed or dynamically declared CLI flag prevents absence checks in scanned scope {scope}.")
    if not index.source_text_complete:
        return _finding(claim, Verdict.UNVERIFIABLE,
                        f"Source text could not be read completely in scanned scope {scope}.")
    if any(flag in text for text in index.source_text_files.values()):
        return _finding(claim, Verdict.UNVERIFIABLE,
                        f"The flag text appears in scanned source scope {scope}, but no declaration was indexed.")
    near = [
        (declared, info)
        for declared, declarations in index.flags.items()
        for info in declarations
        if not info.dynamic and _likely_renamed_flag(flag, declared)
    ]
    near.sort(key=lambda item: (
        _edit_distance(_normalized_flag(flag)[0], _normalized_flag(item[0])[0]),
        item[0], item[1].path, item[1].line,
    ))
    contextual = _flag_own_cli_context(claim, index)
    if not near and not contextual:
        return _finding(claim, Verdict.UNVERIFIABLE,
                        f"No declaration found for {flag}; claim lacks project CLI context in scanned scope {scope}.")
    evidence: tuple[Evidence, ...] = ()
    if near:
        source = index.evidence(near[0][1].path, near[0][1].line)
        evidence = (source,) if source else ()
    explanation = (
        f"No declaration found for {flag}; scanned source scope {scope}."
        if not evidence else f"No declaration found for {flag}; likely renamed declaration {near[0][0]} is shown."
    )
    return _finding(claim, Verdict.FALSE, explanation, evidence, "static_cli_absence")


def _source_complete(index: CodeIndex) -> bool:
    return not index.errors


def check_function(claim: Claim, index: CodeIndex) -> Finding:
    if not _source_complete(index):
        return _finding(claim, Verdict.UNVERIFIABLE, "Python source indexing was incomplete; symbol absence or uniqueness cannot be established.")
    if claim.expected.get("ambiguous"):
        return _finding(claim, Verdict.UNVERIFIABLE, "Documented parameter list is outside the supported grammar.")
    matches = _funcs(claim, index)
    if len(matches) != 1:
        state = "No statically indexed function matches." if not matches else "Multiple functions match this name."
        return _finding(claim, Verdict.UNVERIFIABLE, state)
    info = matches[0]
    evidence = index.evidence(info.path, info.line)
    ev = (evidence,) if evidence else ()
    if any(err.path == info.path for err in index.errors):
        return _finding(claim, Verdict.UNVERIFIABLE, "Function source could not be fully indexed.", ev)
    expected = claim.expected.get("parameters")
    actual = [p.name for p in info.parameters]
    if expected is None:
        return _finding(claim, Verdict.UNVERIFIABLE, "Documented signature cannot be compared safely.", ev)
    if list(expected) == actual:
        return _finding(claim, Verdict.TRUE, f"Parameters match {info.signature}.", ev)
    return _finding(claim, Verdict.FALSE, f"Documented parameters {list(expected)} differ from source parameters {actual}.", ev)


def _normal_literal(value: object) -> tuple[bool, object]:
    text = str(value).strip()
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+", text):
        try:
            return True, int(text.replace(",", ""))
        except ValueError:
            return False, None
    if text.startswith(("'", '"')) and text[-1:] == text[:1]:
        try:
            return True, ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return False, None
    if text in {"True", "False", "None"} or re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        try:
            return True, ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return False, None
    return False, None


def check_default(claim: Claim, index: CodeIndex) -> Finding:
    unit_note = (
        f" Documented unit {claim.expected['unit']!r} is not checked."
        if "unit" in claim.expected
        else ""
    )
    if not _source_complete(index):
        return _finding(claim, Verdict.UNVERIFIABLE, "Python source indexing was incomplete; parameter uniqueness cannot be established." + unit_note)
    expected_raw = claim.expected.get("value")
    if claim.expected.get("ambiguous"):
        return _finding(claim, Verdict.UNVERIFIABLE, "Default expression is outside the supported literal grammar." + unit_note)
    ok, expected = _normal_literal(expected_raw)
    if not ok:
        return _finding(claim, Verdict.UNVERIFIABLE, "Documented default cannot be normalized safely." + unit_note)
    env_default_subject = re.fullmatch(r"(?=[A-Z0-9_]*_)(?=.*[A-Z])[A-Z0-9_]+", claim.subject)
    is_backticked_subject = f"`{claim.subject}`" in claim.text
    if env_default_subject and is_backticked_subject:
        reads = index.environment_defaults.get(claim.subject, [])
        if not reads:
            return _finding(claim, Verdict.UNVERIFIABLE,
                            "No recognized environment-variable read with a default was found." + unit_note)
        if len(reads) != 1 or not reads[0].has_default or not reads[0].default_resolved:
            return _finding(claim, Verdict.UNVERIFIABLE,
                            "Environment-variable default is absent, non-literal, or ambiguous." + unit_note)
        read = reads[0]
        evidence = index.evidence(read.path, read.line)
        ev = (evidence,) if evidence else ()
        actual = read.default
        equivalent = actual == expected and type(actual) is type(expected)
        if isinstance(actual, str) and isinstance(expected, (int, float)):
            parsed, numeric_actual = _normal_literal(actual)
            equivalent = parsed and type(numeric_actual) is type(expected) and numeric_actual == expected
        if equivalent:
            return _finding(claim, Verdict.TRUE,
                            f"Environment-variable default for {claim.subject} matches." + unit_note, ev,
                            "static_environment_default")
        return _finding(claim, Verdict.FALSE,
                        f"Documented default {expected!r} differs from environment-variable default {actual!r}." + unit_note,
                        ev, "static_environment_default")
    qualified_function = claim.expected.get("function")
    if qualified_function is not None:
        functions = index.functions.get(str(qualified_function), [])
        if len(functions) != 1:
            return _finding(claim, Verdict.UNVERIFIABLE,
                            "The documented function is missing or ambiguous in source." + unit_note)
        function_info = functions[0]
        selected = [p for p in function_info.parameters if p.name == claim.subject]
        unique = {
            (function_info.path, function_info.line, p.name): (function_info, p)
            for p in selected
        }
    else:
        unique = {
            (f.path, f.line, p.name): (f, p)
            for values in index.functions.values()
            for f in values
            for p in f.parameters
            if p.name == claim.subject
        }
    if not unique:
        return _finding(claim, Verdict.UNVERIFIABLE, "No statically indexed parameter matches this name." + unit_note)
    if len(unique) != 1:
        return _finding(claim, Verdict.UNVERIFIABLE, "Parameter name is ambiguous across functions." + unit_note)
    function, param = next(iter(unique.values()))
    evidence = index.evidence(function.path, function.line)
    ev = (evidence,) if evidence else ()
    if not param.has_default:
        return _finding(claim, Verdict.FALSE, f"Parameter {param.name} has no explicit default in source." + unit_note, ev)
    if not param.default_resolved:
        return _finding(claim, Verdict.UNVERIFIABLE, f"Parameter {param.name} has a default that is not a safely representable literal." + unit_note, ev)
    actual = param.default
    if actual == expected and type(actual) is type(expected):
        if "unit" in claim.expected:
            return _finding(
                claim,
                Verdict.TRUE,
                f"Numeric default for {param.name} matches." + unit_note,
                ev,
            )
        return _finding(claim, Verdict.TRUE, f"Explicit default for {param.name} matches.", ev)
    return _finding(claim, Verdict.FALSE, f"Documented default {expected!r} differs from source default {actual!r}." + unit_note, ev)


def check_flag(claim: Claim, index: CodeIndex) -> Finding:
    if not _source_complete(index):
        return _finding(claim, Verdict.UNVERIFIABLE, "Python source indexing was incomplete; flag presence cannot be established.")
    flags = index.flags.get(claim.subject, [])
    if not flags:
        return _check_absent_flag(claim, index)
    if len(flags) != 1 or any(f.dynamic for f in flags):
        return _finding(claim, Verdict.UNVERIFIABLE, "Flag declaration is ambiguous or dynamically configured.")
    flag = flags[0]
    evidence = index.evidence(flag.path, flag.line)
    ev = (evidence,) if evidence else ()
    return _finding(claim, Verdict.TRUE, f"Flag {claim.subject} is declared by a recognized parser.", ev)


def check_route(claim: Claim, index: CodeIndex) -> Finding:
    routes = index.routes.get(claim.subject, [])
    expected_method = str(claim.expected.get("method", "")).upper()
    exact = [route for route in routes if route.method == expected_method and not route.dynamic]
    if exact:
        route = sorted(exact, key=lambda item: (item.source_path, item.line))[0]
        evidence = index.evidence(route.source_path, route.line)
        ev = (evidence,) if evidence else ()
        return _finding(claim, Verdict.TRUE,
                        f"A static {expected_method} route declaration matches {route.path}.",
                        ev, "static_python_route")
    if not _source_complete(index) or not index.routes_complete:
        return _finding(claim, Verdict.UNVERIFIABLE,
                        "Route indexing was incomplete because a declaration or URL prefix could not be resolved.",
                        method="static_python_route")
    indexed_routes = [route for path_routes in index.routes.values() for route in path_routes if not route.dynamic]
    if not indexed_routes:
        return _finding(claim, Verdict.UNVERIFIABLE,
                        f"No statically indexed route declaration exists in scanned source scope {_source_scope_label(index)}.",
                        method="static_python_route")
    if not routes:
        nearest = sorted(
            indexed_routes,
            key=lambda item: (item.method != expected_method,
                              _edit_distance(item.path, claim.subject, max(len(item.path), len(claim.subject))),
                              item.source_path, item.line),
        )[0]
        evidence = index.evidence(nearest.source_path, nearest.line)
        ev = (evidence,) if evidence else ()
        explanation = (
            f"No {expected_method} {claim.subject} route declaration was found in scanned source scope {_source_scope_label(index)}."
            if not ev else f"No {expected_method} {claim.subject} route was found; nearest indexed route is {nearest.method} {nearest.path}."
        )
        return _finding(claim, Verdict.FALSE, explanation, ev, "static_python_route_absence")
    declarations: dict[tuple[str, int], list[RouteInfo]] = {}
    for route in routes:
        declarations.setdefault((route.source_path, route.line), []).append(route)
    if len(declarations) != 1:
        return _finding(claim, Verdict.UNVERIFIABLE, "Multiple route declarations use this path.", method="static_python_route")
    declaration = next(iter(declarations.values()))
    route = declaration[0]
    evidence = index.evidence(route.source_path, route.line)
    ev = (evidence,) if evidence else ()
    methods = sorted({item.method for item in declaration})
    if expected_method in methods:
        return _finding(claim, Verdict.TRUE, f"A static {expected_method} route declaration matches {route.path}.", ev, "static_python_route")
    return _finding(claim, Verdict.FALSE, f"The static route declares {', '.join(methods)}, not {expected_method}.", ev, "static_python_route")


def check_env(claim: Claim, index: CodeIndex) -> Finding:
    if not _source_complete(index):
        return _finding(claim, Verdict.UNVERIFIABLE, "Python source indexing was incomplete; environment reads may be missing.")
    evidence = index.environment.get(claim.subject, [])
    if evidence:
        return _finding(claim, Verdict.TRUE, f"A recognized source read references {claim.subject}; this does not prove it is required.", tuple(evidence), "static_environment_read")
    scope = _source_scope_label(index)
    if not index.environment_text_complete:
        return _finding(claim, Verdict.UNVERIFIABLE,
                        f"Environment source text could not be read completely in scanned scope {scope}.")
    name_pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(claim.subject)}(?![A-Za-z0-9_])")
    if any(name_pattern.search(text) for text in index.environment_text_files.values()):
        return _finding(claim, Verdict.UNVERIFIABLE,
                        f"The environment name appears in scanned text but has no recognized literal read in scope {scope}.")
    if index.dynamic_environment_access:
        return _finding(claim, Verdict.UNVERIFIABLE,
                        f"Dynamic environment access prevents an absence check in scanned source scope {scope}.")
    prefix = claim.subject.split("_", 1)[0]
    owned_names = {name for name in index.environment if name.split("_", 1)[0] == prefix}
    if len(prefix) < 3 or len(owned_names) < 2:
        return _finding(claim, Verdict.UNVERIFIABLE,
                        f"No recognized read of {claim.subject}; no project-owned environment namespace is established in scope {scope}.")
    alternates = sorted(
        ((name, item) for name in owned_names for item in index.environment[name]),
        key=lambda pair: (_edit_distance(claim.subject, pair[0], max(len(claim.subject), len(pair[0]))),
                          pair[0], pair[1].path, pair[1].line),
    )
    near = alternates[0][1] if alternates else None
    ev = (near,) if near else ()
    explanation = (
        f"No declaration found for environment variable {claim.subject}; scanned environment text scope {scope}."
        if not ev else f"No read of {claim.subject} was found; nearby project-owned read {alternates[0][0]} is shown."
    )
    return _finding(claim, Verdict.FALSE, explanation, ev, "static_environment_absence")


def check_path(claim: Claim, index: CodeIndex) -> Finding:
    candidate = PurePosixPath(claim.subject)
    if candidate.is_absolute():
        return _finding(claim, Verdict.UNVERIFIABLE, "Repository path is absolute.", method="repository_path")
    # Relative parent references are allowed only when they resolve inside the
    # repository; unresolved traversal remains unverifiable below.
    traversal = ".." in candidate.parts

    document_parent = (index.root / PurePosixPath(claim.document_path)).parent
    bases: list[Path] = [index.root]
    current = document_parent
    while current.is_relative_to(index.root):
        if current not in bases:
            bases.append(current)
        if current == index.root:
            break
        current = current.parent
    for base in bases:
        resolved = (base / str(candidate)).resolve()
        if not resolved.is_relative_to(index.root):
            continue
        if resolved.is_file() or resolved.is_dir():
            if resolved.is_dir():
                return _finding(claim, Verdict.TRUE, "Referenced repository directory exists.", method="repository_path")
            try:
                line = resolved.read_text(encoding="utf-8").splitlines()[0].strip()
                ev = (Evidence(resolved.relative_to(index.root).as_posix(), 1, line),) if line else (Evidence(resolved.relative_to(index.root).as_posix(), 1, ""),)
            except (OSError, UnicodeError):
                ev = ()
            return _finding(claim, Verdict.TRUE, "Referenced repository file exists.", ev, "repository_path")

    suffix = str(candidate).replace("\\", "/").rstrip("/")
    matches = sorted(path for path in index.scanned_paths if path == suffix or path.endswith("/" + suffix))
    if len(matches) == 1:
        return _finding(claim, Verdict.UNVERIFIABLE,
                        "Path resolves only relative to another directory.", method="repository_path")
    if traversal:
        return _finding(claim, Verdict.UNVERIFIABLE,
                        "Relative path does not resolve within the repository.", method="repository_path")
    return _finding(claim, Verdict.FALSE, "Referenced repository path does not exist.", method="repository_path")


def check_python_version(claim: Claim, index: CodeIndex) -> Finding:
    document_parent = (index.root / PurePosixPath(claim.document_path)).parent
    metadata: Path | None = None
    current = document_parent
    while current.is_relative_to(index.root):
        candidate = current / "pyproject.toml"
        if candidate.is_file():
            metadata = candidate
            break
        if current == index.root:
            break
        current = current.parent
    if metadata is None:
        return _finding(claim, Verdict.UNVERIFIABLE, "No pyproject.toml metadata is available.", method="python_metadata")
    text = metadata.read_text(encoding="utf-8")
    match = re.search(r"(?m)^requires-python\s*=\s*[\"']([^\"']+)[\"']", text)
    if not match:
        return _finding(claim, Verdict.UNVERIFIABLE, "No supported requires-python declaration was found.", method="python_metadata")
    req = match.group(1).strip()
    documented = tuple(int(x) for x in claim.expected["version"].split("."))
    # Reliably handle only >=M.m; other ranges need a version-specifier parser.
    requirement = re.fullmatch(r">=\s*(\d+)\.(\d+)(?:\.\d+)?", req)
    if not requirement:
        return _finding(claim, Verdict.UNVERIFIABLE, f"Metadata requirement {req!r} is outside the supported comparison grammar.", method="python_metadata")
    actual = (int(requirement.group(1)), int(requirement.group(2)))
    source_line = text[:match.start()].count("\n") + 1
    metadata_path = metadata.relative_to(index.root).as_posix()
    return _finding(claim, Verdict.TRUE if documented == actual else Verdict.FALSE,
                    f"Documentation requires {documented}; project metadata declares {actual}.",
                    (Evidence(metadata_path, source_line, match.group(0)),), "python_metadata")


REGISTRY: dict[ClaimType, Checker] = {
    ClaimType.FUNCTION: check_function,
    ClaimType.DEFAULT: check_default,
    ClaimType.CLI_FLAG: check_flag,
    ClaimType.ROUTE: check_route,
    ClaimType.ENV_VAR: check_env,
    ClaimType.FILE_PATH: check_path,
    ClaimType.PYTHON_VERSION: check_python_version,
}


def check_claim(claim: Claim, index: CodeIndex) -> Finding:
    if _HISTORICAL_FRAMING.search(claim.text) or _NEGATED_FRAMING.search(claim.text):
        return _finding(claim, Verdict.UNVERIFIABLE,
                        "Historical, negated, deprecated, or change-arrow framing cannot be verified as a positive current claim.")
    if (claim.type in {ClaimType.DEFAULT, ClaimType.FUNCTION}
            and _FOREIGN_PACKAGE_FRAMING.search(claim.text)):
        return _finding(claim, Verdict.UNVERIFIABLE,
                        "The documented function or parameter is attributed to another project or package.")
    checker = REGISTRY.get(claim.type)
    if checker is None:
        return _finding(claim, Verdict.UNVERIFIABLE, "No checker is registered for this claim type.")
    return checker(claim, index)
