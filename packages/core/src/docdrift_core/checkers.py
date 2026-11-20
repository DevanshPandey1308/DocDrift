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
        return _finding(claim, Verdict.UNVERIFIABLE, "No statically resolved declaration of this flag was found.")
    if len(flags) != 1 or any(f.dynamic for f in flags):
        return _finding(claim, Verdict.UNVERIFIABLE, "Flag declaration is ambiguous or dynamically configured.")
    flag = flags[0]
    evidence = index.evidence(flag.path, flag.line)
    ev = (evidence,) if evidence else ()
    return _finding(claim, Verdict.TRUE, f"Flag {claim.subject} is declared by a recognized parser.", ev)


def check_route(claim: Claim, index: CodeIndex) -> Finding:
    if not _source_complete(index) or not index.routes_complete:
        return _finding(claim, Verdict.UNVERIFIABLE, "Route indexing was incomplete because a declaration or URL prefix could not be resolved.", method="static_python_route")
    routes = index.routes.get(claim.subject, [])
    if not routes:
        return _finding(claim, Verdict.UNVERIFIABLE, "No unambiguous static route declaration matches this path.", method="static_python_route")
    declarations: dict[tuple[str, int], list[RouteInfo]] = {}
    for route in routes:
        declarations.setdefault((route.source_path, route.line), []).append(route)
    if len(declarations) != 1:
        return _finding(claim, Verdict.UNVERIFIABLE, "Multiple route declarations use this path.", method="static_python_route")
    declaration = next(iter(declarations.values()))
    route = declaration[0]
    evidence = index.evidence(route.source_path, route.line)
    ev = (evidence,) if evidence else ()
    expected_method = str(claim.expected.get("method", "")).upper()
    methods = sorted({item.method for item in declaration})
    if expected_method in methods:
        return _finding(claim, Verdict.TRUE, f"A static {expected_method} route declaration matches {route.path}.", ev, "static_python_route")
    return _finding(claim, Verdict.FALSE, f"The static route declares {', '.join(methods)}, not {expected_method}.", ev, "static_python_route")


def check_env(claim: Claim, index: CodeIndex) -> Finding:
    if not _source_complete(index):
        return _finding(claim, Verdict.UNVERIFIABLE, "Python source indexing was incomplete; environment reads may be missing.")
    evidence = index.environment.get(claim.subject, [])
    if not evidence:
        return _finding(claim, Verdict.UNVERIFIABLE, "No recognized static read of this environment variable was found.")
    return _finding(claim, Verdict.TRUE, f"A recognized source read references {claim.subject}; this does not prove it is required.", tuple(evidence), "static_environment_read")


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
    checker = REGISTRY.get(claim.type)
    if checker is None:
        return _finding(claim, Verdict.UNVERIFIABLE, "No checker is registered for this claim type.")
    return checker(claim, index)
