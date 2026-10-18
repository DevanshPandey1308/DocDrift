from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docdrift_core.models import Claim, ClaimType, Finding, Verdict
from docdrift_worker.noise import RepositoryConfigError, load_finding_exclusions


def make_finding(claim_id: str, document_path: str, line: int) -> Finding:
    claim = Claim(
        id=claim_id,
        type=ClaimType.FILE_PATH,
        subject="file_path",
        expected="module.py",
        text=f"The file is module.py on line {line}.",
        document_path=document_path,
        line=line,
        span_start=0,
        span_end=10,
    )
    return Finding(
        claim=claim,
        verdict=Verdict.FALSE,
        explanation="Missing file.",
        evidence=(),
        verification_method="repository_path",
        deterministic=True,
    )


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
        self.assertEqual(exclusions.inline_min, "breaking")
        self.assertEqual(exclusions.max_inline_comments, 5)

    def test_severity_inline_threshold_and_budget_are_validated(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            (root / ".docdrift.yml").write_text(
                "version: 1\nseverity:\n  inline_min: misleading\n  max_inline_comments: 2\n",
                encoding="utf-8",
            )
            config = load_finding_exclusions(root)
        self.assertEqual(config.inline_min, "misleading")
        self.assertEqual(config.max_inline_comments, 2)

    def test_invalid_severity_values_fail_closed(self) -> None:
        invalid_documents = (
            "version: 1\nseverity:\n  inline_min: urgent\n",
            "version: 1\nseverity:\n  max_inline_comments: -1\n",
            "version: 1\nseverity:\n  max_inline_comments: 51\n",
            "version: 1\nseverity:\n  unsupported: true\n",
        )
        for document in invalid_documents:
            with self.subTest(document=document), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
                root = Path(temporary)
                (root / ".docdrift.yml").write_text(document, encoding="utf-8")
                with self.assertRaises(RepositoryConfigError):
                    load_finding_exclusions(root)

    def test_ignore_marker_applies_only_to_the_immediately_following_claim_line(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs/guide.md").write_text(
                "<!-- docdrift-ignore -->\nThe file is module.py.\n"
                "<!-- ordinary comment -->\nThe file is another.py.\n"
                "<!-- docdrift-ignore reason -->\nThe file is third.py.\n"
                "<!-- docdrift-ignore -->\n\nThe file is fourth.py.\n",
                encoding="utf-8",
            )
            findings = [
                make_finding("a" * 16, "docs/guide.md", 2),
                make_finding("b" * 16, "docs/guide.md", 4),
                make_finding("c" * 16, "docs/guide.md", 6),
                make_finding("d" * 16, "docs/guide.md", 9),
            ]
            exclusions = load_finding_exclusions(root, findings)

        self.assertEqual(
            exclusions.reason_for("a" * 16, "docs/guide.md"),
            "ignored by <!-- docdrift-ignore --> on line 1",
        )
        for claim_id in ("b" * 16, "c" * 16, "d" * 16):
            self.assertIsNone(exclusions.reason_for(claim_id, "docs/guide.md"))

    def test_marker_scope_composes_with_claim_and_document_exclusions(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs/guide.md").write_text(
                "<!-- docdrift-ignore -->\nThe file is module.py.\n",
                encoding="utf-8",
            )
            (root / ".docdrift.yml").write_text(
                "version: 1\n"
                "docs:\n  exclude:\n    - \"docs/**\"\n"
                "findings:\n  ignore:\n"
                "    - claim_id: \"aaaaaaaaaaaaaaaa\"\n"
                "      reason: \"Reviewed by a maintainer.\"\n",
                encoding="utf-8",
            )
            finding = make_finding("a" * 16, "docs/guide.md", 2)
            exclusions = load_finding_exclusions(root, [finding])

        self.assertEqual(
            exclusions.reason_for(finding.claim.id, finding.claim.document_path),
            "Reviewed by a maintainer.",
        )
        self.assertEqual(
            exclusions.reason_for("b" * 16, finding.claim.document_path),
            "excluded by docs.exclude pattern 'docs/**'",
        )

    def test_marker_never_reads_documents_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            external = root.parent / f"{root.name}-external.md"
            external.write_text("<!-- docdrift-ignore -->\n", encoding="utf-8")
            try:
                finding = make_finding("a" * 16, f"../{external.name}", 2)
                exclusions = load_finding_exclusions(root, [finding])
            finally:
                external.unlink(missing_ok=True)

        self.assertIsNone(exclusions.reason_for("a" * 16, finding.claim.document_path))


if __name__ == "__main__":
    unittest.main()
