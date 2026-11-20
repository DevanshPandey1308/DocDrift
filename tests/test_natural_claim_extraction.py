from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docdrift_core import analyze, extract_markdown
from docdrift_core.models import Verdict

ROOT = Path(__file__).parent / "fixtures"
NATURAL = ROOT / "natural_phrasing_repo"
CLEAN = ROOT / "natural_phrasing_clean_repo"


class NaturalClaimExtractionTests(unittest.TestCase):
    def test_natural_phrasing_fixture_reports_expected_drift(self) -> None:
        findings, _, _ = analyze(NATURAL, ("docs",), ("src",))
        observed = [(item.claim.subject, item.claim.expected, item.verdict) for item in findings]
        self.assertEqual(
            observed,
            [
                ("timeout", {"value": "30"}, Verdict.FALSE),
                ("retries", {"value": "5"}, Verdict.FALSE),
                ("timeout", {"value": "30", "function": "fetch"}, Verdict.FALSE),
                ("timeout", {"value": "30"}, Verdict.FALSE),
                ("timeout", {"value": "30", "unit": "seconds"}, Verdict.FALSE),
                ("retries", {"value": "5"}, Verdict.FALSE),
                ("3.9", {"version": "3.9"}, Verdict.TRUE),
                ("HTTP_PROXY", {}, Verdict.TRUE),
            ],
        )
        self.assertTrue(all(item.claim.line != 10 for item in findings))

    def test_correct_natural_phrasing_fixture_has_no_false_findings(self) -> None:
        findings, _, _ = analyze(CLEAN, ("docs",), ("src",))
        self.assertTrue(findings)
        self.assertTrue(all(item.verdict is Verdict.TRUE for item in findings))

    def test_corrected_default_changes_only_its_claim_verdict(self) -> None:
        original, _, _ = analyze(NATURAL, ("docs",), ("src",))
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            root = Path(temp)
            (root / "docs").mkdir()
            (root / "src").mkdir()
            (root / "pyproject.toml").write_text((NATURAL / "pyproject.toml").read_text(), encoding="utf-8")
            (root / "src/api.py").write_text((NATURAL / "src/api.py").read_text(), encoding="utf-8")
            edited = (NATURAL / "docs/README.md").read_text(encoding="utf-8").replace(
                "| `timeout` | `30` |", "| `timeout` | `60` |", 1
            )
            (root / "docs/README.md").write_text(edited, encoding="utf-8")
            changed, _, _ = analyze(root, ("docs",), ("src",))
        before = {item.claim.id: item.verdict for item in original}
        after = {item.claim.id: item.verdict for item in changed}
        self.assertEqual(set(before), set(after))
        changed_ids = {claim_id for claim_id in before if before[claim_id] != after[claim_id]}
        self.assertEqual(len(changed_ids), 1)
        changed_claim = next(item.claim for item in changed if item.claim.id in changed_ids)
        self.assertEqual(changed_claim.line, 3)
        self.assertEqual(after[changed_claim.id], Verdict.TRUE)

    def test_inline_default_forms_and_unit_capture(self) -> None:
        claims = extract_markdown(
            "`limit` (default 5) bounds work.\n"
            "The `retries` defaults to 3.\n"
            "The default timeout is 30 ms.\n",
            "docs/guide.md",
        )
        self.assertEqual([claim.subject for claim in claims], ["limit", "retries", "timeout"])
        self.assertEqual(claims[0].expected, {"value": "5"})
        self.assertEqual(claims[1].expected, {"value": "3"})
        self.assertEqual(claims[2].expected, {"value": "30", "unit": "ms"})

    def test_supported_python_minimum_phrasings(self) -> None:
        claims = extract_markdown(
            "Python >= 3.9 is supported.\n"
            "Python 3.9 or higher is supported.\n"
            "Python 3.9 or later is supported.\n",
            "docs/guide.md",
        )
        self.assertEqual([claim.expected for claim in claims], [{"version": "3.9"}] * 3)

    def test_unknown_table_default_is_unverifiable_and_exclusions_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            root = Path(temp)
            (root / "docs").mkdir()
            (root / "src").mkdir()
            (root / "src/api.py").write_text("def known(value=1): return value\n", encoding="utf-8")
            (root / "docs/README.md").write_text(
                "| Name | Default |\n| --- | --- |\n"
                "| `unknown` | `2` |\n| `required_value` | required |\n| `unset` | - |\n"
                "| `empty` | — |\n| `na` | n/a |\n"
                "| Name | Meaning |\n| --- | --- |\n| `ignored` | `4` |\n"
                "```md\n| Name | Default |\n| --- | --- |\n| `fenced` | `4` |\n"
                "`fake(timeout=3)` makes a call.\n```\n"
                "By default, requests time out after 30 seconds.\n",
                encoding="utf-8",
            )
            findings, _, claims = analyze(root, ("docs",), ("src",))
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].subject, "unknown")
        self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)

    def test_signature_default_requires_unique_function_resolution(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            root = Path(temp)
            (root / "docs").mkdir()
            (root / "src").mkdir()
            (root / "src/api.py").write_text(
                "def fetch(timeout=30): return timeout\n"
                "def fetch(timeout=30): return timeout\n",
                encoding="utf-8",
            )
            (root / "docs/README.md").write_text("Call `fetch(timeout=30)` for requests.\n", encoding="utf-8")
            findings, _, _ = analyze(root, ("docs",), ("src",))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].verdict, Verdict.UNVERIFIABLE)


if __name__ == "__main__":
    unittest.main()
