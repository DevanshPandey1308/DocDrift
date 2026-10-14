from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from docdrift_core import analyze, build_impact_index, extract_markdown
from docdrift_core.models import Verdict, to_dict


ROOT = Path(__file__).parent / "fixtures/sample_repo"


class CoreTests(unittest.TestCase):
    def test_fixture_finds_seeded_default_drift_and_keeps_controls(self) -> None:
        findings, _, claims = analyze(ROOT, ("docs",), ("src",))
        by_line = {f.claim.line: f for f in findings}
        self.assertEqual(by_line[3].verdict, Verdict.TRUE)
        self.assertEqual(by_line[4].verdict, Verdict.TRUE)
        self.assertEqual(by_line[5].verdict, Verdict.FALSE)
        self.assertEqual(by_line[6].verdict, Verdict.TRUE)
        self.assertEqual(by_line[7].verdict, Verdict.TRUE)
        self.assertEqual(by_line[8].verdict, Verdict.TRUE)
        self.assertGreaterEqual(len(claims), 7)
        for finding in findings:
            for evidence in finding.evidence:
                source_lines = (ROOT / evidence.path).read_text(encoding="utf-8").splitlines()
                self.assertGreater(evidence.line, 0)
                self.assertLessEqual(evidence.line, len(source_lines))
                self.assertEqual(evidence.snippet, source_lines[evidence.line - 1].strip())

    def test_multiple_docs_and_fences_are_handled(self) -> None:
        claims = extract_markdown("`one(a)` accepts it.\n```\n`fake(b)` accepts\n```\n`two(b)` accepts it.", "x.md")
        self.assertEqual([c.subject for c in claims], ["one", "two"])

    def test_path_traversal_abstains_and_missing_file_is_false(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "src.py").write_text("def ok(a): return a\n")
            (root / "docs.md").write_text("See `../secret.py`.\nSee `gone.py`.\n")
            findings, _, _ = analyze(root, ("docs.md",), ("src.py",))
            self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)
            self.assertEqual(findings[1].verdict, Verdict.FALSE)

    def test_syntax_errors_are_structured_and_missing_scope_is_error(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "bad.py").write_text("def broken(:\n")
            (root / "good.py").write_text("def documented(value): return value\n")
            (root / "guide.md").write_text("`documented(value)` accepts a value.\n")
            findings, index, _ = analyze(root, ("guide.md",), (".",))
            self.assertEqual(index.errors[0].kind, "syntax_error")
            self.assertEqual(index.errors[0].line, 1)
            self.assertEqual(index.errors[0].path, "bad.py")
            self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)

    def test_dynamic_flag_declaration_abstains(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "cli.py").write_text("import argparse\np = argparse.ArgumentParser()\nflag = '--runtime'\np.add_argument(flag)\n")
            (root / "guide.md").write_text("Run the CLI with `--runtime`.\n")
            findings, _, _ = analyze(root, ("guide.md",), ("cli.py",))
            self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)

    def test_json_serialization_and_deterministic_impact_index(self) -> None:
        findings, _, claims = analyze(ROOT, ("docs",), ("src",))
        encoded = json.dumps([to_dict(f) for f in findings], sort_keys=True)
        self.assertEqual(encoded, json.dumps([to_dict(f) for f in analyze(ROOT, ("docs",), ("src",))[0]], sort_keys=True))
        self.assertIn("create_user", encoded)
        impact = build_impact_index(claims)
        self.assertEqual(impact, build_impact_index(claims))
        self.assertIn("create_user", impact)


if __name__ == "__main__":
    unittest.main()
