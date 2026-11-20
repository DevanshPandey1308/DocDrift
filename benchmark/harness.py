"""Seeded synthetic benchmark for DocDrift's shipped deterministic CLI."""

from __future__ import annotations

import argparse
import ast
import contextlib
import csv
import io
import json
import math
import random
import re
import shutil
import sys
import time
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from docdrift_cli.main import main as cli_main


SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "benchmark/datasets/synthetic"
DEFAULT_OUT = ROOT / "benchmark/results/latest"
DEFAULT_BASELINE = ROOT / "benchmark/baseline.json"
CLAIM_TYPES = (
    "default_value", "function_signature", "cli_flag", "environment_variable",
    "file_path", "python_version", "route",
)
OPERATORS = (
    "change_default", "rename_param", "add_param", "rename_flag",
    "rename_env_read", "delete_file", "change_requires_python", "rename_route",
)
UNSUPPORTED_TEMPLATES = (
    "By default, calls time out after {value} seconds.",
    "Retries are capped at {value}.",
    "The usual wait lasts {value} seconds.",
    "A client normally waits for {value} seconds.",
    "Calls typically repeat {value} times.",
    "The timeout is typically around {value} seconds.",
)
PARAPHRASE_TEMPLATES = (
    "`{subject}` is set to {value} unless overridden.",
    "Pass `{subject}={value}` for the usual behaviour.",
    "For typical use, keep `{subject}` at {value}.",
    "`{subject}` takes a value of {value} in normal deployments.",
    "Most configurations leave `{subject}` as {value}.",
    "The recommended `{subject}` value is {value}.",
)
DECOY_TEMPLATES = (
    "In v1 the default timeout was {value} seconds.",
    "A different project's default timeout is {other}.",
    "The default timeout is {value}.",
    "| timeout: {value} -> {other} |",
    "Another package's `legacy_fetch(timeout={other})` uses the same parameter name.",
    "Legacy notes say timeout changed from {value} to {other}.",
)


@dataclass(slots=True)
class StatementSpec:
    claim_type: str
    category: str
    label: str
    subject: str
    text: str
    operator: str | None = None
    fenced: bool = False


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _write_json(path: Path, value: object) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _affected_operator(statement: StatementSpec, mutations: dict[str, object]) -> str | None:
    if statement.label == "decoy":
        return None
    if statement.claim_type == "default_value":
        if statement.subject == "timeout" and "change_default" in mutations:
            return "change_default"
        if statement.subject == "retries" and "rename_param" in mutations:
            return "rename_param"
    if statement.claim_type == "function_signature":
        if "rename_param" in mutations:
            return "rename_param"
        if "add_param" in mutations:
            return "add_param"
    mapped = {
        "cli_flag": "rename_flag",
        "environment_variable": "rename_env_read",
        "file_path": "delete_file",
        "python_version": "change_requires_python",
        "route": "rename_route",
    }.get(statement.claim_type)
    return mapped if mapped in mutations else None


def _make_statements(case_number: int, rng: random.Random, timeout: int,
                     retries: int, flag: str, env_name: str, path: str,
                     version: str, route: str, mutation_count: int) -> list[StatementSpec]:
    other = timeout + 17
    retry_subject = "retries"
    unsupported_index = case_number % len(UNSUPPORTED_TEMPLATES)
    paraphrase_index = (case_number // 2) % len(PARAPHRASE_TEMPLATES)
    decoy_index = (case_number // 3) % len(DECOY_TEMPLATES)
    unsupported_subject = "retries" if unsupported_index in {1, 4} else "timeout"
    unsupported_value = retries if unsupported_subject == "retries" else timeout
    paraphrase_subject = "retries" if paraphrase_index in {1, 4} else "timeout"
    paraphrase_value = retries if paraphrase_subject == "retries" else timeout
    decoy_value = timeout if decoy_index in {0, 2, 3, 5} else other
    decoy_text = DECOY_TEMPLATES[decoy_index].format(value=decoy_value, other=other)
    return [
        StatementSpec("default_value", "canonical", "correct", "timeout",
                      f"The default timeout is {timeout}."),
        StatementSpec("default_value", "natural_supported", "correct", "timeout",
                      f"| `timeout` | `{timeout}` | Timeout setting. |"),
        StatementSpec("default_value", "natural_supported", "correct", "timeout",
                      f"The `timeout` (default: {timeout}) controls waiting."),
        StatementSpec("default_value", "natural_supported", "correct", "timeout",
                      f"The `fetch(timeout={timeout})` call starts a transfer."),
        StatementSpec("default_value", "natural_supported", "correct", "timeout",
                      f"The default timeout is {timeout} seconds."),
        StatementSpec("function_signature", "canonical", "correct", "fetch",
                      "`fetch(timeout, retries)` accepts request options."),
        StatementSpec("cli_flag", "canonical", "correct", flag,
                      f"Run the CLI with `{flag}`."),
        StatementSpec("environment_variable", "canonical", "correct", env_name,
                      f"The `{env_name}` environment variable is read."),
        StatementSpec("file_path", "canonical", "correct", path,
                      f"The example is implemented in `{path}`."),
        StatementSpec("python_version", "canonical", "correct", version,
                      f"Requires Python {version} or newer."),
        StatementSpec("route", "canonical", "correct", route,
                      f"The GET `{route}` endpoint exists."),
        StatementSpec("default_value", "unsupported_prose", "correct", unsupported_subject,
                      UNSUPPORTED_TEMPLATES[unsupported_index].format(value=unsupported_value)),
        StatementSpec("default_value", "paraphrase_named", "correct", paraphrase_subject,
                      PARAPHRASE_TEMPLATES[paraphrase_index].format(subject=paraphrase_subject,
                                                                   value=paraphrase_value)),
        StatementSpec("default_value", "decoy", "decoy", "timeout", decoy_text,
                      fenced=decoy_index == 2),
    ]


def _choose_mutations(rng: random.Random, forced: Iterable[str] | None) -> list[str]:
    if forced is not None:
        selected = list(forced)
        if len(selected) < 1 or len(selected) > 3 or len(set(selected)) != len(selected):
            raise ValueError("forced mutations must contain one to three unique operators")
        if any(operator not in OPERATORS for operator in selected):
            raise ValueError("unknown mutation operator")
        if {"rename_param", "add_param"}.issubset(selected):
            raise ValueError("rename_param and add_param cannot be combined in one case")
        return selected
    if rng.random() < 0.35:
        return []
    count = rng.randint(1, 3)
    pool = list(OPERATORS)
    rng.shuffle(pool)
    selected: list[str] = []
    for operator in pool:
        if operator == "add_param" and "rename_param" in selected:
            continue
        if operator == "rename_param" and "add_param" in selected:
            continue
        selected.append(operator)
        if len(selected) == count:
            break
    return selected


def generate_case(dataset_root: Path, case_number: int, rng: random.Random,
                  forced_mutations: Iterable[str] | None = None) -> dict[str, Any]:
    case_id = f"case-{case_number:04d}"
    case_dir = dataset_root / case_id
    repo = case_dir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    timeout = rng.randint(15, 90)
    retries = rng.randint(2, 8)
    flag = f"--max-records-{case_number:03d}"
    env_name = f"APP_MODE_{case_number:03d}"
    version = rng.choice(("3.10", "3.11", "3.12"))
    route = f"/items/{case_number:03d}"
    path = f"reference/data-{case_number:03d}.txt"
    statements = _make_statements(case_number, rng, timeout, retries, flag, env_name,
                                  path, version, route, 0)
    selected = _choose_mutations(rng, forced_mutations)
    mutations: dict[str, object] = {}
    for operator in selected:
        if operator == "change_default":
            mutations[operator] = timeout + rng.randint(1, 13)
        elif operator == "rename_param":
            mutations[operator] = "retry_count"
        elif operator == "add_param":
            mutations[operator] = "batch_size"
        elif operator == "rename_flag":
            mutations[operator] = f"--max-renamed-{case_number:03d}"
        elif operator == "rename_env_read":
            mutations[operator] = f"APP_MODE_RENAMED_{case_number:03d}"
        elif operator == "delete_file":
            mutations[operator] = path
        elif operator == "change_requires_python":
            mutations[operator] = "3.11" if version != "3.11" else "3.10"
        elif operator == "rename_route":
            mutations[operator] = f"/records/{case_number:03d}"

    actual_timeout = int(mutations.get("change_default", timeout))
    actual_retries_name = str(mutations.get("rename_param", "retries"))
    extra_param = bool("add_param" in mutations)
    actual_flag = str(mutations.get("rename_flag", flag))
    actual_env = str(mutations.get("rename_env_read", env_name))
    actual_version = str(mutations.get("change_requires_python", version))
    actual_route = str(mutations.get("rename_route", route))
    function_params = [f"timeout={actual_timeout}", f"{actual_retries_name}={retries}"]
    if extra_param:
        function_params.append("batch_size=1")

    api_source = (
        "import os\n"
        "from fastapi import FastAPI\n\n"
        f"def fetch({', '.join(function_params)}):\n    return timeout\n\n"
        "def normalize(limit=5):\n    return limit\n\n"
        f"APP_MODE = os.getenv({actual_env!r})\n"
        f"APP_LIMIT = os.environ.get('APP_LIMIT_{case_number:03d}', '20')\n\n"
        "app = FastAPI()\n\n"
        f"@app.get({actual_route!r})\n"
        "def item():\n    return {}\n"
    )
    cli_source = (
        "import argparse\n\n"
        "parser = argparse.ArgumentParser()\n"
        f"parser.add_argument({actual_flag!r}, action='store_true')\n"
    )
    _write_text(repo / "src/api.py", api_source)
    _write_text(repo / "src/cli.py", cli_source)
    _write_text(repo / "pyproject.toml", f'[project]\nrequires-python = ">={actual_version}"\n')
    # Keep path composition explicit so dataset paths remain repository-relative.
    data_path = repo / path
    if "delete_file" not in mutations:
        _write_text(data_path, f"reference data for case {case_number:03d}\n")

    doc_lines: list[str] = []
    manifest_statements: list[dict[str, Any]] = []
    for statement in statements:
        if statement.text.startswith("| `timeout` |"):
            doc_lines.extend(("| Parameter | Default | Description |", "| --- | --- | --- |"))
        if statement.fenced:
            doc_lines.append("```text")
        doc_lines.append(statement.text)
        line_number = len(doc_lines)
        if statement.fenced:
            doc_lines.append("```")
        operator = _affected_operator(statement, mutations)
        label = "decoy" if statement.label == "decoy" else ("drifted" if operator else "correct")
        manifest_statements.append({
            "path": "docs/README.md",
            "line": line_number,
            "claim_type": statement.claim_type,
            "category": statement.category,
            "label": label,
            "subject": statement.subject,
            "operator": operator,
            "text": statement.text,
        })
    _write_text(repo / "docs/README.md", "\n".join(doc_lines) + "\n")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "mutated": bool(mutations),
        "statements": manifest_statements,
    }
    _write_json(case_dir / "manifest.json", manifest)
    validate_manifest(case_dir, manifest)
    return manifest


def generate_dataset(dataset_root: Path, seed: int = 1337, repos: int = 60) -> list[dict[str, Any]]:
    if repos < 1:
        raise ValueError("repos must be positive")
    target = dataset_root.resolve()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    return [generate_case(target, number, rng) for number in range(repos)]


def validate_manifest(case_dir: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported manifest schema in {case_dir}")
    repo = case_dir / "repo"
    for statement in manifest.get("statements", []):
        path = Path(statement["path"])
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe statement path in {case_dir}")
        source = repo / path
        lines = source.read_text(encoding="utf-8").splitlines()
        line = statement["line"]
        if not isinstance(line, int) or line < 1 or line > len(lines):
            raise ValueError(f"statement line out of range in {case_dir}: {statement}")
        if lines[line - 1] != statement["text"]:
            raise ValueError(f"statement text mismatch in {case_dir}: {statement}")


def _read_python(repo: Path) -> tuple[list[ast.Module], list[Path]]:
    paths = sorted((repo / "src").glob("*.py"))
    trees = [ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix()) for path in paths]
    return trees, paths


def _function(tree_list: list[ast.Module], name: str = "fetch") -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next((node for tree in tree_list for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name), None)


def _parameter_defaults(function: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    args = [*function.args.posonlyargs, *function.args.args]
    result: dict[str, Any] = {}
    if function.args.defaults:
        for argument, default in zip(args[-len(function.args.defaults):], function.args.defaults):
            try:
                result[argument.arg] = ast.literal_eval(default)
            except (ValueError, TypeError, SyntaxError):
                result[argument.arg] = object()
    for argument, default in zip(function.args.kwonlyargs, function.args.kw_defaults):
        if default is not None:
            try:
                result[argument.arg] = ast.literal_eval(default)
            except (ValueError, TypeError, SyntaxError):
                result[argument.arg] = object()
    return result


def _documented_number(text: str) -> int | None:
    match = re.search(r"(?<![A-Za-z0-9])(-?\d+(?:,\d{3})*)(?![A-Za-z0-9])", text)
    if match:
        return int(match.group(1).replace(",", ""))
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    match = re.search(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\b", text, re.I)
    return words[match.group(1).lower()] if match else None


def _independent_agreement(repo: Path, statement: dict[str, Any]) -> bool:
    """Independent stdlib-only semantic check; intentionally does not use core APIs."""
    trees, _ = _read_python(repo)
    kind, subject, text = statement["claim_type"], statement["subject"], statement["text"]
    if kind == "default_value":
        function = _function(trees)
        documented = _documented_number(text)
        if function is None or documented is None:
            return False
        actual = _parameter_defaults(function).get(subject, object())
        return isinstance(actual, int) and not isinstance(actual, bool) and actual == documented
    if kind == "function_signature":
        function = _function(trees)
        match = re.search(r"`([A-Za-z_]\w*)\(([^()]*)\)`", text)
        if function is None or match is None:
            return False
        documented = [name.strip() for name in match.group(2).split(",") if name.strip()]
        actual = [arg.arg for arg in [*function.args.posonlyargs, *function.args.args]]
        actual += [arg.arg for arg in function.args.kwonlyargs]
        if function.args.vararg:
            actual.append(function.args.vararg.arg)
        if function.args.kwarg:
            actual.append(function.args.kwarg.arg)
        return documented == actual
    if kind == "cli_flag":
        for tree in trees:
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
                    if any(isinstance(arg, ast.Constant) and arg.value == subject for arg in node.args):
                        return True
        return False
    if kind == "environment_variable":
        for tree in trees:
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"getenv", "get"}:
                    if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == subject:
                        return True
        return False
    if kind == "file_path":
        return (repo / subject).is_file()
    if kind == "python_version":
        documented = re.search(r"Python\s+(\d+\.\d+)", text)
        try:
            metadata = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
            requirement = metadata["project"]["requires-python"]
        except (OSError, KeyError, tomllib.TOMLDecodeError):
            return False
        source = re.fullmatch(r">=(\d+\.\d+)", requirement)
        return bool(documented and source and documented.group(1) == source.group(1))
    if kind == "route":
        match = re.search(r"\b(GET|POST)\s+`([^`]+)`", text)
        if not match:
            return False
        expected_method, expected_path = match.groups()
        for tree in trees:
            for node in ast.walk(tree):
                for decorator in getattr(node, "decorator_list", []):
                    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                        continue
                    path = decorator.args[0] if decorator.args else None
                    if (decorator.func.attr.upper() == expected_method
                            and isinstance(path, ast.Constant) and path.value == expected_path):
                        return True
        return False
    raise ValueError(f"unsupported benchmark claim type: {kind}")


def _code_symbols(repo: Path) -> set[str]:
    trees, _ = _read_python(repo)
    symbols: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.add(node.name)
                symbols.update(arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs])
            elif isinstance(node, ast.arg):
                symbols.add(node.arg)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                symbols.add(node.value)
    for path in repo.rglob("*"):
        if path.is_file():
            symbols.add(path.relative_to(repo).as_posix())
    try:
        meta = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
        version = re.search(r"\d+\.\d+", meta["project"]["requires-python"])
        if version:
            symbols.add(version.group(0))
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        pass
    return symbols


def n1_verdict(manifest: dict[str, Any], statement: dict[str, Any], symbols: set[str]) -> str:
    if manifest["mutated"] and statement["subject"] and statement["subject"] in symbols:
        return "false"
    return "none"


def _line_verdict(findings: list[dict[str, Any]], path: str, line: int) -> tuple[str, str | None]:
    matched = [finding for finding in findings
               if finding.get("claim", {}).get("document_path") == path
               and finding.get("claim", {}).get("line") == line]
    verdicts = [finding.get("verdict") for finding in matched]
    if "false" in verdicts:
        explanation = next((item.get("explanation") for item in matched if item.get("verdict") == "false"), None)
        return "false", explanation
    if "true" in verdicts:
        return "true", None
    if "unverifiable" in verdicts:
        return "unverifiable", None
    return "none", None


def _reason(label: str, verdict: str) -> str | None:
    if label in {"correct", "decoy"} and verdict == "false":
        return "false_verdict"
    if label == "drifted" and verdict != "false":
        return {"none": "not_extracted", "unverifiable": "unverifiable", "true": "judged_true"}[verdict]
    return None


def evaluate_case(case_dir: Path) -> tuple[list[dict[str, Any]], float]:
    manifest_path = case_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(case_dir, manifest)
    output = io.StringIO()
    errors = io.StringIO()
    started = time.perf_counter()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        status = cli_main(["check", "--repo", str(case_dir / "repo"), "--format", "json"])
    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        result = json.loads(output.getvalue())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"CLI returned invalid JSON for {manifest['case_id']}: {errors.getvalue()}") from exc
    if status not in {0, 1} or result.get("errors"):
        raise RuntimeError(f"CLI analysis failed for {manifest['case_id']}: {result.get('errors')}")
    symbols = _code_symbols(case_dir / "repo")
    rows: list[dict[str, Any]] = []
    for statement in manifest["statements"]:
        verdict, explanation = _line_verdict(result["findings"], statement["path"], statement["line"])
        baseline = n1_verdict(manifest, statement, symbols)
        reason = _reason(statement["label"], verdict)
        if reason == "false_verdict":
            reason = explanation or reason
        rows.append({
            "case_id": manifest["case_id"],
            "mutated": manifest["mutated"],
            **statement,
            "verdict": verdict,
            "n1_verdict": baseline,
            "explanation": explanation,
            "reason": reason,
            "extracted": verdict != "none",
        })
    return rows, elapsed_ms


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def metrics(rows: list[dict[str, Any]], verdict_key: str = "verdict") -> dict[str, Any]:
    flagged_rows = [row for row in rows if row[verdict_key] == "false"]
    true_positive = sum(row["label"] == "drifted" for row in flagged_rows)
    false_positive = sum(row["label"] in {"correct", "decoy"} for row in flagged_rows)
    drifted = sum(row["label"] == "drifted" for row in rows)
    false_negative = sum(row["label"] == "drifted" and row[verdict_key] != "false" for row in rows)
    correct = [row for row in rows if row["label"] == "correct"]
    extracted = sum(bool(row.get("extracted", row[verdict_key] != "none")) for row in rows)
    abstained_correct = sum(row[verdict_key] == "unverifiable" for row in correct)
    precision = _rate(true_positive, len(flagged_rows))
    recall = _rate(true_positive, drifted)
    f1 = None if precision is None or recall is None or precision + recall == 0 else round(
        2 * precision * recall / (precision + recall), 6
    )
    return {
        "statements": len(rows),
        "drifted": drifted,
        "flagged": len(flagged_rows),
        "TP": true_positive,
        "FP": false_positive,
        "FN": false_negative,
        "precision": precision,
        "recall": recall,
        "F1": f1,
        "extraction_rate": _rate(extracted, len(rows)),
        "abstain_rate_on_correct": _rate(abstained_correct, len(correct)),
    }


def _group_metrics(rows: list[dict[str, Any]], key: str, verdict_key: str = "verdict") -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "none")].append(row)
    return {name: metrics(groups[name], verdict_key) for name in sorted(groups)}


def _operator_metrics(rows: list[dict[str, Any]], verdict_key: str = "verdict") -> dict[str, dict[str, Any]]:
    cases_by_operator: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("operator"):
            cases_by_operator[row["operator"]].add(row["case_id"])
    result: dict[str, dict[str, Any]] = {}
    for operator, case_ids in sorted(cases_by_operator.items()):
        cohort = [row for row in rows if row["case_id"] in case_ids
                  and (row["label"] != "drifted" or row.get("operator") == operator)]
        result[operator] = metrics(cohort, verdict_key)
    return result


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _top_causes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(row.get("reason") or (row.get("explanation") or "false_verdict")
                     for row in rows if row.get("reason") is not None)
    return [{"cause": cause, "count": count} for cause, count in counts.most_common(5)]


def _failure_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures = []
    for row in rows:
        if row["reason"] is not None:
            failures.append({key: row.get(key) for key in (
                "case_id", "path", "line", "text", "label", "claim_type", "category", "verdict", "reason"
            )})
    return failures


def run_benchmark(dataset_root: Path, out: Path, *, seed: int | None = None,
                  progress: bool = True) -> dict[str, Any]:
    cases = sorted(path for path in dataset_root.iterdir() if path.is_dir() and (path / "manifest.json").is_file())
    all_rows: list[dict[str, Any]] = []
    latency_by_case: list[dict[str, Any]] = []
    elapsed: list[float] = []
    for index, case_dir in enumerate(cases, 1):
        rows, duration = evaluate_case(case_dir)
        all_rows.extend(rows)
        elapsed.append(duration)
        latency_by_case.append({"case_id": case_dir.name, "latency_ms": round(duration, 3)})
        if progress and index % 10 == 0:
            print(f"processed {index}/{len(cases)} repos")
    engine = {
        "overall": metrics(all_rows),
        "per_claim_type": _group_metrics(all_rows, "claim_type"),
        "per_category": _group_metrics(all_rows, "category"),
        "per_operator": _operator_metrics(all_rows),
    }
    n1 = {
        "overall": metrics(all_rows, "n1_verdict"),
        "per_claim_type": _group_metrics(all_rows, "claim_type", "n1_verdict"),
        "per_category": _group_metrics(all_rows, "category", "n1_verdict"),
        "per_operator": _operator_metrics(all_rows, "n1_verdict"),
    }
    result = {
        "schema_version": 1,
        "seed": seed,
        "case_count": len(cases),
        "statement_count": len(all_rows),
        "engine": engine,
        "N1_flag_all_mentions": n1,
        "latency_ms": {
            "p50": _percentile(elapsed, 0.50),
            "p95": _percentile(elapsed, 0.95),
            "per_case": latency_by_case,
        },
        "failure_causes": {
            "FP": _top_causes([row for row in all_rows if row["label"] in {"correct", "decoy"}
                                and row["verdict"] == "false"]),
            "FN": _top_causes([row for row in all_rows if row["label"] == "drifted" and row["reason"]]),
        },
        "limitations": [
            "The dataset is synthetic and template-generated; these metrics indicate harness behavior, not real-world accuracy.",
            "Per-category recall shows where the extractor does not look; a real-repository dataset is still pending.",
            "Blueprint targets are goals and are not benchmark results.",
            "N1 is deliberately generous: it flags mentions in mutated cases and never flags unmutated controls.",
        ],
    }
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "results.json", result)
    _write_text(out / "failures.csv", _csv_text(_failure_rows(all_rows)))
    markdown = render_markdown(result)
    _write_text(out / "results.md", markdown)
    latest_md = ROOT / "benchmark/results/latest.md"
    _write_text(latest_md, markdown)
    return result


def _csv_text(rows: list[dict[str, Any]]) -> str:
    columns = ("case_id", "path", "line", "text", "label", "claim_type", "category", "verdict", "reason")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _table(title: str, groups: dict[str, dict[str, Any]]) -> list[str]:
    lines = [f"### {title}", "", "| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, value in groups.items():
        cell = lambda key: "null" if value[key] is None else str(value[key])
        lines.append(f"| {name} | {value['statements']} | {value['TP']} | {value['FP']} | {value['FN']} | "
                     f"{cell('precision')} | {cell('recall')} | {cell('F1')} | "
                     f"{cell('extraction_rate')} | {cell('abstain_rate_on_correct')} |")
    lines.append("")
    return lines


def render_markdown(result: dict[str, Any]) -> str:
    engine = result["engine"]
    n1 = result["N1_flag_all_mentions"]
    overall = engine["overall"]
    baseline = n1["overall"]
    lines = [
        "# Deterministic DocDrift synthetic benchmark",
        "",
        f"Seed: `{result['seed']}`<br>",
        f"Repos: `{result['case_count']}`; statements: `{result['statement_count']}`<br>",
        f"Case latency p50/p95: `{result['latency_ms']['p50']}` / `{result['latency_ms']['p95']}` ms",
        "",
        "## Overall",
        "",
        "| System | TP | FP | FN | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| DocDrift | {overall['TP']} | {overall['FP']} | {overall['FN']} | {overall['precision']} | {overall['recall']} | {overall['F1']} |",
        f"| N1 flag-all-mentions | {baseline['TP']} | {baseline['FP']} | {baseline['FN']} | {baseline['precision']} | {baseline['recall']} | {baseline['F1']} |",
        "",
    ]
    lines.extend(_table("Per claim type", engine["per_claim_type"]))
    lines.extend(_table("Per phrasing category", engine["per_category"]))
    lines.extend(_table("Per mutation operator", engine["per_operator"]))
    lines.extend(["## Failure causes", "", "### False positives", ""])
    lines.extend(f"- {item['cause']}: {item['count']}" for item in result["failure_causes"]["FP"])
    lines.extend(["", "### False negatives", ""])
    lines.extend(f"- {item['cause']}: {item['count']}" for item in result["failure_causes"]["FN"])
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in result["limitations"])
    lines.append("")
    return "\n".join(lines)


def baseline_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "overall": {
            "precision": result["engine"]["overall"]["precision"],
            "recall": result["engine"]["overall"]["recall"],
        },
        "per_claim_type_recall": {
            name: values["recall"] for name, values in result["engine"]["per_claim_type"].items()
        },
    }


def compare_baseline(current: dict[str, Any], baseline: dict[str, Any], tolerance: float) -> list[str]:
    failures: list[str] = []
    current_overall = current["engine"]["overall"]
    for metric in ("precision", "recall"):
        prior = baseline.get("overall", {}).get(metric)
        now = current_overall.get(metric)
        if isinstance(prior, (int, float)) and isinstance(now, (int, float)) and now < prior - tolerance:
            failures.append(f"overall {metric} dropped from {prior:.4f} to {now:.4f} (tolerance {tolerance:.4f})")
    current_types = current["engine"]["per_claim_type"]
    for claim_type, prior in baseline.get("per_claim_type_recall", {}).items():
        now = current_types.get(claim_type, {}).get("recall")
        if isinstance(prior, (int, float)) and isinstance(now, (int, float)) and now < prior - tolerance:
            failures.append(f"{claim_type} recall dropped from {prior:.4f} to {now:.4f} (tolerance {tolerance:.4f})")
    return failures


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m benchmark.harness")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="create seeded synthetic repositories")
    generate.add_argument("--seed", type=int, default=1337)
    generate.add_argument("--repos", type=int, default=60)
    generate.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET)
    run = commands.add_parser("run", help="run the shipped CLI over a dataset")
    run.add_argument("--seed", type=int, default=1337)
    run.add_argument("--repos", type=int, default=60)
    run.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET)
    run.add_argument("--out", type=Path, default=DEFAULT_OUT)
    run.add_argument("--write-baseline", action="store_true")
    check = commands.add_parser("check", help="compare latest metrics to a baseline")
    check.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    check.add_argument("--tolerance", type=float, default=0.02)
    check.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "generate":
            manifests = generate_dataset(args.dataset_root, args.seed, args.repos)
            print(f"generated {len(manifests)} repos at {args.dataset_root}")
            return 0
        if args.command == "run":
            result = run_benchmark(args.dataset_root, args.out, seed=args.seed, progress=True)
            if args.write_baseline:
                _write_json(ROOT / "benchmark/baseline.json", baseline_payload(result))
                print(f"wrote baseline: {ROOT / 'benchmark/baseline.json'}")
            print(f"results: {args.out / 'results.json'}")
            return 0
        current = _load_json(args.out / "results.json")
        baseline = _load_json(args.baseline)
        failures = compare_baseline(current, baseline, args.tolerance)
        if failures:
            print("baseline regression: " + "; ".join(failures))
            return 1
        print(f"baseline check passed (tolerance {args.tolerance:.4f})")
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"benchmark: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
