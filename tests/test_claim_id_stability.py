from __future__ import annotations

import json
import unittest
from pathlib import Path

from docdrift_core import analyze

ROOT = Path(__file__).parent
GOLDEN = ROOT / "golden/claim_ids_before.json"


class ClaimIdStabilityTests(unittest.TestCase):
    def test_existing_fixture_claim_ids_remain_present(self) -> None:
        previous = json.loads(GOLDEN.read_text(encoding="utf-8"))
        for fixture_name, expected_ids in previous.items():
            with self.subTest(fixture=fixture_name):
                fixture = ROOT.parent / fixture_name
                _, _, claims = analyze(fixture, ("docs",), ("src",))
                current_ids = {claim.id for claim in claims}
                self.assertTrue(set(expected_ids).issubset(current_ids))


if __name__ == "__main__":
    unittest.main()
