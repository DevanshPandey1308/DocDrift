from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docdrift_core import analyze, build_impact_index


class ImpactMappingTests(unittest.TestCase):
    def test_changed_python_file_selects_claims_by_exact_indexed_symbol(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "src/alpha.py").write_text("def alpha(value):\n    return value\n", encoding="utf-8")
            (root / "src/beta.py").write_text("def beta(value):\n    return value\n", encoding="utf-8")
            (root / "docs/guide.md").write_text(
                "`alpha(value)` accepts a value.\n`beta(value)` accepts a value.\n",
                encoding="utf-8",
            )

            findings, _, claims = analyze(
                root,
                ("docs",),
                ("src",),
                changed_paths=("src/alpha.py",),
            )

        self.assertEqual([finding.claim.subject for finding in findings], ["alpha"])
        self.assertEqual(build_impact_index(claims)["alpha"], (claims[0].id,))

    def test_docs_or_unindexed_changes_fall_back_to_full_scan(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "src/api.py").write_text("def alpha(value):\n    return value\n", encoding="utf-8")
            (root / "docs/guide.md").write_text(
                "`alpha(value)` accepts a value.\nSee `missing.py`.\n", encoding="utf-8"
            )
            full, _, _ = analyze(root, ("docs",), ("src",))
            docs_change, _, _ = analyze(
                root, ("docs",), ("src",), changed_paths=("docs/guide.md",)
            )
            unmapped, _, _ = analyze(
                root, ("docs",), ("src",), changed_paths=("src/not_indexed.py",)
            )

        self.assertEqual([f.claim.id for f in docs_change], [f.claim.id for f in full])
        self.assertEqual([f.claim.id for f in unmapped], [f.claim.id for f in full])

    def test_empty_impact_map_falls_back_instead_of_suppressing_findings(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "src/api.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "docs/guide.md").write_text("See `missing.py`.\n", encoding="utf-8")
            full, _, _ = analyze(root, ("docs",), ("src",))
            changed, _, _ = analyze(
                root, ("docs",), ("src",), changed_paths=("src/api.py",)
            )

        self.assertEqual([f.claim.id for f in changed], [f.claim.id for f in full])

    def test_unmapped_symbol_in_changed_file_forces_full_scan(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "src/alpha.py").write_text(
                "def alpha(value):\n    return value\n\n"
                "def helper(value):\n    return value\n",
                encoding="utf-8",
            )
            (root / "src/beta.py").write_text(
                "def beta(value):\n    return value\n", encoding="utf-8"
            )
            (root / "docs/guide.md").write_text(
                "`alpha(value)` accepts a value.\n`beta(value)` accepts a value.\n",
                encoding="utf-8",
            )
            full, _, _ = analyze(root, ("docs",), ("src",))
            changed, _, _ = analyze(
                root,
                ("docs",),
                ("src",),
                changed_paths=("src/alpha.py",),
            )

        self.assertEqual([f.claim.id for f in changed], [f.claim.id for f in full])


if __name__ == "__main__":
    unittest.main()
