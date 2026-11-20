from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docdrift_core import analyze
from docdrift_core.models import ClaimType, Verdict


class AbsencePolicyTests(unittest.TestCase):
    def _analyze(self, source: str, documentation: str, *, source_name: str = "src/app.py"):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "docs").mkdir()
        source_path = root / source_name
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source, encoding="utf-8")
        (root / "docs/guide.md").write_text(documentation, encoding="utf-8")
        return analyze(root, ("docs",), (source_name,))

    def test_renamed_flag_is_false_with_near_miss_declaration_evidence(self) -> None:
        findings, _, _ = self._analyze(
            "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('--max-renamed', action='store_true')\n",
            "Run the CLI with `--max-records`.\n",
        )
        finding = findings[0]
        self.assertEqual(finding.claim.type, ClaimType.CLI_FLAG)
        self.assertEqual(finding.verdict, Verdict.FALSE)
        self.assertEqual(finding.evidence[0].snippet, "parser.add_argument('--max-renamed', action='store_true')")

    def test_own_cli_context_allows_absence_without_a_similar_declaration(self) -> None:
        findings, _, _ = self._analyze(
            "import argparse\nparser = argparse.ArgumentParser(prog='mytool')\nparser.add_argument('--input')\n",
            "Run the CLI with `--removed`.\n",
        )
        finding = findings[0]
        self.assertEqual(finding.verdict, Verdict.FALSE)
        self.assertEqual(finding.evidence, ())
        self.assertIn("No declaration found", finding.explanation)
        self.assertIn("src/app.py", finding.explanation)

    def test_dynamic_flag_declaration_prevents_absence_false(self) -> None:
        findings, _, _ = self._analyze(
            "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument(build_flag())\n",
            "Run the CLI with `--missing-option`.\n",
        )
        self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)
        self.assertIn("computed or dynamically declared", findings[0].explanation)

    def test_dynamic_flag_in_another_source_file_prevents_absence_false(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "docs").mkdir()
        (root / "src").mkdir()
        (root / "src/cli.py").write_text(
            "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('--known')\n",
            encoding="utf-8",
        )
        (root / "src/extensions.py").write_text(
            "extension_builder.add_argument(build_flag())\n", encoding="utf-8"
        )
        (root / "docs/guide.md").write_text("Run the CLI with `--removed`.\n", encoding="utf-8")
        findings, _, _ = analyze(root, ("docs",), ("src",))
        self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)

    def test_third_party_command_flag_is_unverifiable(self) -> None:
        findings, _, _ = self._analyze(
            "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('--real-option')\n",
            "Run `pytest --cov` to measure coverage.\n",
        )
        finding = next(item for item in findings if item.claim.type is ClaimType.CLI_FLAG)
        self.assertEqual(finding.claim.subject, "--cov")
        self.assertEqual(finding.verdict, Verdict.UNVERIFIABLE)

    def test_builtin_help_and_version_flags_are_never_false(self) -> None:
        findings, _, _ = self._analyze(
            "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('--real-option')\n",
            "Run the CLI with `--help`.\nRun the CLI with `--version`.\nRun the CLI with `-h`.\nRun the CLI with `-V`.\n",
        )
        self.assertEqual(len(findings), 4)
        self.assertTrue(all(item.verdict is Verdict.UNVERIFIABLE for item in findings))

    def test_project_prefixed_missing_environment_name_is_false_with_evidence(self) -> None:
        findings, _, _ = self._analyze(
            "import os\nMODE = os.getenv('APP_MODE')\nLIMIT = os.environ.get('APP_LIMIT', '5')\n",
            "The `APP_REMOVED` environment variable is read.\n",
        )
        finding = findings[0]
        self.assertEqual(finding.verdict, Verdict.FALSE)
        self.assertEqual(finding.evidence[0].path, "src/app.py")
        self.assertIn("APP_", finding.evidence[0].snippet)

    def test_unowned_third_party_environment_name_abstains(self) -> None:
        findings, _, _ = self._analyze(
            "import os\nMODE = os.getenv('APP_MODE')\nLIMIT = os.environ.get('APP_LIMIT', '5')\n",
            "Set the `HTTP_PROXY` environment variable.\n",
        )
        self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)

    def test_environment_name_in_scanned_yaml_prevents_absence_false(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "docs").mkdir()
        (root / "src").mkdir()
        (root / "src/settings.py").write_text(
            "import os\nMODE = os.getenv('APP_MODE')\nLIMIT = os.getenv('APP_LIMIT')\n",
            encoding="utf-8",
        )
        (root / "src/deployment.yml").write_text("optional: APP_REMOVED\n", encoding="utf-8")
        (root / "docs/guide.md").write_text(
            "The `APP_REMOVED` environment variable is read.\n", encoding="utf-8"
        )
        findings, _, _ = analyze(root, ("docs",), ("src",))
        self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)
        self.assertIn("appears in scanned text", findings[0].explanation)

    def test_dynamic_environment_access_prevents_absence_false(self) -> None:
        findings, _, _ = self._analyze(
            "import os\nMODE = os.getenv('APP_MODE')\nLIMIT = os.getenv('APP_LIMIT')\nVALUE = os.environ[key]\n",
            "The `APP_REMOVED` environment variable is read.\n",
        )
        self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)
        self.assertIn("Dynamic environment access", findings[0].explanation)

    def test_removed_route_is_false_when_static_index_is_complete(self) -> None:
        findings, _, _ = self._analyze(
            "from fastapi import FastAPI\napp = FastAPI()\n"
            "@app.get('/records')\ndef records(): return []\n",
            "The GET `/items` endpoint exists.\n",
        )
        self.assertEqual(findings[0].verdict, Verdict.FALSE)
        self.assertEqual(findings[0].evidence[0].snippet, "@app.get('/records')")

    def test_unresolved_route_prefix_prevents_absence_false(self) -> None:
        findings, _, _ = self._analyze(
            "from fastapi import APIRouter\nrouter = APIRouter(prefix=load_prefix())\n"
            "@router.get('/items')\ndef items(): return []\n",
            "The GET `/items` endpoint exists.\n",
        )
        self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)

    def test_historical_framing_does_not_produce_false(self) -> None:
        findings, _, _ = self._analyze(
            "def fetch(timeout=30): return timeout\n",
            "The `timeout` (default: 99) was used in v1.\n",
        )
        self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)
        self.assertIn("Historical", findings[0].explanation)

    def test_negated_cli_statement_does_not_produce_false(self) -> None:
        findings, _, _ = self._analyze(
            "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('--real-option')\n",
            "The CLI does not accept `--missing-option`.\n",
        )
        self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)

    def test_foreign_project_default_does_not_compare_local_parameter(self) -> None:
        findings, _, _ = self._analyze(
            "def fetch(timeout=30): return timeout\n",
            "A different project's default timeout is 99.\n",
        )
        self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)
        self.assertIn("another project", findings[0].explanation)

    def test_exact_route_remains_true_when_an_unrelated_file_has_parse_error(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "src").mkdir()
        (root / "docs").mkdir()
        (root / "src/app.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n"
            "@app.get('/items')\ndef items(): return []\n", encoding="utf-8"
        )
        (root / "src/broken.py").write_text("def broken(:\n", encoding="utf-8")
        (root / "docs/guide.md").write_text("The GET `/items` endpoint exists.\n", encoding="utf-8")
        findings, _, _ = analyze(root, ("docs",), ("src",))
        self.assertEqual(findings[0].verdict, Verdict.TRUE)


if __name__ == "__main__":
    unittest.main()
