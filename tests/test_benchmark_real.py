from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmark.real import __main__ as real


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, text=True,
                          capture_output=True).stdout.strip()


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "benchmark@example.test")
    _git(root, "config", "user.name", "Benchmark Test")
    return root


class RealBenchmarkTests(unittest.TestCase):
    def test_hunk_parsing_numbers_equal_pair_and_unequal_skip(self) -> None:
        parsed = real.parse_hunks([
            "@@ -4,1 +4,1 @@", "-The default timeout is 30.", "+The default timeout is 20.",
            "@@ -10,2 +10,1 @@", "-Use --old.", "-More old text.", "+Use --new.",
        ])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["old_line_no"], 4)
        self.assertEqual(parsed[0]["new_line_no"], 4)
        self.assertEqual(parsed[0]["claim_type_guess"], "default_value")

    def test_token_classes(self) -> None:
        self.assertEqual(real.guess_claim_type("Python 3.11", "Python 3.12"), "python_version")
        self.assertEqual(real.guess_claim_type("timeout is 30", "timeout is 20"), "default_value")
        self.assertEqual(real.guess_claim_type("Use --old", "Use --new"), "cli_flag")
        self.assertEqual(real.guess_claim_type("Set FOO_BAR", "Set BAZ_BAR"), "environment_variable")
        self.assertEqual(real.guess_claim_type("See `old.py`", "See `new.py`"), "file_path")

    def test_bulk_document_filter_and_deterministic_balance(self) -> None:
        self.assertTrue(real._doc_path("README.md"))
        self.assertTrue(real._doc_path("docs/guide.rst"))
        self.assertFalse(real._doc_path("src/README.md"))
        rows: list[dict[str, object]] = [
            {"claim_type_guess": kind, "sha": f"{i:02}", "doc_path": "README.md", "old_line_no": i}
            for kind in ("cli_flag", "default_value") for i in range(4)
        ]
        self.assertEqual(real._round_robin(rows, 4), real._round_robin(rows, 4))
        self.assertEqual([row["claim_type_guess"] for row in real._round_robin(rows, 4)],
                         ["cli_flag", "default_value", "cli_flag", "default_value"])
        sample_rows: list[dict[str, object]] = []
        sample_rows.extend({"repo": "one", "id": str(index)} for index in range(5))
        sample_rows.append({"repo": "two", "id": "x"})
        sampled = real._even_repo_sample(sample_rows, 2)
        self.assertEqual([row["repo"] for row in sampled], ["one", "two"])

    def test_miner_skips_bulk_document_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = _init_repo(Path(temp) / "repo")
            (repo / "README.md").write_text("The default timeout is 1.\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-qm", "base")
            for index in range(41):
                (repo / f"README-{index}.md").write_text(
                    f"The default timeout is {index}.\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-qm", "bulk docs")
            rows, commits = real._mine_repo("sample", repo, 10)
            self.assertEqual(commits, 2)
            self.assertEqual(rows, [])

    def test_mining_is_deterministic_for_a_reviewable_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = _init_repo(Path(temp) / "repo")
            (repo / "README.md").write_text("The default timeout is 30.\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-qm", "base")
            parent = _git(repo, "rev-parse", "HEAD")
            (repo / "README.md").write_text("The default timeout is 20.\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-qm", "correct timeout")
            first, commits = real._mine_repo("sample", repo, 10)
            second, _ = real._mine_repo("sample", repo, 10)
            self.assertEqual(first, second)
            self.assertEqual(commits, 2)
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0]["parent_sha"], parent)
            self.assertEqual(first[0]["claim_type_guess"], "default_value")

    def test_csv_uses_bom_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rows.csv"
            real._csv_write(path, ["id", "human_label"], [{"id": "x", "human_label": ""}])
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertEqual(real._csv_read(path), [{"id": "x", "human_label": ""}])

    def test_build_rejects_bad_label_and_accepts_empty_reviews(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            candidates, cases = root / "candidates.csv", root / "cases.json"
            real._csv_write(candidates, real.CANDIDATE_FIELDS, [{"id": "c1", "human_label": ""}])
            self.assertEqual(real.build(argparse.Namespace(candidates=candidates, cases=cases)), 0)
            self.assertEqual(json.loads(cases.read_text()), [])
            real._csv_write(candidates, real.CANDIDATE_FIELDS, [{"id": "c2", "human_label": "wrong"}])
            with self.assertRaisesRegex(ValueError, "c2"):
                real.build(argparse.Namespace(candidates=candidates, cases=cases))

    def test_evaluate_parent_false_is_tp_and_unextractable_is_fn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = _init_repo(root / "repos" / "sample")
            (repo / "src.py").write_text("def fetch(timeout=10):\n    return timeout\n", encoding="utf-8")
            (repo / "README.md").write_text("The default timeout is 30.\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-qm", "parent")
            parent = _git(repo, "rev-parse", "HEAD")
            (repo / "README.md").write_text("The default timeout is 10.\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-qm", "fixed docs")
            fixed = _git(repo, "rev-parse", "HEAD")
            cases = [
                {"id": "tp", "repo": "sample", "parent_sha": parent, "sha": fixed,
                 "doc_path": "README.md", "old_line_no": 1, "old_line": "The default timeout is 30.",
                 "new_line_no": 1, "new_line": "The default timeout is 10.", "claim_type_guess": "default_value"},
                {"id": "fn", "repo": "sample", "parent_sha": parent, "sha": fixed,
                 "doc_path": "README.md", "old_line_no": 2, "old_line": "No claim here.",
                 "new_line_no": 2, "new_line": "No claim here.", "claim_type_guess": "default_value"},
            ]
            cases_path = root / "cases.json"
            cases_path.write_text(json.dumps(cases), encoding="utf-8")
            with patch.object(real, "ROOT", root), patch.object(real, "DEFAULT_REPOS", root / "repos"):
                output = root / "results"
                self.assertEqual(real.evaluate(argparse.Namespace(cases=cases_path, out=output)), 0)
            result = json.loads((output / "results.json").read_text(encoding="utf-8"))
            self.assertEqual([item["outcome"] for item in result], ["TP", "FN"])
            self.assertEqual(result[1]["reason"], "not_extracted")

    def test_wilson_zero_and_small_samples(self) -> None:
        self.assertIsNone(real.wilson(0, 0))
        interval = real.wilson(5, 10)
        self.assertIsNotNone(interval)
        assert interval is not None
        self.assertLess(interval[0], 0.5)
        self.assertGreater(interval[1], 0.5)

    def test_fp_score_math_unclear_exclusion_and_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            review = root / "review.csv"
            out = root / "out"
            real._csv_write(review, real.FP_FIELDS, [
                {"id": "a", "claim_type": "default_value", "human_label": "real_drift"},
                {"id": "b", "claim_type": "default_value", "human_label": "false_positive"},
                {"id": "c", "claim_type": "cli_flag", "human_label": "unclear"},
            ])
            real.fp_score(argparse.Namespace(review=review, out=out))
            report = (out / "results.md").read_text(encoding="utf-8")
            self.assertIn("1/2; n too small for a rate", report)
            self.assertIn("Unclear (excluded): 1", report)
            real._csv_write(review, real.FP_FIELDS, [])
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                real.fp_score(argparse.Namespace(review=review, out=out))
            self.assertIn("0 reviewed findings", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
