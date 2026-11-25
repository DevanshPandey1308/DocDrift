from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from docdrift_core import analyze, extract_markdown
from docdrift_core.models import ClaimType, Verdict
from docdrift_cli.main import main


FIXTURES = Path(__file__).parent / "fixtures"
PATHS = FIXTURES / "path_edge_cases_repo"
VERSIONS = FIXTURES / "monorepo_versions_repo"
ENV_DEFAULTS = FIXTURES / "env_defaults_repo"
NATURAL_DRIFT = FIXTURES / "natural_phrasing_repo"
NATURAL_CLEAN = FIXTURES / "natural_phrasing_clean_repo"


class PrecisionHardeningTests(unittest.TestCase):
    def test_path_resolution_and_noise_filtering(self) -> None:
        findings, _, claims = analyze(PATHS, ("docs",), (".",))
        self.assertEqual(
            [(claim.subject, finding.verdict) for claim, finding in zip(claims, findings)],
            [
                ("src/existing.py", Verdict.TRUE),
                ("src/", Verdict.TRUE),
                ("docs/missing.md", Verdict.FALSE),
                ("src/missing-name", Verdict.UNVERIFIABLE),
                ("web/unique.md", Verdict.UNVERIFIABLE),
                ("../shared/sibling.md", Verdict.TRUE),
            ],
        )
        self.assertFalse(any(claim.subject.casefold() in {"n/a", "i/o", "and/or", "w/o", "tcp/ip"}
                             for claim in claims))

    def test_python_version_uses_nearest_package_metadata(self) -> None:
        findings, _, _ = analyze(VERSIONS, ("packages",), (".",))
        self.assertEqual(
            [(finding.claim.document_path, finding.verdict) for finding in findings],
            [
                ("packages/pkg_a/README.md", Verdict.TRUE),
                ("packages/pkg_b/README.md", Verdict.FALSE),
                ("packages/pkg_b/README.md", Verdict.TRUE),
            ],
        )
        self.assertEqual(findings[0].evidence[0].path, "packages/pkg_a/pyproject.toml")
        self.assertEqual(findings[1].evidence[0].path, "packages/pkg_b/pyproject.toml")

    def test_environment_noise_requires_context_without_underscore(self) -> None:
        claims = extract_markdown(
            "The labels `KB`, `PING`, and `RIGHT` are output markers.\n"
            "Set the `API_KEY` environment variable.\n"
            "`GET` is the route method.\n`PING` env var sets the target.\n",
            "docs/guide.md",
        )
        self.assertEqual([(claim.type, claim.subject) for claim in claims], [
            (ClaimType.ENV_VAR, "API_KEY"), (ClaimType.ENV_VAR, "PING")
        ])

    def test_default_discovery_skips_hidden_and_vendor_directories(self) -> None:
        with tempfile.TemporaryDirectory(dir=FIXTURES) as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src/api.py").write_text("def count(value=1): return value\n", encoding="utf-8")
            (root / "README.md").write_text("The default value is 1.\n", encoding="utf-8")
            for hidden in (".cache", "node_modules/x", "venv", ".venv", "__pycache__", "site-packages", "dist", "build"):
                folder = root / hidden
                folder.mkdir(parents=True)
                (folder / "README.md").write_text("The default value is 999.\n", encoding="utf-8")
            findings, _, claims = analyze(root, (".",), ("src",))
        self.assertEqual(len(claims), 1)
        self.assertEqual(findings[0].verdict, Verdict.TRUE)

    def test_explicit_hidden_docs_scope_is_still_honored(self) -> None:
        with tempfile.TemporaryDirectory(dir=FIXTURES) as temp:
            root = Path(temp)
            (root / ".cache").mkdir()
            (root / "src").mkdir()
            (root / "src/api.py").write_text("def count(value=1): return value\n", encoding="utf-8")
            (root / ".cache/README.md").write_text("The default value is 1.\n", encoding="utf-8")
            findings, _, claims = analyze(root, (".cache",), ("src",))
        self.assertEqual(len(claims), 1)
        self.assertEqual(findings[0].verdict, Verdict.TRUE)

    def test_explicit_hidden_source_scope_is_still_honored(self) -> None:
        with tempfile.TemporaryDirectory(dir=FIXTURES) as temp:
            root = Path(temp)
            (root / "docs").mkdir()
            (root / ".source").mkdir()
            (root / ".source/api.py").write_text("def count(value=1): return value\n", encoding="utf-8")
            (root / "docs/README.md").write_text("The default value is 1.\n", encoding="utf-8")
            findings, _, _ = analyze(root, ("docs",), (".source",))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].verdict, Verdict.TRUE)

    def test_environment_variable_literal_defaults(self) -> None:
        findings, _, _ = analyze(ENV_DEFAULTS, ("docs",), ("src",))
        findings = [finding for finding in findings if finding.claim.type is ClaimType.DEFAULT]
        self.assertEqual(
            [(finding.claim.subject, finding.verdict) for finding in findings],
            [
                ("DOCDRIFT_MATCH", Verdict.TRUE),
                ("DOCDRIFT_WRONG", Verdict.FALSE),
                ("DOCDRIFT_NO_DEFAULT", Verdict.UNVERIFIABLE),
                ("DOCDRIFT_DYNAMIC", Verdict.UNVERIFIABLE),
                ("DOCDRIFT_UNREAD", Verdict.UNVERIFIABLE),
            ],
        )
        self.assertEqual(findings[0].evidence[0].path, "src/settings.py")
        self.assertIn("os.getenv", findings[0].evidence[0].snippet)

    def test_cli_text_uses_bounded_claim_span_and_json_keeps_full_text(self) -> None:
        paragraph = "x" * 450 + " The default timeout is 30. " + "z" * 522
        self.assertEqual(len(paragraph), 1000)
        with tempfile.TemporaryDirectory(dir=FIXTURES) as temp:
            root = Path(temp)
            (root / "docs").mkdir()
            (root / "src").mkdir()
            (root / "src/api.py").write_text("def connect(timeout=30): return timeout\n", encoding="utf-8")
            (root / "docs/guide.md").write_text(paragraph + "\n", encoding="utf-8")
            text_output = io.StringIO()
            with contextlib.redirect_stdout(text_output):
                text_status = main(["check", "--repo", str(root), "--docs", "docs", "--source", "src"])
            json_output = io.StringIO()
            with contextlib.redirect_stdout(json_output):
                json_status = main(["check", "--repo", str(root), "--docs", "docs", "--source", "src", "--format", "json"])
        self.assertEqual(text_status, 0)
        self.assertEqual(json_status, 0)
        claim_line = text_output.getvalue().splitlines()[0]
        rendered_span = claim_line.split("] ", 1)[1]
        self.assertLessEqual(len(rendered_span), 160)
        parsed = json.loads(json_output.getvalue())
        self.assertEqual(parsed["findings"][0]["claim"]["text"], paragraph)
        expected_id = extract_markdown(paragraph, "docs/guide.md")[0].id
        self.assertEqual(parsed["findings"][0]["claim"]["id"], expected_id)

    def test_natural_phrasing_fixtures_scanned_alone_have_expected_counts(self) -> None:
        drift, _, _ = analyze(NATURAL_DRIFT, (".",), (".",))
        clean, _, _ = analyze(NATURAL_CLEAN, (".",), (".",))
        self.assertEqual(sum(item.verdict is Verdict.FALSE for item in drift), 6)
        self.assertEqual(sum(item.verdict is Verdict.TRUE for item in drift), 2)
        self.assertEqual(sum(item.verdict is Verdict.FALSE for item in clean), 0)

    def test_repository_dogfood_has_no_false_findings(self) -> None:
        root = Path(__file__).parent.parent
        findings, _, _ = analyze(root, ("README.md", "docs"), ("apps", "packages"))
        self.assertFalse([finding for finding in findings if finding.verdict is Verdict.FALSE])


if __name__ == "__main__":
    unittest.main()
