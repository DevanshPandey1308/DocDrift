"""CLI for the reusable DocDrift core."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from docdrift_core import analyze
from docdrift_core.models import Verdict, to_dict


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docdrift", description="Check supported documentation claims against repository source.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="scan documentation and source deterministically")
    check.add_argument("--repo", required=True, metavar="PATH", help="repository root")
    check.add_argument("--docs", action="append", metavar="PATH", help="documentation scope (repeatable; default: repository root)")
    check.add_argument("--source", action="append", metavar="PATH", help="Python source scope (repeatable; default: repository root)")
    check.add_argument("--changed-path", action="append", metavar="PATH", help="repository-relative changed Python source path (repeatable; falls back to full scan if impact is uncertain)")
    check.add_argument("--format", choices=("text", "json"), default="text", help="output format")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.repo).expanduser()
    if not root.exists() or not root.is_dir():
        print(f"docdrift: repository path is not a directory: {root}", file=sys.stderr)
        return 2
    root = root.resolve()
    findings, index, _ = analyze(
        root,
        tuple(args.docs or ["."]),
        tuple(args.source or ["."]),
        changed_paths=tuple(args.changed_path) if args.changed_path else None,
    )
    errors = [to_dict(error) for error in index.errors]
    payload = {"schema_version": "1", "findings": [to_dict(f) for f in findings], "errors": errors}
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        if not findings:
            print("No supported claims found.")
        for finding in findings:
            claim = finding.claim
            print(f"{finding.verdict.value.upper()} {claim.document_path}:{claim.line} [{claim.type.value}] {claim.text.strip()}")
            print(f"  {finding.explanation}")
            for evidence in finding.evidence:
                print(f"  source {evidence.path}:{evidence.line}: {evidence.snippet}")
        for error in index.errors:
            location = f":{error.line}" if error.line else ""
            print(f"ERROR {error.path}{location}: {error.message}", file=sys.stderr)
    if any(error.get("kind") == "configuration_error" for error in errors):
        return 2
    if any(f.verdict is Verdict.FALSE for f in findings):
        return 1
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
