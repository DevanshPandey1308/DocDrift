from __future__ import annotations

import unittest
from collections.abc import Awaitable, Coroutine
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch as mock_patch

from docdrift_core import (
    Claim,
    ClaimType,
    Evidence,
    Verdict,
    VerificationRequest,
    VerificationResult,
    VerificationVerdict,
    build_index,
    extract_markdown,
    validate_verification_output,
)
from docdrift_core.fix_verification import (
    CandidatePatchStatus,
    ExtractionCoverageStatus,
    FixReverificationResult,
    MarkdownValidationStatus,
    ReExtractionStatus,
    ReVerificationStatus,
    reverify_candidate_patch,
)
from docdrift_core.patches import DocumentationPatch, create_claim_patch

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_repo"


class ImmediateAwaitable:
    def __init__(self, value: object = None) -> None:
        self.value = value

    def __await__(self):  # type: ignore[no-untyped-def]
        if False:
            yield None
        return self.value


class FakeClient:
    def __init__(
        self,
        result: VerificationResult | None = None,
        error: Exception | None = None,
        *,
        validate_true: bool = False,
    ) -> None:
        self.result = result
        self.error = error
        self.validate_true = validate_true
        self.requests: list[VerificationRequest] = []

    def verify(self, request: VerificationRequest) -> Awaitable[VerificationResult]:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.validate_true:
            snippet = request.evidence[0]
            return ImmediateAwaitable(
                validate_verification_output(
                    request.claim,
                    {
                        "verdict": "true",
                        "evidence_refs": [{"ref": snippet.reference, "quote": snippet.text}],
                        "rationale": "The supplied code evidence supports the updated claim.",
                    },
                    request.evidence,
                    evidence_sufficient=lambda claim, snippets, citations: (
                        claim == request.claim
                        and snippets == (snippet,)
                        and citations[0].quote == snippet.text
                    ),
                )
            )
        return cast(Awaitable[VerificationResult], ImmediateAwaitable(self.result))


class FixReverificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.code_index = build_index(FIXTURE_ROOT, ("src",))
        source_evidence = self.code_index.evidence("src/api.py", 5)
        if source_evidence is None:
            raise AssertionError("sample source evidence should exist")
        self.source_evidence: Evidence = source_evidence

    @staticmethod
    def run_without_event_loop(
        coroutine: Coroutine[Any, Any, object],
    ) -> object:
        """Drive this workflow's synchronous fakes without Windows loop startup."""
        try:
            coroutine.send(None)
        except StopIteration as completed:
            return completed.value
        raise AssertionError("fake verification unexpectedly suspended")

    @staticmethod
    def candidate(document: str, replacement: str) -> tuple[Claim, DocumentationPatch]:
        claims = extract_markdown(document, "docs/guide.md")
        if len(claims) != 1:
            raise AssertionError(f"test input must have one claim, found {len(claims)}")
        claim = claims[0]
        expected = claim.text[claim.span_start : claim.span_end]
        return claim, create_claim_patch(
            document,
            claim,
            expected_original=expected,
            replacement_text=replacement,
        )

    def verify(
        self,
        document: str,
        claim: Claim,
        candidate_patch: DocumentationPatch,
        *,
        evidence: tuple[Evidence, ...] = (),
        client: FakeClient | None = None,
    ) -> FixReverificationResult:
        return cast(
            FixReverificationResult,
            self.run_without_event_loop(
                reverify_candidate_patch(
                    document,
                    candidate_patch,
                    claim,
                    self.code_index,
                    evidence=evidence,
                    client=client,
                )
            ),
        )

    def test_deterministic_true_patch_is_verified_in_memory_only(self) -> None:
        document = "The default timeout is 60.\n"
        claim, candidate_patch = self.candidate(document, "The default timeout is 30")
        client = FakeClient(error=AssertionError("deterministic true must not escalate"))

        with mock_patch.object(Path, "write_text", side_effect=AssertionError("file write")) as write:
            result = self.verify(document, claim, candidate_patch, client=client)

        self.assertEqual(result.patch_status, CandidatePatchStatus.APPLIED_IN_MEMORY)
        self.assertEqual(result.re_extraction_status, ReExtractionStatus.FOUND)
        self.assertEqual(result.verification_status, ReVerificationStatus.COMPLETED)
        self.assertEqual(result.markdown_validation_status, MarkdownValidationStatus.VALID)
        self.assertEqual(result.extraction_coverage_status, ExtractionCoverageStatus.COMPLETE)
        assert result.verification_finding is not None
        self.assertEqual(result.verification_finding.verdict, Verdict.TRUE)
        self.assertTrue(result.fix_verified)
        self.assertEqual(result.patched_document, "The default timeout is 30.\n")
        self.assertEqual(document, "The default timeout is 60.\n")
        self.assertEqual(client.requests, [])
        write.assert_not_called()

    def test_false_candidate_remains_unverified(self) -> None:
        document = "The default timeout is 60.\n"
        claim, candidate_patch = self.candidate(document, "default timeout is 29")

        result = self.verify(document, claim, candidate_patch)

        assert result.verification_finding is not None
        self.assertEqual(result.verification_finding.verdict, Verdict.FALSE)
        self.assertFalse(result.fix_verified)

    def test_grounded_true_is_accepted_only_through_existing_orchestration(self) -> None:
        document = "The default timeout is dynamic.\n"
        claim, candidate_patch = self.candidate(
            document, "default timeout is dynamically selected"
        )
        client = FakeClient(validate_true=True)

        result = self.verify(
            document,
            claim,
            candidate_patch,
            evidence=(self.source_evidence,),
            client=client,
        )

        self.assertEqual(len(client.requests), 1)
        assert result.verification_finding is not None
        self.assertEqual(result.verification_finding.verdict, Verdict.TRUE)
        self.assertEqual(result.verification_finding.verification_method, "grounded_llm")
        self.assertFalse(result.verification_finding.deterministic)
        self.assertTrue(result.fix_verified)

    def test_unverifiable_abstention_and_provider_failure_never_verify_fix(self) -> None:
        document = "The default timeout is dynamic.\n"
        claim, candidate_patch = self.candidate(
            document, "default timeout is dynamically selected"
        )
        abstaining = FakeClient(
            VerificationResult(
                VerificationVerdict.ABSTAIN, (), "The available evidence is insufficient."
            )
        )
        result = self.verify(
            document,
            claim,
            candidate_patch,
            evidence=(self.source_evidence,),
            client=abstaining,
        )
        assert result.verification_finding is not None
        self.assertEqual(result.verification_finding.verdict, Verdict.UNVERIFIABLE)
        self.assertFalse(result.fix_verified)

        failed_client = FakeClient(error=RuntimeError("provider failed"))
        failed = self.verify(
            document,
            claim,
            candidate_patch,
            evidence=(self.source_evidence,),
            client=failed_client,
        )
        assert failed.verification_finding is not None
        self.assertEqual(failed.verification_finding.verdict, Verdict.UNVERIFIABLE)
        self.assertFalse(failed.fix_verified)

    def test_missing_and_ambiguous_reextracted_claims_are_not_verified(self) -> None:
        document = "The default timeout is 60.\n"
        claim, missing_patch = self.candidate(document, "not a supported claim")
        missing = self.verify(document, claim, missing_patch)
        self.assertEqual(missing.re_extraction_status, ReExtractionStatus.MISSING)
        self.assertEqual(missing.extraction_coverage_status, ExtractionCoverageStatus.MISSING)
        self.assertFalse(missing.fix_verified)

        claim, ambiguous_patch = self.candidate(
            document, "The default timeout is 30. The default timeout is 30"
        )
        ambiguous = self.verify(document, claim, ambiguous_patch)
        self.assertEqual(ambiguous.re_extraction_status, ReExtractionStatus.AMBIGUOUS)
        self.assertEqual(
            ambiguous.extraction_coverage_status, ExtractionCoverageStatus.AMBIGUOUS
        )
        self.assertFalse(ambiguous.fix_verified)

    def test_every_supported_claim_in_the_edited_line_is_verified(self) -> None:
        document = "The default timeout is 60; use CLI flag `--retries`.\n"
        claims = extract_markdown(document, "docs/guide.md")
        default_claim = next(claim for claim in claims if claim.type is ClaimType.DEFAULT)
        candidate_patch = create_claim_patch(
            document,
            default_claim,
            expected_original=default_claim.text[default_claim.span_start : default_claim.span_end],
            replacement_text="The default timeout is 30",
        )

        result = self.verify(document, default_claim, candidate_patch)

        self.assertEqual(len(result.required_claims), 2)
        self.assertEqual(len(result.claim_verifications), 2)
        self.assertTrue(
            all(
                item.finding is not None and item.finding.verdict is Verdict.TRUE
                for item in result.claim_verifications
            )
        )
        self.assertTrue(result.fix_verified)

    def test_claim_shifted_by_multiline_edit_is_still_reverified(self) -> None:
        document = "The default timeout is 60; use CLI flag `--retries`.\n"
        claims = extract_markdown(document, "docs/guide.md")
        default_claim = next(claim for claim in claims if claim.type is ClaimType.DEFAULT)
        candidate_patch = create_claim_patch(
            document,
            default_claim,
            expected_original=default_claim.text[default_claim.span_start : default_claim.span_end],
            replacement_text="The default timeout is 30\n",
        )

        result = self.verify(document, default_claim, candidate_patch)

        self.assertEqual(len(result.required_claims), 2)
        self.assertEqual(len(result.claim_verifications), 2)
        self.assertTrue(result.fix_verified)

    def test_candidate_is_not_verified_when_another_claim_in_region_is_unverifiable(self) -> None:
        document = "The default timeout is 60; use CLI flag `--unknown`.\n"
        claims = extract_markdown(document, "docs/guide.md")
        default_claim = next(claim for claim in claims if claim.type is ClaimType.DEFAULT)
        candidate_patch = create_claim_patch(
            document,
            default_claim,
            expected_original=default_claim.text[default_claim.span_start : default_claim.span_end],
            replacement_text="The default timeout is 30",
        )

        result = self.verify(document, default_claim, candidate_patch)

        self.assertEqual(len(result.claim_verifications), 2)
        first_finding = result.claim_verifications[0].finding
        second_finding = result.claim_verifications[1].finding
        assert first_finding is not None
        assert second_finding is not None
        self.assertEqual(first_finding.verdict, Verdict.TRUE)
        self.assertEqual(second_finding.verdict, Verdict.UNVERIFIABLE)
        self.assertFalse(result.fix_verified)

    def test_edit_cannot_hide_an_existing_claim_from_reextraction(self) -> None:
        document = "The default timeout is 60; use CLI flag `--retries`.\n"
        claims = extract_markdown(document, "docs/guide.md")
        default_claim = next(claim for claim in claims if claim.type is ClaimType.DEFAULT)
        candidate_patch = create_claim_patch(
            document,
            default_claim,
            expected_original=default_claim.text[default_claim.span_start : default_claim.span_end],
            replacement_text="The default timeout is 30\n```",
        )

        result = self.verify(document, default_claim, candidate_patch)

        self.assertEqual(result.re_extraction_status, ReExtractionStatus.MISSING)
        self.assertEqual(result.extraction_coverage_status, ExtractionCoverageStatus.MISSING)
        self.assertFalse(result.fix_verified)

    def test_parser_rejection_for_malformed_candidate_fails_closed(self) -> None:
        document = "The default timeout is 60.\n"
        claim, candidate_patch = self.candidate(document, "The default timeout is 30")

        with mock_patch(
            "docdrift_core.fix_verification.MarkdownIt",
            side_effect=ValueError("malformed markdown"),
        ):
            result = self.verify(document, claim, candidate_patch)

        self.assertEqual(result.markdown_validation_status, MarkdownValidationStatus.INVALID)
        self.assertIn("ValueError", result.markdown_validation_error or "")
        self.assertEqual(result.claim_verifications, ())
        self.assertFalse(result.fix_verified)

    def test_stale_patch_and_claim_mismatch_are_rejected(self) -> None:
        document = "The default timeout is 60.\n"
        claim, candidate_patch = self.candidate(document, "default timeout is 30")
        stale = self.verify(document + "changed\n", claim, candidate_patch)
        self.assertEqual(stale.patch_status, CandidatePatchStatus.REJECTED)

        mismatched_claim = Claim(
            claim.id,
            claim.type,
            claim.subject,
            claim.expected,
            "a different extracted line",
            claim.document_path,
            claim.line,
            claim.span_start,
            claim.span_end,
        )
        mismatch = self.verify(document, mismatched_claim, candidate_patch)
        self.assertEqual(mismatch.patch_status, CandidatePatchStatus.REJECTED)
        self.assertFalse(mismatch.fix_verified)

    def test_mismatched_evidence_fails_closed_before_client_call(self) -> None:
        document = "The default timeout is dynamic.\n"
        claim, candidate_patch = self.candidate(
            document, "default timeout is dynamically selected"
        )
        client = FakeClient(VerificationResult(VerificationVerdict.ABSTAIN, (), "abstain"))

        result = self.verify(
            document,
            claim,
            candidate_patch,
            evidence=(Evidence("../outside.py", 1, "untrusted"),),
            client=client,
        )

        self.assertEqual(result.verification_status, ReVerificationStatus.FAILED)
        self.assertFalse(result.fix_verified)
        self.assertEqual(client.requests, [])

    def test_checker_exception_is_reported_as_failed_verification(self) -> None:
        document = "The default timeout is 60.\n"
        claim, candidate_patch = self.candidate(document, "default timeout is 30")

        with mock_patch(
            "docdrift_core.fix_verification.check_claim",
            side_effect=RuntimeError("checker failed"),
        ):
            result = self.verify(document, claim, candidate_patch)

        self.assertEqual(result.verification_status, ReVerificationStatus.FAILED)
        self.assertFalse(result.fix_verified)
        self.assertEqual(document, "The default timeout is 60.\n")


if __name__ == "__main__":
    unittest.main()
