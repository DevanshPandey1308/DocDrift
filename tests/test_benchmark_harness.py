from __future__ import annotations

import contextlib
import hashlib
import io
import json
import random
import tempfile
import unittest
from pathlib import Path

from benchmark.harness import (
    OPERATORS,
    _independent_agreement,
    generate_case,
    generate_dataset,
    main,
    metrics,
    n1_verdict,
    validate_manifest,
)


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class BenchmarkHarnessTests(unittest.TestCase):
    def test_generator_is_seed_deterministic_and_seed_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            generate_dataset(first, seed=1337, repos=8)
            first_hash = _tree_hash(first)
            generate_dataset(first, seed=1337, repos=8)
            self.assertEqual(first_hash, _tree_hash(first))
            generate_dataset(second, seed=2024, repos=8)
            self.assertNotEqual(first_hash, _tree_hash(second))

    def test_manifest_lines_and_text_match_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifests = generate_dataset(root / "data", seed=71, repos=5)
            for manifest in manifests:
                case_dir = root / "data" / manifest["case_id"]
                validate_manifest(case_dir, manifest)
                for statement in manifest["statements"]:
                    lines = (case_dir / "repo" / statement["path"]).read_text(encoding="utf-8").splitlines()
                    self.assertEqual(lines[statement["line"] - 1], statement["text"])

    def test_generator_v3_includes_third_party_decoys_for_clean_and_mutated_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clean = generate_case(root, 0, random.Random(0))
            mutated = generate_case(root, 1, random.Random(72), ["change_default"])
            for manifest in (clean, mutated):
                self.assertEqual(manifest["generator_version"], 3)
                self.assertEqual(manifest["mutated"], manifest is mutated)
                decoys = [item for item in manifest["statements"] if item["category"] == "decoy_third_party"]
                self.assertEqual(len(decoys), 4)
                self.assertTrue(all(item["label"] == "decoy" for item in decoys))
                self.assertEqual(
                    {item["text"] for item in decoys},
                    {
                        "Run `pytest --cov` to measure coverage.",
                        "Set `HTTP_PROXY` to use a proxy.",
                        "Run `pip install -e .` to install the project.",
                        "Use `docker run --rm` for a disposable container.",
                    },
                )

    def test_independent_labels_and_each_operator_flip_only_relevant_statements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, operator in enumerate(OPERATORS):
                case_dir = root / f"case-{index:04d}"
                manifest = generate_case(root, index, random.Random(120 + index), [operator])
                validate_manifest(case_dir, manifest)
                drifted = [statement for statement in manifest["statements"] if statement["label"] == "drifted"]
                self.assertTrue(drifted, operator)
                self.assertTrue(all(statement["operator"] == operator for statement in drifted), operator)
                for statement in manifest["statements"]:
                    if statement["label"] == "decoy":
                        continue
                    agreement = _independent_agreement(case_dir / "repo", statement)
                    self.assertEqual(agreement, statement["label"] == "correct",
                                     (operator, statement, agreement))
                if operator == "delete_file":
                    self.assertTrue((case_dir / "repo/reference").is_dir())
                    self.assertTrue((case_dir / "repo/reference/README.md").is_file())
                if operator == "delete_directory":
                    self.assertFalse((case_dir / "repo/reference").exists())

    def test_metrics_and_zero_denominators(self) -> None:
        rows = [
            {"label": "drifted", "verdict": "false", "extracted": True},
            {"label": "drifted", "verdict": "unverifiable", "extracted": True},
            {"label": "drifted", "verdict": "none", "extracted": False},
            {"label": "correct", "verdict": "false", "extracted": True},
            {"label": "decoy", "verdict": "false", "extracted": True},
            {"label": "correct", "verdict": "true", "extracted": True},
            {"label": "correct", "verdict": "unverifiable", "extracted": True},
        ]
        score = metrics(rows)
        self.assertEqual((score["TP"], score["FP"], score["FN"]), (1, 2, 2))
        self.assertAlmostEqual(score["precision"], 1 / 3, places=6)
        self.assertAlmostEqual(score["recall"], 1 / 3, places=6)
        self.assertAlmostEqual(score["F1"], 1 / 3, places=6)
        self.assertAlmostEqual(score["extraction_rate"], 6 / 7, places=6)
        self.assertAlmostEqual(score["abstain_rate_on_correct"], 1 / 3, places=6)
        empty = metrics([])
        self.assertIsNone(empty["precision"])
        self.assertIsNone(empty["recall"])
        self.assertIsNone(empty["F1"])

    def test_n1_flags_mentions_only_in_mutated_cases(self) -> None:
        statement = {"subject": "timeout"}
        self.assertEqual(n1_verdict({"mutated": False}, statement, {"timeout"}), "none")
        self.assertEqual(n1_verdict({"mutated": True}, statement, {"timeout"}), "false")
        self.assertEqual(n1_verdict({"mutated": True}, statement, {"retries"}), "none")

    def test_check_command_equal_and_artificially_raised_baselines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out = root / "latest"
            out.mkdir()
            current = {
                "engine": {
                    "overall": {"precision": 0.5, "recall": 0.6},
                    "per_claim_type": {"default_value": {"recall": 0.4}},
                }
            }
            (out / "results.json").write_text(json.dumps(current), encoding="utf-8")
            baseline = root / "baseline.json"
            equal = {
                "overall": {"precision": 0.5, "recall": 0.6},
                "per_claim_type_recall": {"default_value": 0.4},
            }
            baseline.write_text(json.dumps(equal), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                passed = main(["check", "--baseline", str(baseline), "--out", str(out)])
            self.assertEqual(passed, 0)
            raised = {
                "overall": {"precision": 0.9, "recall": 0.9},
                "per_claim_type_recall": {"default_value": 0.9},
            }
            baseline.write_text(json.dumps(raised), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                regressed = main(["check", "--baseline", str(baseline), "--out", str(out)])
            self.assertEqual(regressed, 1)


if __name__ == "__main__":
    unittest.main()
