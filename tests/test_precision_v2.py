from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docdrift_core import analyze, extract_markdown
from docdrift_core.checkers import check_python_version
from docdrift_core.index import CodeIndex
from docdrift_core.models import Claim, ClaimType, Verdict


class PrecisionV2Tests(unittest.TestCase):
    def _root(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "docs").mkdir()
        (root / "src").mkdir()
        return root

    def test_missing_file_with_known_extension_and_existing_parent_is_false(self) -> None:
        root = self._root()
        (root / "docs/guide.md").write_text(
            "The example is implemented in `src/old_module.py`.\n", encoding="utf-8"
        )
        findings, _, _ = analyze(root, ("docs",), ("src",))
        self.assertEqual(findings[0].verdict, Verdict.FALSE)
        self.assertIn("existing parent", findings[0].explanation)

    def test_factual_reference_to_existing_file_remains_true(self) -> None:
        root = self._root()
        (root / "src/current.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "docs/guide.md").write_text(
            "The example is implemented in `src/current.py`.\n", encoding="utf-8"
        )
        findings, _, _ = analyze(root, ("docs",), ("src",))
        self.assertEqual(findings[0].verdict, Verdict.TRUE)

    def test_moved_file_is_false_with_destination_evidence(self) -> None:
        root = self._root()
        (root / "archive").mkdir()
        (root / "archive/old_module.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "docs/guide.md").write_text(
            "The example is implemented in `src/old_module.py`.\n", encoding="utf-8"
        )
        findings, _, _ = analyze(root, ("docs",), ("src", "archive"))
        self.assertEqual(findings[0].verdict, Verdict.FALSE)
        self.assertEqual(findings[0].evidence[0].path, "archive/old_module.py")

    def test_unrelated_imperative_prose_does_not_suppress_factual_missing_path(self) -> None:
        root = self._root()
        (root / "docs/guide.md").write_text(
            "The implementation lives in `src/old_module.py`; run the generator before release.\n",
            encoding="utf-8",
        )
        findings, _, _ = analyze(root, ("docs",), ("src",))
        self.assertEqual(findings[0].verdict, Verdict.FALSE)

    def test_path_noise_abstains_and_keeps_extracted_claim_ids(self) -> None:
        root = self._root()
        (root / "docker").mkdir()
        documents = {
            "README.md": (
                "Use the image `ghcr.io/acme/service:1.2.3`.\n"
                "Use action `docker/setup-buildx-action`.\n"
                "The rendered site is emitted into `site/`.\n"
                "The example app uses `myproject/wsgi.py`.\n"
                "The setup instructions say to create `gunicorn.conf.py` before starting.\n"
                "The runtime path is under `$XDG_RUNTIME_DIR/app.ctl`.\n"
                "This feature has a note (Python 3.6+).\n"
                "The demo is in `example.py`.\n"
            ),
            "docs/news.md": "The example is implemented in `src/news_module.py`.\n",
            "CHANGELOG.md": "The example is implemented in `src/changelog_module.py`.\n",
            "docs/reference/settings.md": "| Legacy option | Python 3.4+ |\n",
        }
        for relative, contents in documents.items():
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(contents, encoding="utf-8")

        findings, _, claims = analyze(root, (".",), (".",))
        self.assertTrue(claims)
        self.assertTrue(all(finding.verdict is Verdict.UNVERIFIABLE for finding in findings))
        self.assertEqual(len({claim.id for claim in claims}), len(claims))

        extracted_ids = {
            claim.id
            for relative, contents in documents.items()
            for claim in extract_markdown(contents, relative, root)
        }
        self.assertEqual({claim.id for claim in claims}, extracted_ids)
        self.assertEqual(
            {
                (claim.document_path, claim.type.value, claim.subject): claim.id
                for claim in claims
            },
            {
                ("README.md", "file_path", "ghcr.io/acme/service:1.2.3"): "9cfcfebe6499b5c3",
                ("README.md", "file_path", "docker/setup-buildx-action"): "eab43a885ff9f5ef",
                ("README.md", "file_path", "site/"): "d57e495cde80ee75",
                ("README.md", "file_path", "myproject/wsgi.py"): "81f0e9111f6e34c4",
                ("README.md", "file_path", "gunicorn.conf.py"): "9579e10e5688888f",
                ("README.md", "file_path", "$XDG_RUNTIME_DIR/app.ctl"): "5e986b6433e26d89",
                ("README.md", "python_version", "3.6"): "5e8944873755291a",
                ("README.md", "file_path", "example.py"): "a7f247b5e0002ff8",
                ("docs/news.md", "file_path", "src/news_module.py"): "3303df9c2118a3f0",
                ("docs/reference/settings.md", "python_version", "3.4"): "6622e815c1b7c37b",
                ("CHANGELOG.md", "file_path", "src/changelog_module.py"): "143b244e32c12fad",
            },
        )
        self.assertTrue(any(claim.type is ClaimType.FILE_PATH for claim in claims))
        self.assertTrue(any(claim.type is ClaimType.PYTHON_VERSION for claim in claims))

    def test_project_level_python_minimum_mismatch_remains_false(self) -> None:
        root = self._root()
        (root / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12"\n', encoding="utf-8"
        )
        (root / "docs/guide.md").write_text(
            "Requires Python 3.9+ for the project.\n", encoding="utf-8"
        )
        findings, _, _ = analyze(root, ("docs",), (".",))
        self.assertEqual(findings[0].claim.type, ClaimType.PYTHON_VERSION)
        self.assertEqual(findings[0].verdict, Verdict.FALSE)

    def test_parenthetical_and_settings_table_versions_abstain(self) -> None:
        root = self._root()
        (root / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12"\n', encoding="utf-8"
        )
        (root / "docs/guide.md").write_text(
            "A feature note (Python 3.6+) applies to this setting.\n", encoding="utf-8"
        )
        (root / "docs/reference").mkdir()
        (root / "docs/reference/settings.md").write_text(
            "| Setting | Version |\n|---|---|\n| Legacy mode | Python 3.4+ |\n"
            "The service supports Python 3.5+.\n",
            encoding="utf-8",
        )
        findings, _, claims = analyze(root, ("docs",), (".",))
        findings_by_id = {finding.claim.id: finding for finding in findings}
        by_version = {
            claim.expected["version"]: findings_by_id[claim.id]
            for claim in claims
            if claim.type is ClaimType.PYTHON_VERSION
        }
        self.assertEqual(by_version["3.6"].verdict, Verdict.UNVERIFIABLE)
        self.assertEqual(by_version["3.5"].verdict, Verdict.UNVERIFIABLE)
        self.assertTrue(all("feature-level" in item.explanation for item in findings))

        table_claim = Claim(
            "table-version", ClaimType.PYTHON_VERSION, "3.4", {"version": "3.4"},
            "| Legacy mode | Python 3.4+ |", "docs/reference/settings.md", 3, 16, 29,
        )
        table_finding = check_python_version(table_claim, CodeIndex(root))
        self.assertEqual(table_finding.verdict, Verdict.UNVERIFIABLE)
        self.assertIn("feature-level", table_finding.explanation)

    def test_feature_and_historical_python_versions_are_not_project_minima(self) -> None:
        root = self._root()
        (root / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12"\n', encoding="utf-8"
        )
        (root / "docs/guide.md").write_text(
            "Type declarations use Python 3.6+ syntax.\n", encoding="utf-8"
        )
        (root / "docs/news.md").write_text(
            "The former release required Python 3.8+.\n", encoding="utf-8"
        )
        findings, _, _ = analyze(root, ("docs",), (".",))
        self.assertEqual([finding.verdict for finding in findings], [
            Verdict.UNVERIFIABLE, Verdict.UNVERIFIABLE,
        ])
        self.assertIn("feature-level", findings[0].explanation)
        self.assertIn("historical document", findings[1].explanation)


if __name__ == "__main__":
    unittest.main()
