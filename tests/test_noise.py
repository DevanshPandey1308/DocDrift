from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docdrift_worker.noise import RepositoryConfigError, load_finding_exclusions


class FindingExclusionTests(unittest.TestCase):
    def test_valid_document_and_claim_exclusions_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            (root / ".docdrift.yml").write_text(
                "version: 1\n"
                "docs:\n"
                '  exclude: ["docs/legacy/**"]\n'
                "findings:\n"
                "  ignore:\n"
                "    - claim_id: abcdef0123456789\n"
                "      reason: Confirmed generated documentation.\n",
                encoding="utf-8",
            )
            exclusions = load_finding_exclusions(root)

        self.assertEqual(
            exclusions.reason_for("other", "docs/legacy/guide.md"),
            "excluded by docs.exclude pattern 'docs/legacy/**'",
        )
        self.assertEqual(
            exclusions.reason_for("abcdef0123456789", "docs/guide.md"),
            "Confirmed generated documentation.",
        )
        self.assertIsNone(exclusions.reason_for("other", "docs/guide.md"))

    def test_malformed_or_unsafe_configuration_fails_closed(self) -> None:
        invalid_documents = (
            "version: 2\n",
            "version: 1\nunknown: true\n",
            "version: 1\ndocs:\n  exclude: ../outside.md\n",
            "version: 1\nfindings:\n  ignore:\n    - claim_id: a\n",
            "version: 1\nversion: 1\n",
            "version: 1\nfindings: !!python/object/apply:os.system ['echo unsafe']\n",
        )
        for document in invalid_documents:
            with self.subTest(document=document), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
                root = Path(temporary)
                (root / ".docdrift.yml").write_text(document, encoding="utf-8")
                with self.assertRaises(RepositoryConfigError):
                    load_finding_exclusions(root)

    def test_absent_configuration_means_no_exclusions(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            exclusions = load_finding_exclusions(Path(temporary))
        self.assertIsNone(exclusions.reason_for("claim", "docs/guide.md"))


if __name__ == "__main__":
    unittest.main()
