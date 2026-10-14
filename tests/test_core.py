from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from docdrift_core import analyze, build_impact_index, extract_markdown
from docdrift_core.models import Verdict, to_dict


ROOT = Path(__file__).parent / "fixtures/sample_repo"
ROUTE_CLI = Path(__file__).parent / "fixtures/route_cli_repo"
DYNAMIC_ROUTE = Path(__file__).parent / "fixtures/dynamic_route_repo"


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

    def test_tutorial_file_instructions_are_not_path_claims(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "config").mkdir()
            (root / "config/settings.py").write_text("SETTING = True\n")
            (root / "docs/guide.md").write_text(
                "Create a file `main.py` with:\n"
                "Create a file `main.py`:\n"
                "Create a `main.py` file with:\n"
                "Now create a `main.py` with:\n"
                "Create another file/module `app/test_main.py`.\n"
                "Copy that to a file `main.py`.\n"
                "Copy that example from above to a file `main.py`.\n"
                "To run any of the examples, copy the code to a file `main.py`, and run it:\n"
                "To run any of the examples, copy the code to a file `main.py`, and start `fastapi dev` with `uv run`:\n"
                "Now let's start using Typer in your own code, update `main.py` with:\n"
                "Modify the file `main.py`.\n"
                "Now modify the file `main.py` to receive a request body.\n"
                "And then update your `config.py` with:\n"
                "Now you could create a demo file on the same Typer directory in your editor, for example `demo.py`:\n"
                "Then you can write a template at `templates/item.html` with, for example:\n"
                "Here's what we do in `main.py`:\n"
                "The file would live right beside `__init__.py` and `main.py`:\n"
                "Finally, we need to create an `__init__.py` file in the `users` directory to make it a package.\n"
                "Let's start with a file `tutorial.py`:\n"
                "And now the equivalent for managing towns in `towns.py`:\n"
                "Now let's put the commands together in `combined.py`:\n"
                "But to achieve that, we just have to add the module to the same `main.py` file we already had:\n"
                "The example is implemented in `missing_module.py`.\n"
                "The configuration is stored in `config/settings.py`.\n"
                "See `missing_module.py` for the implementation.\n"
            )
            findings, _, claims = analyze(root, ("docs",), (".",))
            self.assertEqual([claim.subject for claim in claims], ["missing_module.py", "config/settings.py", "missing_module.py"])
            self.assertEqual([finding.verdict for finding in findings], [Verdict.FALSE, Verdict.TRUE, Verdict.FALSE])

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

    def test_route_and_framework_flags_have_real_evidence(self) -> None:
        findings, _, _ = analyze(ROUTE_CLI, ("docs",), ("src",))
        by_subject = {(f.claim.type.value, f.claim.subject): f for f in findings}
        health = by_subject[("route", "/health")]
        items = [f for f in findings if f.claim.type.value == "route" and f.claim.expected["method"] == "POST"][0]
        head_items = [f for f in findings if f.claim.type.value == "route" and f.claim.expected["method"] == "HEAD"][0]
        put_items = [f for f in findings if f.claim.type.value == "route" and f.claim.expected["method"] == "PUT"][0]
        click_flag = by_subject[("cli_flag", "--click-count")]
        typer_flag = by_subject[("cli_flag", "--typer-limit")]
        dynamic_flag = by_subject[("cli_flag", "--computed")]
        self.assertEqual(health.verdict, Verdict.TRUE)
        self.assertEqual(health.claim.expected["method"], "GET")
        self.assertEqual(items.verdict, Verdict.FALSE)
        self.assertEqual(items.claim.expected["method"], "POST")
        self.assertEqual(head_items.verdict, Verdict.TRUE)
        self.assertEqual(put_items.verdict, Verdict.TRUE)
        self.assertEqual(click_flag.verdict, Verdict.TRUE)
        self.assertEqual(typer_flag.verdict, Verdict.TRUE)
        self.assertEqual(dynamic_flag.verdict, Verdict.UNVERIFIABLE)
        for finding, expected_path, expected_line in (
            (health, "src/endpoints.py", 8),
            (items, "src/endpoints.py", 13),
            (click_flag, "src/commands.py", 8),
            (typer_flag, "src/commands.py", 14),
        ):
            self.assertEqual(finding.evidence[0].path, expected_path)
            self.assertEqual(finding.evidence[0].line, expected_line)
            actual = (ROUTE_CLI / expected_path).read_text(encoding="utf-8").splitlines()[expected_line - 1].strip()
            self.assertEqual(finding.evidence[0].snippet, actual)
        _, index, _ = analyze(ROUTE_CLI, ("docs",), ("src",))
        self.assertNotIn("--comment-only", index.flags)
        self.assertNotIn("--string-only", index.flags)
        self.assertNotIn("--not-a-cli-command", index.flags)

    def test_dynamic_route_path_abstains(self) -> None:
        findings, _, _ = analyze(DYNAMIC_ROUTE, ("docs",), ("src",))
        self.assertTrue(all(f.verdict is Verdict.UNVERIFIABLE for f in findings))

    def test_signature_conventions_and_ambiguous_symbols_abstain(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "one.py").write_text("def target(required, /, positional, *args, keyword, optional=1, **kwargs): return None\n")
            (root / "guide.md").write_text(
                "`target(required, positional, args, keyword, optional, kwargs)` accepts these names.\n"
                "`target(required, /, positional)` accepts positional-only arguments.\n"
                "`target(required, optional=1)` accepts optional arguments.\n"
                "The default optional is 1.\n"
            )
            findings, _, _ = analyze(root, ("guide.md",), ("one.py",))
            self.assertEqual([f.verdict for f in findings], [Verdict.TRUE, Verdict.UNVERIFIABLE, Verdict.UNVERIFIABLE, Verdict.TRUE])
            (root / "two.py").write_text("def target(required, /, positional, *args, keyword, optional=1, **kwargs): return None\n")
            ambiguous, _, _ = analyze(root, ("guide.md",), (".",))
            self.assertEqual(ambiguous[0].verdict, Verdict.UNVERIFIABLE)

    def test_dynamic_default_is_not_reported_as_absent_or_literal(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "source.py").write_text("def configure(limit=runtime_value): return limit\n")
            (root / "guide.md").write_text("The default limit is 3.\n")
            findings, _, _ = analyze(root, ("guide.md",), ("source.py",))
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
