from __future__ import annotations

import asyncio
import unittest
from collections.abc import Awaitable, Coroutine
from typing import Any, cast

from docdrift_core import (
    Claim,
    ClaimType,
    Evidence,
    EvidenceCitation,
    Finding,
    Verdict,
    VerificationRequest,
    VerificationResult,
    VerificationSnippet,
    VerificationVerdict,
    orchestrate_verification,
    validate_verification_output,
)


class ImmediateAwaitable:
    """Complete an awaitable without creating a Windows asyncio event loop."""

    def __init__(self, result: object = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error

    def __await__(self):  # type: ignore[no-untyped-def]
        if self.error is not None:
            raise self.error
        if False:
            yield None
        return self.result


class FakeClient:
    def __init__(self, result: object = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.requests: list[VerificationRequest] = []

    def verify(self, request: VerificationRequest) -> Awaitable[VerificationResult]:
        self.requests.append(request)
        result = self.result(request) if callable(self.result) else self.result
        return cast(Awaitable[VerificationResult], ImmediateAwaitable(result, self.error))


class OrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.claim = Claim(
            "claim-1",
            ClaimType.DEFAULT,
            "timeout",
            {"value": "30"},
            "The default timeout is 30.",
            "docs/guide.md",
            4,
            0,
            30,
        )
        self.evidence = Evidence("src/client.py", 12, "def connect(timeout=30): return timeout")
        self.deterministic = Finding(
            self.claim,
            Verdict.UNVERIFIABLE,
            "No deterministic checker could establish this claim.",
            (),
            "default_checker",
            True,
        )

    @staticmethod
    def run_without_event_loop(coro: Coroutine[Any, Any, Finding]) -> Finding:
        """Run a coroutine whose only await is the synchronous fake client."""
        try:
            coro.send(None)
        except StopIteration as completed:
            return cast(Finding, completed.value)
        raise AssertionError("fake client unexpectedly suspended")

    def verified_result(self, verdict: str) -> VerificationResult:
        snippet = VerificationSnippet.from_evidence(self.evidence)
        result = validate_verification_output(
            self.claim,
            {
                "verdict": verdict,
                "evidence_refs": [{"ref": snippet.reference, "quote": "timeout=30"}],
                "rationale": "The declaration supports the documented default.",
            },
            (snippet,),
            evidence_sufficient=lambda claim, cited, citations: (
                claim == self.claim and cited == (snippet,) and citations[0].quote == "timeout=30"
            ),
        )
        self.assertEqual(result.verdict, VerificationVerdict(verdict))
        return result

    def test_deterministic_true_and_false_are_returned_unchanged_without_client_call(self) -> None:
        client = FakeClient(error=AssertionError("must not be called"))
        for verdict in (Verdict.TRUE, Verdict.FALSE):
            original = Finding(self.claim, verdict, "deterministic", (self.evidence,), "checker")
            with self.subTest(verdict=verdict):
                result = self.run_without_event_loop(
                    orchestrate_verification(self.claim, original, (self.evidence,), client)
                )
                self.assertIs(result, original)
        self.assertEqual(client.requests, [])

    def test_unverifiable_prose_claim_uses_bounded_relevant_evidence(self) -> None:
        evidence = tuple(
            Evidence(f"src/module_{index}.py", 1, "x" * 4_000) for index in range(20)
        )
        client = FakeClient(
            lambda request: validate_verification_output(
                request.claim,
                {
                    "verdict": "true",
                    "evidence_refs": [
                        {"ref": request.evidence[0].reference, "quote": "x"}
                    ],
                    "rationale": "The cited bounded snippet supports the claim.",
                },
                request.evidence,
                evidence_sufficient=lambda *_: True,
            )
        )

        result = self.run_without_event_loop(
            orchestrate_verification(self.claim, self.deterministic, evidence, client)
        )

        self.assertEqual(len(client.requests), 1)
        request = client.requests[0]
        self.assertEqual(request.claim, self.claim)
        self.assertEqual(len(request.evidence), 4)
        self.assertLessEqual(sum(len(item.text) for item in request.evidence), 16_000)
        self.assertEqual(request.evidence[0].path, "src/module_0.py")
        self.assertEqual(result.verdict, Verdict.TRUE)
        self.assertEqual(result.verification_method, "grounded_llm")
        self.assertFalse(result.deterministic)

    def test_grounded_true_and_false_results_are_propagated(self) -> None:
        for raw, expected in (("true", Verdict.TRUE), ("false", Verdict.FALSE)):
            with self.subTest(verdict=raw):
                client = FakeClient(self.verified_result(raw))
                result = self.run_without_event_loop(
                    orchestrate_verification(self.claim, self.deterministic, (self.evidence,), client)
                )
                self.assertEqual(result.verdict, expected)
                self.assertEqual(result.evidence, (self.evidence,))
                self.assertFalse(result.deterministic)

    def test_client_abstention_is_preserved(self) -> None:
        abstention = VerificationResult(
            VerificationVerdict.ABSTAIN, (), "Evidence does not establish the claim."
        )
        client = FakeClient(abstention)

        result = self.run_without_event_loop(
            orchestrate_verification(self.claim, self.deterministic, (self.evidence,), client)
        )

        self.assertEqual(result.verdict, Verdict.UNVERIFIABLE)
        self.assertEqual(result.explanation, abstention.rationale)
        self.assertFalse(result.deterministic)

    def test_missing_or_insufficient_evidence_does_not_call_client(self) -> None:
        client = FakeClient(self.verified_result("true"))
        result = self.run_without_event_loop(
            orchestrate_verification(self.claim, self.deterministic, (), client)
        )

        self.assertIs(result, self.deterministic)
        self.assertEqual(client.requests, [])

        snippet = VerificationSnippet.from_evidence(self.evidence)
        insufficient = validate_verification_output(
            self.claim,
            {
                "verdict": "true",
                "evidence_refs": [{"ref": snippet.reference, "quote": "timeout=30"}],
                "rationale": "The quote is present but does not establish the claim.",
            },
            (snippet,),
            evidence_sufficient=lambda *_: False,
        )
        self.assertEqual(insufficient.verdict, VerificationVerdict.ABSTAIN)
        insufficient_client = FakeClient(insufficient)
        safe = self.run_without_event_loop(
            orchestrate_verification(
                self.claim, self.deterministic, (self.evidence,), insufficient_client
            )
        )
        self.assertEqual(safe.verdict, Verdict.UNVERIFIABLE)
        self.assertEqual(safe.explanation, insufficient.rationale)

    def test_client_exception_and_invalid_citations_fail_closed(self) -> None:
        failed = self.run_without_event_loop(
            orchestrate_verification(
                self.claim,
                self.deterministic,
                (self.evidence,),
                FakeClient(error=RuntimeError("provider secret")),
            )
        )
        self.assertEqual(failed.verdict, Verdict.UNVERIFIABLE)
        self.assertNotIn("provider secret", failed.explanation)

        fabricated = VerificationResult(
            VerificationVerdict.TRUE,
            (EvidenceCitation("ev_fake", "timeout=30"),),
            "Untrusted client result.",
        )
        invalid = self.run_without_event_loop(
            orchestrate_verification(
                self.claim, self.deterministic, (self.evidence,), FakeClient(fabricated)
            )
        )
        self.assertEqual(invalid.verdict, Verdict.UNVERIFIABLE)
        self.assertFalse(invalid.deterministic)

    def test_cancellation_propagates(self) -> None:
        with self.assertRaises(asyncio.CancelledError):
            self.run_without_event_loop(
                orchestrate_verification(
                    self.claim,
                    self.deterministic,
                    (self.evidence,),
                    FakeClient(error=asyncio.CancelledError()),
                )
            )

    def test_unsupported_claim_type_is_not_escalated(self) -> None:
        file_claim = Claim(
            "path-claim", ClaimType.FILE_PATH, "missing.py", {}, "See `missing.py`.",
            "docs/guide.md", 1, 0, 20
        )
        file_finding = Finding(
            file_claim, Verdict.UNVERIFIABLE, "Path checking is not applicable.", (),
            "path_checker", True
        )
        client = FakeClient(self.verified_result("true"))

        result = self.run_without_event_loop(
            orchestrate_verification(file_claim, file_finding, (self.evidence,), client)
        )

        self.assertIs(result, file_finding)
        self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()
