from __future__ import annotations

import tempfile
import unittest
from collections.abc import Coroutine
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from docdrift_core import Finding, Verdict, build_index, extract_markdown
from docdrift_core.checkers import check_claim
from docdrift_core.models import Evidence
from docdrift_worker.fix_delivery import FixDeliveryMode
from docdrift_worker.fix_integration import (
    FixCandidateClient,
    VerifiedFixSuggestion,
    prepare_verified_fix_suggestions,
)
from docdrift_worker.github import _approved_suggestion_lines

HEAD_SHA = "a" * 40


class ImmediateAwaitable:
    def __init__(self, value: object) -> None:
        self.value = value

    def __await__(self):  # type: ignore[no-untyped-def]
        if False:
            yield None
        return self.value


class FakeCandidateClient:
    def __init__(self, replacement: str) -> None:
        self.replacement = replacement
        self.calls: list[tuple[Finding, tuple[Evidence, ...]]] = []

    def propose_replacement(
        self, finding: Finding, evidence: tuple[Evidence, ...]
    ) -> ImmediateAwaitable:
        self.calls.append((finding, evidence))
        return ImmediateAwaitable(self.replacement)


class FixIntegrationTests(unittest.TestCase):
    @staticmethod
    def run_without_event_loop(
        coroutine: Coroutine[Any, Any, object],
    ) -> object:
        try:
            coroutine.send(None)
        except StopIteration as completed:
            return completed.value
        raise AssertionError("fake candidate provider unexpectedly suspended")

    def setup_repository(self, root: Path, *, document_path: str = "docs/guide.md") -> tuple[Finding, object]:
        (root / "src").mkdir(parents=True)
        (root / "docs").mkdir(parents=True)
        (root / "src" / "api.py").write_text(
            "def connect(timeout=30):\n    return timeout\n", encoding="utf-8"
        )
        document = "The default timeout is 60.\n"
        (root / document_path).write_text(document, encoding="utf-8")
        index = build_index(root)
        claim = extract_markdown(document, document_path)[0]
        finding = check_claim(claim, index)
        if finding.verdict is not Verdict.FALSE or not finding.evidence:
            raise AssertionError("test fixture must produce an evidence-backed false finding")
        return finding, index

    def prepare(
        self,
        finding: Finding,
        root: Path,
        index: object,
        client: FakeCandidateClient,
        *,
        added_lines: dict[str, frozenset[int]] | None = None,
        head_sha: str = HEAD_SHA,
    ) -> tuple[VerifiedFixSuggestion, ...]:
        return cast(
            tuple[VerifiedFixSuggestion, ...],
            self.run_without_event_loop(
                prepare_verified_fix_suggestions(
                    [finding],
                    root,
                    candidate_client=cast(FixCandidateClient, client),
                    added_markdown_lines=(
                        {finding.claim.document_path: frozenset({finding.claim.line})}
                        if added_lines is None
                        else added_lines
                    ),
                    analyzed_head_sha=HEAD_SHA,
                    current_head_sha=head_sha,
                    code_index=cast(Any, index),
                )
            ),
        )

    def test_successful_in_diff_fix_is_reverified_and_planned(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            finding, index = self.setup_repository(root)
            client = FakeCandidateClient("The default timeout is 30")

            suggestions = self.prepare(finding, root, index, client)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][1], finding.evidence)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].decision.mode, FixDeliveryMode.INLINE_SUGGESTION)
        self.assertEqual(suggestions[0].replacement_line, "The default timeout is 30.")
        persisted_finding = SimpleNamespace(
            claim_id=finding.claim.id,
            document_path=finding.claim.document_path,
            document_line=finding.claim.line,
        )
        self.assertEqual(
            _approved_suggestion_lines([persisted_finding], suggestions, HEAD_SHA),
            {finding.claim.id: "The default timeout is 30."},
        )
        unapproved = replace(
            suggestions[0],
            decision=replace(suggestions[0].decision, patch_delivery_eligible=False),
        )
        self.assertEqual(
            _approved_suggestion_lines([persisted_finding], [unapproved], HEAD_SHA), {}
        )

    def test_failed_reverification_stale_head_and_out_of_diff_fall_back(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            finding, index = self.setup_repository(root)
            failed = self.prepare(
                finding, root, index, FakeCandidateClient("The default timeout is 45")
            )
            stale_client = FakeCandidateClient("The default timeout is 30")
            stale = self.prepare(
                finding,
                root,
                index,
                stale_client,
                head_sha="b" * 40,
            )
            outside_diff_client = FakeCandidateClient("The default timeout is 30")
            outside_diff = self.prepare(
                finding,
                root,
                index,
                outside_diff_client,
                added_lines={"docs/guide.md": frozenset({2})},
            )

        self.assertEqual(failed, ())
        self.assertEqual(stale, ())
        self.assertEqual(outside_diff, ())
        self.assertEqual(stale_client.calls, [])
        self.assertEqual(outside_diff_client.calls, [])

    def test_invalid_document_location_and_untrusted_evidence_never_call_provider(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            finding, index = self.setup_repository(root)
            bad_path_claim = replace(
                finding.claim, document_path="../docs/guide.md"
            )
            bad_path_finding = replace(finding, claim=bad_path_claim)
            fake = FakeCandidateClient("The default timeout is 30")
            invalid_location = self.prepare(bad_path_finding, root, index, fake)
            fabricated = Finding(
                finding.claim,
                finding.verdict,
                finding.explanation,
                (Evidence("src/api.py", 999, "timeout=30"),),
                finding.verification_method,
            )
            untrusted = self.prepare(fabricated, root, index, fake)

        self.assertEqual(invalid_location, ())
        self.assertEqual(untrusted, ())
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
