from __future__ import annotations

import unittest
from collections.abc import Coroutine, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock
from unittest.mock import patch as mock_patch

from docdrift_core import Claim, Verdict, build_index, extract_markdown
from docdrift_core.checkers import check_claim
from docdrift_core.fix_verification import (
    CandidatePatchStatus,
    ClaimReverification,
    ExtractionCoverageStatus,
    FixReverificationResult,
    MarkdownValidationStatus,
    ReExtractionStatus,
    ReVerificationStatus,
    reverify_candidate_patch,
)
from docdrift_core.patches import DocumentationPatch, create_claim_patch
from docdrift_worker.fix_delivery import (
    FixDeliveryDecision,
    FixDeliveryMode,
    FixDeliveryStatus,
    plan_verified_fix_delivery,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_repo"
HEAD_SHA = "a" * 40


class FixDeliveryPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.code_index = build_index(FIXTURE_ROOT, ("src",))

    @staticmethod
    def run_without_event_loop(coroutine: Coroutine[Any, Any, object]) -> object:
        try:
            coroutine.send(None)
        except StopIteration as completed:
            return completed.value
        raise AssertionError("deterministic verification unexpectedly suspended")

    def verified_candidate(
        self, document_path: str = "docs/guide.md"
    ) -> tuple[FixReverificationResult, DocumentationPatch, Claim]:
        original = "The default timeout is 60.\n"
        affected = extract_markdown(original, document_path)[0]
        patch = create_claim_patch(
            original,
            affected,
            expected_original=affected.text[affected.span_start : affected.span_end],
            replacement_text="The default timeout is 30",
        )
        updated = extract_markdown(patch.proposed_text, document_path)[0]
        finding = check_claim(updated, self.code_index)
        self.assertEqual(finding.verdict, Verdict.TRUE)
        item = ClaimReverification(updated, finding, True, finding.explanation)
        result = FixReverificationResult(
            patch_status=CandidatePatchStatus.APPLIED_IN_MEMORY,
            re_extraction_status=ReExtractionStatus.FOUND,
            verification_status=ReVerificationStatus.COMPLETED,
            re_extracted_claim=updated,
            verification_finding=finding,
            patched_document=patch.proposed_text,
            fix_verified=True,
            explanation="All edited-region claims verified true.",
            markdown_validation_status=MarkdownValidationStatus.VALID,
            extraction_coverage_status=ExtractionCoverageStatus.COMPLETE,
            required_claims=(updated,),
            claim_verifications=(item,),
        )
        return result, patch, affected

    def plan(
        self,
        result: FixReverificationResult,
        patch: DocumentationPatch,
        claim: Claim,
        *,
        lines: Mapping[str, AbstractSet[int]] | None = None,
        current_head: str = HEAD_SHA,
    ) -> FixDeliveryDecision:
        return plan_verified_fix_delivery(
            result,
            patch,
            claim,
            analyzed_head_sha=HEAD_SHA,
            current_head_sha=current_head,
            added_markdown_lines=lines,
        )

    def test_verified_fix_on_added_line_selects_inline_suggestion(self) -> None:
        result, patch, claim = self.verified_candidate()

        decision = self.plan(result, patch, claim, lines={"docs/guide.md": frozenset({1})})

        self.assertEqual(decision.mode, FixDeliveryMode.INLINE_SUGGESTION)
        self.assertEqual(decision.status, FixDeliveryStatus.READY)
        self.assertTrue(decision.patch_delivery_eligible)
        self.assertEqual(decision.patch, patch)
        self.assertEqual(decision.document_path, "docs/guide.md")
        self.assertEqual(decision.document_line, 1)
        self.assertEqual(decision.head_sha, HEAD_SHA)

    def test_result_from_full_edited_region_workflow_is_eligible(self) -> None:
        original = "The default timeout is 60.\n"
        claim = extract_markdown(original, "docs/guide.md")[0]
        patch = create_claim_patch(
            original,
            claim,
            expected_original=claim.text[claim.span_start : claim.span_end],
            replacement_text="The default timeout is 30",
        )
        verified = cast(
            FixReverificationResult,
            self.run_without_event_loop(
                reverify_candidate_patch(original, patch, claim, self.code_index)
            ),
        )

        decision = self.plan(verified, patch, claim, lines={"docs/guide.md": {1}})

        self.assertTrue(verified.fix_verified)
        self.assertEqual(decision.status, FixDeliveryStatus.READY)
        self.assertEqual(decision.mode, FixDeliveryMode.INLINE_SUGGESTION)

    def test_unverified_false_and_unverifiable_results_never_carry_a_patch(self) -> None:
        result, patch, claim = self.verified_candidate()
        assert result.verification_finding is not None
        for verdict in (Verdict.FALSE, Verdict.UNVERIFIABLE):
            with self.subTest(verdict=verdict):
                finding = replace(result.verification_finding, verdict=verdict)
                item = replace(result.claim_verifications[0], finding=finding)
                invalid = replace(
                    result,
                    verification_finding=finding,
                    claim_verifications=(item,),
                    fix_verified=True,
                )

                decision = self.plan(invalid, patch, claim, lines={"docs/guide.md": {1}})

                self.assertEqual(decision.status, FixDeliveryStatus.REFUSED)
                self.assertEqual(decision.mode, FixDeliveryMode.ANNOTATION_ONLY)
                self.assertFalse(decision.patch_delivery_eligible)
                self.assertIsNone(decision.patch)

    def test_explicitly_unverified_or_incomplete_result_is_refused(self) -> None:
        result, patch, claim = self.verified_candidate()
        cases = (
            replace(result, fix_verified=False),
            replace(result, extraction_coverage_status=ExtractionCoverageStatus.MISSING),
            replace(result, markdown_validation_status=MarkdownValidationStatus.INVALID),
            replace(result, claim_verifications=()),
        )
        for invalid in cases:
            with self.subTest(result=invalid):
                decision = self.plan(
                    invalid, patch, claim, lines={"docs/guide.md": frozenset({1})}
                )
                self.assertEqual(decision.status, FixDeliveryStatus.REFUSED)
                self.assertIsNone(decision.patch)

    def test_out_of_diff_location_uses_annotation_only_fallback(self) -> None:
        result, patch, claim = self.verified_candidate()

        decision = self.plan(result, patch, claim, lines={"docs/guide.md": frozenset({2})})

        self.assertEqual(decision.mode, FixDeliveryMode.ANNOTATION_ONLY)
        self.assertEqual(decision.status, FixDeliveryStatus.FALLBACK)
        self.assertFalse(decision.patch_delivery_eligible)
        self.assertIsNone(decision.patch)
        self.assertIn("outside the pull request diff", decision.reason)

    def test_stale_sha_invalid_location_and_unavailable_diff_refuse_patch(self) -> None:
        result, patch, claim = self.verified_candidate()

        stale = self.plan(
            result,
            patch,
            claim,
            lines={"docs/guide.md": {1}},
            current_head="b" * 40,
        )
        invalid_location_result, invalid_location_patch, invalid_location_claim = (
            self.verified_candidate("../guide.md")
        )
        invalid_location = self.plan(
            invalid_location_result,
            invalid_location_patch,
            invalid_location_claim,
            lines={"../guide.md": {1}},
        )
        unavailable = self.plan(result, patch, claim, lines=None)

        for decision in (stale, invalid_location, unavailable):
            with self.subTest(decision=decision):
                self.assertEqual(decision.status, FixDeliveryStatus.REFUSED)
                self.assertEqual(decision.mode, FixDeliveryMode.ANNOTATION_ONLY)
                self.assertIsNone(decision.patch)

    def test_planner_never_publishes_or_writes_files(self) -> None:
        from docdrift_worker.github import GitHubAppCheckRunPublisher

        result, patch, claim = self.verified_candidate()
        with (
            mock_patch.object(
                GitHubAppCheckRunPublisher,
                "publish_inline_comments",
                new_callable=AsyncMock,
            ) as publish,
            mock_patch.object(
                Path, "write_text", side_effect=AssertionError("file write")
            ) as write,
        ):
            decision = self.plan(
                result, patch, claim, lines={"docs/guide.md": frozenset({1})}
            )

        self.assertEqual(decision.mode, FixDeliveryMode.INLINE_SUGGESTION)
        publish.assert_not_awaited()
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
