"""Mine and score human-reviewed documentation changes without network access."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import sys
import tarfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from docdrift_core import analyze
from docdrift_core.models import Claim, Finding, Verdict

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPOS = ROOT / "benchmark/real/repos"
DEFAULT_CANDIDATES = ROOT / "benchmark/real/candidates.csv"
DEFAULT_CASES = ROOT / "benchmark/real/cases.json"
DEFAULT_FP_REVIEW = ROOT / "benchmark/real/fp_review.csv"
DEFAULT_RESULTS = ROOT / "benchmark/results/real"
CANDIDATE_FIELDS = [
    "id", "repo", "sha", "parent_sha", "doc_path", "old_line_no", "old_line",
    "new_line_no", "new_line", "claim_type_guess", "commit_subject", "review_cmd",
    "human_label", "notes",
]
FP_FIELDS = [
    "id", "repo", "doc_path", "line", "claim_type", "claim_text", "evidence",
    "verdict_reason", "review_cmd", "human_label", "notes",
]
CASE_FIELDS = [
    "id", "repo", "url", "parent_sha", "sha", "doc_path", "old_line_no",
    "old_line", "new_line_no", "new_line", "claim_type_guess",
]
SUPPORTED_EXTENSIONS = {".py", ".md", ".rst", ".txt", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".json"}
TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("python_version", re.compile(r"\b\d+\.\d+(?:\.\d+)?\b")),
    ("default_value", re.compile(r"(?<!\w)-?\d+(?:\.\d+)?(?!\w)")),
    ("cli_flag", re.compile(r"--[A-Za-z][\w-]*")),
    ("environment_variable", re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")),
    ("file_path", re.compile(r"(?:[\w.-]+/)+[\w.-]+|\b[\w.-]+\.(?:py|toml|ya?ml|json|md|rst|txt|cfg|ini)\b", re.IGNORECASE)),
    ("other", re.compile(r"`[^`]+`")),
)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, encoding="utf-8",
                            errors="replace", capture_output=True, check=False)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result


def _repos(repos_dir: Path) -> list[tuple[str, Path]]:
    if not repos_dir.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for path in sorted(repos_dir.iterdir(), key=lambda item: item.name.casefold()):
        if path.is_dir() and _git(path, "rev-parse", "--git-dir", check=False).returncode == 0:
            found.append((path.name, path))
    return found


def _repository_urls() -> dict[str, str]:
    path = ROOT / "benchmark/real/repos.json"
    try:
        return {str(item["name"]): str(item["url"]) for item in json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _csv_write(path: Path, fields: list[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _csv_read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_hunks(diff_lines: Iterable[str]) -> list[dict[str, object]]:
    """Return equal-sized, non-whitespace replacement pairs with one-based lines."""
    output: list[dict[str, object]] = []
    old_line_no = new_line_no = 0
    removed: list[tuple[int, str]] = []
    added: list[tuple[int, str]] = []
    counts: tuple[int, int] | None = None

    def flush() -> None:
        if counts is None or counts[0] != counts[1] or not removed or len(removed) != len(added):
            return
        for (old_no, old_text), (new_no, new_text) in zip(removed, added):
            if old_text.strip() == new_text.strip():
                continue
            kind = guess_claim_type(old_text, new_text)
            if kind is not None:
                output.append({"old_line_no": old_no, "old_line": old_text,
                               "new_line_no": new_no, "new_line": new_text,
                               "claim_type_guess": kind})

    for line in diff_lines:
        if line.startswith("@@ "):
            flush()
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if match:
                old_start_text, old_count, new_start_text, new_count = match.groups()
                removed, added = [], []
                counts = (int(old_count or 1), int(new_count or 1))
                old_line_no, new_line_no = int(old_start_text), int(new_start_text)
            else:
                counts = None
            continue
        if counts is None:
            continue
        if line.startswith("-") and not line.startswith("---"):
            removed.append((old_line_no, line[1:]))
            old_line_no += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added.append((new_line_no, line[1:]))
            new_line_no += 1
        elif line.startswith(" "):
            old_line_no += 1
            new_line_no += 1
    flush()
    return output


def guess_claim_type(old: str, new: str) -> str | None:
    for name, pattern in TOKEN_PATTERNS:
        if set(pattern.findall(old)) != set(pattern.findall(new)):
            return name
    return None


def _doc_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = PurePosixPath(normalized).name.casefold()
    is_root_readme = "/" not in normalized and name.startswith("readme")
    return (is_root_readme or normalized.casefold().startswith("docs/")) and PurePosixPath(normalized).suffix.lower() in {".md", ".markdown", ".rst", ".txt"}


def _mine_repo(name: str, repo: Path, max_commits: int) -> tuple[list[dict[str, object]], int]:
    command = ["git", "-C", str(repo), "log", "--no-merges", f"-n{max_commits}", "-U0",
               "--format=@@COMMIT%x09%H%x09%P%x09%s", "--", "*.md", "*.markdown", "*.rst", "*.txt"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               encoding="utf-8", errors="replace")
    rows: list[dict[str, object]] = []
    commit_meta: tuple[str, str, str] | None = None
    commit_rows: list[dict[str, object]] = []
    commit_doc_paths: set[str] = set()
    commit_changed_lines = 0
    diff: list[str] = []
    file_path: str | None = None
    considered = 0

    def flush_file() -> None:
        nonlocal diff, file_path, commit_changed_lines
        if file_path and _doc_path(file_path):
            commit_doc_paths.add(file_path)
            commit_changed_lines += sum(1 for item in diff if item.startswith(("+", "-")) and not item.startswith(("+++", "---")))
            for change in parse_hunks(diff):
                commit_rows.append({"repo": name, "sha": commit_meta[0] if commit_meta else "",
                                    "parent_sha": (commit_meta[1].split()[0] if commit_meta and commit_meta[1] else ""),
                                    "doc_path": file_path, "commit_subject": commit_meta[2] if commit_meta else "",
                                    **change})
        diff, file_path = [], None

    def finish_commit() -> None:
        nonlocal considered, commit_rows, commit_doc_paths, commit_changed_lines
        if commit_meta is None:
            return
        considered += 1
        if (commit_meta[1] and len(commit_doc_paths) <= 40
                and commit_changed_lines <= 400):
            rows.extend(commit_rows)
        commit_rows, commit_doc_paths, commit_changed_lines = [], set(), 0
        if considered % 100 == 0:
            print(f"{name}: scanned {considered} commits", flush=True)

    assert process.stdout is not None
    for line in process.stdout:
        if line.startswith("@@COMMIT\t"):
            flush_file()
            finish_commit()
            parts = line.rstrip("\r\n").split("\t", 3)
            commit_meta = (parts[1], parts[2], parts[3] if len(parts) > 3 else "") if len(parts) >= 3 else None
            continue
        if line.startswith("diff --git "):
            flush_file()
            continue
        if line.startswith("+++ b/"):
            file_path = line[6:].rstrip("\r\n")
            continue
        if file_path is not None:
            diff.append(line.rstrip("\r\n"))
    flush_file()
    finish_commit()
    stderr = process.stderr.read() if process.stderr else ""
    code = process.wait()
    if code:
        raise RuntimeError(stderr.strip() or f"git log failed for {name}")
    return rows, considered


def _round_robin(rows: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in sorted(rows, key=lambda item: (str(item["sha"]), str(item["doc_path"]), int(str(item["old_line_no"])) )):
        groups[str(row["claim_type_guess"])].append(row)
    selected: list[dict[str, object]] = []
    types = sorted(groups)
    while types and len(selected) < limit:
        remaining: list[str] = []
        for claim_type in types:
            if groups[claim_type] and len(selected) < limit:
                selected.append(groups[claim_type].pop(0))
            if groups[claim_type]:
                remaining.append(claim_type)
        types = remaining
    return selected


def mine(args: argparse.Namespace) -> int:
    all_rows: list[dict[str, object]] = []
    repos = _repos(args.repos_dir)
    for name, repo in repos:
        rows, commits = _mine_repo(name, repo, args.max_commits)
        capped = _round_robin(rows, args.per_repo)
        all_rows.extend(capped)
        print(f"{name}: scanned {commits} commits, candidates {len(capped)} (raw {len(rows)})")
    balanced = _round_robin(all_rows, args.max_total)
    for index, row in enumerate(sorted(balanced, key=lambda item: (str(item["repo"]), str(item["sha"]), str(item["doc_path"]), int(str(item["old_line_no"])) )), 1):
        row["id"] = f"real-{index:05d}"
        row["review_cmd"] = f"git -C benchmark/real/repos/{row['repo']} show {row['sha']} -- {row['doc_path']}"
        row["human_label"] = ""
        row["notes"] = ""
    ordered = sorted(balanced, key=lambda item: str(item["id"]))
    _csv_write(args.out, CANDIDATE_FIELDS, ordered)
    print(f"wrote {len(ordered)} candidates to {args.out}")
    return 0


def build(args: argparse.Namespace) -> int:
    rows = _csv_read(args.candidates)
    invalid = [str(row.get("id", "")) for row in rows if row.get("human_label", "").strip() and row["human_label"].strip() not in {"drifted", "not_drift", "skip"}]
    if invalid:
        raise ValueError("invalid human_label for candidate ids: " + ", ".join(invalid))
    urls = _repository_urls()
    cases = [{key: row.get(key, "") for key in CASE_FIELDS if key not in {"url"}}
             for row in rows if row.get("human_label", "").strip() == "drifted"]
    for case in cases:
        case["url"] = urls.get(str(case.get("repo", "")), "")
    args.cases.parent.mkdir(parents=True, exist_ok=True)
    args.cases.write_text(json.dumps(cases, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"built {len(cases)} drifted cases at {args.cases}")
    return 0


def wilson(successes: int, total: int) -> tuple[float, float] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _safe_snapshot(repo: Path, revision: str, target: Path) -> int:
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision):
        raise ValueError("snapshot revision must be a full commit SHA")
    if target.is_symlink():
        raise ValueError("snapshot target cannot be a symlink")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(["git", "-C", str(repo), "archive", revision], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    written = 0
    try:
        assert process.stdout is not None
        with tarfile.open(fileobj=process.stdout, mode="r|*") as archive:
            for member in archive:
                pure = PurePosixPath(member.name)
                if pure.is_absolute() or ".." in pure.parts or not member.isfile():
                    continue
                name = pure.name
                if pure.suffix.lower() not in SUPPORTED_EXTENSIONS and name != "Dockerfile":
                    continue
                if member.size < 0:
                    continue
                if member.size >= 1_000_000:
                    continue
                if written + member.size > 60_000_000:
                    raise ValueError("snapshot size limit exceeded")
                source = archive.extractfile(member)
                if source is None:
                    continue
                destination = target.joinpath(*pure.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                data = source.read(member.size + 1)
                if len(data) != member.size:
                    raise ValueError("archive member size mismatch")
                destination.write_bytes(data)
                written += len(data)
        error = process.stderr.read() if process.stderr else b""
        if process.wait() != 0:
            raise RuntimeError(error.decode("utf-8", "replace"))
    except BaseException:
        process.kill()
        process.wait()
        raise
    return written


def _finding_on_line(root: Path, doc_path: str, line_no: int, line_text: str, claim_type: str) -> tuple[Finding | None, list[Finding], list[Claim]]:
    document = (root / doc_path).resolve()
    if (not document.is_relative_to(root.resolve()) or not document.is_file()
            or Path(doc_path).suffix.lower() not in {".md", ".markdown"}):
        return None, [], []
    findings, _, claims = analyze(root, (doc_path,), (".",))
    matching_claims = [claim for claim in claims if claim.document_path == doc_path and claim.line == line_no
                       and claim.type.value == claim_type and claim.text.strip() == line_text.strip()]
    matching_ids = {claim.id for claim in matching_claims}
    matching_findings = [finding for finding in findings if finding.claim.id in matching_ids]
    return (matching_findings[0] if len(matching_findings) == 1 else None), findings, claims


def _load_cases(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise TypeError("cases file must contain a JSON array")
    return raw


def evaluate(args: argparse.Namespace) -> int:
    cases = _load_cases(args.cases)
    args.out.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    skipped = 0
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    overall = [0, 0]
    fixed_false_count = 0
    coverage_extracted: dict[str, int] = defaultdict(int)
    coverage_verdicts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    failures: list[dict[str, object]] = []
    for index, case in enumerate(cases, 1):
        repo_name = str(case.get("repo", ""))
        case_id = str(case.get("id", f"case-{index:05d}"))
        claim_type = str(case.get("claim_type_guess", "other"))
        result: dict[str, object] = {"id": case_id, "repo": repo_name, "claim_type": claim_type}
        try:
            repo_root = DEFAULT_REPOS.resolve()
            repo = (repo_root / repo_name).resolve()
            if not repo.is_relative_to(repo_root) or _git(repo, "rev-parse", "--git-dir", check=False).returncode != 0:
                raise ValueError("case repository is unavailable")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", case_id):
                raise ValueError("case id is invalid")
            snapshots_root = (ROOT / "benchmark/real/snapshots").resolve()
            base = snapshots_root / case_id
            if base.is_symlink() or not base.resolve().is_relative_to(snapshots_root):
                raise ValueError("snapshot path escapes the snapshots directory")
            parent_root, fixed_root = base / "parent", base / "fixed"
            _safe_snapshot(repo, str(case["parent_sha"]), parent_root)
            _safe_snapshot(repo, str(case["sha"]), fixed_root)
            old_finding, parent_findings, parent_claims = _finding_on_line(parent_root, str(case["doc_path"]), int(case["old_line_no"]), str(case["old_line"]), claim_type)
            new_finding, _, _ = _finding_on_line(fixed_root, str(case["doc_path"]), int(case["new_line_no"]), str(case["new_line"]), claim_type)
            if old_finding is None:
                outcome = "FN"
                reason = "not_extracted"
            elif old_finding.verdict is Verdict.FALSE:
                outcome, reason = "TP", "drifted_line_false_at_parent"
            else:
                outcome = "FN"
                reason = "judged_true" if old_finding.verdict is Verdict.TRUE else "unverifiable"
            fixed_is_false = bool(new_finding is not None and new_finding.verdict is Verdict.FALSE)
            result.update({"outcome": outcome, "reason": reason, "fixed_false": fixed_is_false,
                           "parent_verdict": old_finding.verdict.value if old_finding else "not_extracted",
                           "fixed_verdict": new_finding.verdict.value if new_finding else "not_extracted"})
            totals[claim_type][0] += int(outcome == "TP")
            totals[claim_type][1] += 1
            overall[0] += int(outcome == "TP")
            overall[1] += 1
            fixed_false_count += int(fixed_is_false)
            for claim in parent_claims:
                coverage_extracted[claim.type.value] += 1
            for finding in parent_findings:
                coverage_verdicts[finding.claim.type.value][finding.verdict.value] += 1
            if outcome == "FN" or fixed_is_false:
                failures.append({"id": case_id, "repo": repo_name, "doc_path": case.get("doc_path", ""),
                                 "claim_type": claim_type, "reason": reason, "fixed_false": fixed_is_false})
        except (OSError, ValueError, KeyError, RuntimeError, tarfile.TarError, subprocess.SubprocessError) as exc:
            skipped += 1
            totals[claim_type][1] += 1
            overall[1] += 1
            result.update({"outcome": "FN", "reason": "snapshot_error", "detail": str(exc)[:240]})
            failures.append({"id": case_id, "repo": repo_name, "doc_path": case.get("doc_path", ""),
                             "claim_type": claim_type, "reason": "snapshot_error", "fixed_false": False})
        results.append(result)
        if index % 25 == 0:
            print(f"evaluated {index}/{len(cases)} cases")
    _write_json(args.out / "results.json", results)
    _csv_write(args.out / "failures.csv", ["id", "repo", "doc_path", "claim_type", "reason", "fixed_false"], failures)
    report = ["# Real-repository evaluation", "", f"Cases: {len(cases)}", f"Skipped snapshots/cases: {skipped}", f"Fixed-revision false findings: {fixed_false_count}", "", "## Overall recall", ""]
    overall_interval = wilson(overall[0], overall[1])
    if overall[1] < 10:
        report.append(f"{overall[0]}/{overall[1]}; n too small for a rate")
    elif overall_interval:
        report.append(f"{overall[0]/overall[1]:.3f} ({overall_interval[0]:.3f}–{overall_interval[1]:.3f})")
    report += ["", "## Recall by claim type", "", "| Claim type | TP | Total | Recall / 95% Wilson interval |", "|---|---:|---:|---|"]
    for kind in sorted(totals):
        tp, total = totals[kind]
        interval = wilson(tp, total)
        metric = f"{tp}/{total}; n too small for a rate" if total < 10 else f"{tp/total:.3f} ({interval[0]:.3f}–{interval[1]:.3f})" if interval else "n/a"
        report.append(f"| {kind} | {tp} | {total} | {metric} |")
    if not cases:
        print("0 reviewed cases")
    report += ["", "## Extraction coverage", "", "Extracted claims and verdicts across the complete reviewed parent documents; this sample is not random.", ""]
    for kind in sorted(set(coverage_extracted) | set(coverage_verdicts)):
        verdicts = ", ".join(f"{name}={count}" for name, count in sorted(coverage_verdicts[kind].items()))
        report.append(f"- {kind}: {coverage_extracted[kind]} claims; verdicts {verdicts or 'none'}")
    (args.out / "results.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"evaluated {len(cases)} cases; skipped {skipped}; output {args.out}")
    return 0


def _current_doc_paths(repo: Path) -> list[str]:
    result = _git(repo, "ls-files", "--", "README*", "docs")
    return [line for line in result.stdout.splitlines() if _doc_path(line)]


def fp_sample(args: argparse.Namespace) -> int:
    sampled: list[dict[str, object]] = []
    for name, repo in _repos(args.repos_dir):
        findings, _, _ = analyze(repo, tuple(_current_doc_paths(repo)), (".",))
        false_findings = [finding for finding in findings if finding.verdict is Verdict.FALSE]
        for finding in false_findings:
            evidence = "; ".join(f"{item.path}:{item.line} {item.snippet}" for item in finding.evidence)
            sampled.append({"id": f"{name}:{finding.claim.id}", "repo": name,
                            "doc_path": finding.claim.document_path, "line": finding.claim.line,
                            "claim_type": finding.claim.type.value, "claim_text": finding.claim.text,
                            "evidence": evidence, "verdict_reason": finding.explanation,
                            "review_cmd": f"git -C benchmark/real/repos/{name} show HEAD -- {finding.claim.document_path}",
                            "human_label": "", "notes": ""})
        print(f"{name}: FALSE findings {len(false_findings)}")
    ordered = sorted(sampled, key=lambda row: (str(row["repo"]), str(row["doc_path"]), int(str(row["line"])), str(row["id"])))
    selected = _even_repo_sample(ordered, args.max)
    _csv_write(args.out, FP_FIELDS, selected)
    print(f"wrote {len(selected)} of {len(ordered)} FALSE findings to {args.out}")
    return 0


def _even_repo_sample(rows: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[str(row["repo"])].append(row)
    selected: list[dict[str, object]] = []
    repo_names = sorted(groups)
    while repo_names and len(selected) < max(0, limit):
        remaining: list[str] = []
        for repo_name in repo_names:
            if groups[repo_name] and len(selected) < limit:
                selected.append(groups[repo_name].pop(0))
            if groups[repo_name]:
                remaining.append(repo_name)
        repo_names = remaining
    return selected


def fp_score(args: argparse.Namespace) -> int:
    rows = _csv_read(args.review)
    valid = {"real_drift", "false_positive", "unclear"}
    invalid = [row.get("id", "") for row in rows if row.get("human_label", "").strip() and row["human_label"].strip() not in valid]
    if invalid:
        raise ValueError("invalid human_label for finding ids: " + ", ".join(invalid))
    reviewed = [row for row in rows if row.get("human_label", "").strip() in valid]
    positives = sum(row["human_label"].strip() == "real_drift" for row in reviewed)
    denominator = sum(row["human_label"].strip() in {"real_drift", "false_positive"} for row in reviewed)
    unclear = sum(row["human_label"].strip() == "unclear" for row in reviewed)
    interval = wilson(positives, denominator)
    score = (f"{positives}/{denominator}; n too small for a rate" if denominator < 10 else
             f"{positives/denominator:.3f} ({interval[0]:.3f}–{interval[1]:.3f})" if interval else "n/a")
    lines = ["## Human-reviewed precision sample", "", f"Reviewed: {len(reviewed)}", f"Unclear (excluded): {unclear}", f"Precision (real_drift / definite labels): {score}", "", "### By claim type", ""]
    kinds = sorted({row.get("claim_type", "unknown") for row in reviewed})
    for kind in kinds:
        subset = [row for row in reviewed if row.get("claim_type") == kind]
        n = sum(row["human_label"].strip() != "unclear" for row in subset)
        yes = sum(row["human_label"].strip() == "real_drift" for row in subset)
        lines.append(f"- {kind}: {yes}/{n}; n too small for a rate" if n < 10 else f"- {kind}: {yes/n:.3f} ({wilson(yes, n)})")
    if not reviewed:
        print("0 reviewed findings")
    args.out.mkdir(parents=True, exist_ok=True)
    report_path = args.out / "results.md"
    existing = report_path.read_text(encoding="utf-8") if report_path.exists() else "# Real-repository evaluation\n"
    marker = "## Human-reviewed precision sample"
    if marker in existing:
        existing = existing[:existing.index(marker)].rstrip() + "\n\n"
    report_path.write_text(existing.rstrip() + "\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    bug_rows = [row for row in reviewed if row["human_label"].strip() == "real_drift"]
    bug_md = ["# Human-confirmed documentation bugs", ""]
    for row in bug_rows:
        bug_md += [f"## {row.get('repo')} — {row.get('doc_path')}:{row.get('line')}", "", str(row.get("claim_text", "")), "", f"Evidence: {row.get('evidence', '')}", ""]
    (args.out / "found_doc_bugs.md").write_text("\n".join(bug_md), encoding="utf-8")
    print(f"scored {len(reviewed)} reviewed findings; precision {score}")
    return 0


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m benchmark.real")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("mine")
    command.add_argument("--repos-dir", type=Path, default=DEFAULT_REPOS)
    command.add_argument("--max-commits", type=int, default=2500)
    command.add_argument("--per-repo", type=int, default=15)
    command.add_argument("--max-total", type=int, default=150)
    command.add_argument("--out", type=Path, default=DEFAULT_CANDIDATES)
    command.set_defaults(run=mine)
    command = commands.add_parser("build")
    command.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    command.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    command.set_defaults(run=build)
    command = commands.add_parser("evaluate")
    command.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    command.add_argument("--out", type=Path, default=DEFAULT_RESULTS)
    command.set_defaults(run=evaluate)
    command = commands.add_parser("fp-sample")
    command.add_argument("--repos-dir", type=Path, default=DEFAULT_REPOS)
    command.add_argument("--max", type=int, default=150)
    command.add_argument("--out", type=Path, default=DEFAULT_FP_REVIEW)
    command.set_defaults(run=fp_sample)
    command = commands.add_parser("fp-score")
    command.add_argument("--review", type=Path, default=DEFAULT_FP_REVIEW)
    command.add_argument("--out", type=Path, default=DEFAULT_RESULTS)
    command.set_defaults(run=fp_score)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = args.run(args)
        if not isinstance(result, int):
            raise TypeError("benchmark command returned an invalid status")
        return result
    except (OSError, TypeError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"real benchmark: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
