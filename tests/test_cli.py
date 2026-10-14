from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
FIXTURE = ROOT / "tests/fixtures/sample_repo"
CLEAN_FIXTURE = ROOT / "tests/fixtures/clean_repo"


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(ROOT / "docdrift.py"), *args], cwd=ROOT, text=True, capture_output=True)

    def test_end_to_end_exit_and_json(self) -> None:
        text = self.run_cli("check", "--repo", str(FIXTURE), "--docs", "docs", "--source", "src")
        self.assertEqual(text.returncode, 1, text.stderr)
        self.assertIn("source src/api.py:5", text.stdout)
        data = self.run_cli("check", "--repo", str(FIXTURE), "--docs", "docs", "--source", "src", "--format", "json")
        self.assertEqual(data.returncode, 1)
        parsed = json.loads(data.stdout)
        self.assertEqual(parsed["schema_version"], "1")
        self.assertTrue(parsed["findings"])

    def test_help_and_bad_path(self) -> None:
        self.assertEqual(self.run_cli("--help").returncode, 0)
        self.assertEqual(self.run_cli("check", "--repo", str(ROOT / "missing")).returncode, 2)
        invalid_scope = self.run_cli("check", "--repo", str(FIXTURE), "--docs", "missing")
        self.assertEqual(invalid_scope.returncode, 2)

    def test_clean_fixture_and_negative_control(self) -> None:
        result = self.run_cli("check", "--repo", str(CLEAN_FIXTURE), "--docs", "docs", "--source", "src")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TRUE", result.stdout)


if __name__ == "__main__":
    unittest.main()
