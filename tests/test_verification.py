from __future__ import annotations

import unittest
from typing import cast

from docdrift_core import (
    Claim,
    ClaimType,
    Evidence,
    EvidenceSufficiencyGate,
    VerificationSnippet,
    VerificationVerdict,
    validate_verification_output,
)


class GroundedVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.claim = Claim(
            id="claim-1",
            type=ClaimType.DEFAULT,
            subject="timeout",
            expected={"value": "30"},
            text="The default timeout is 30.",
            document_path="docs/guide.md",
            line=4,
            span_start=0,
            span_end=30,
        )
        self.snippet = VerificationSnippet(
            path="src/client.py", line=12, text="def connect(timeout=30): return timeout"
        )

    def response(
        self,
        *,
        verdict: str = "true",
        reference: str | None = None,
        quote: str = "timeout=30",
    ) -> dict[str, object]:
        return {
            "verdict": verdict,
            "evidence_refs": [
                {"ref": reference or self.snippet.reference, "quote": quote}
            ],
            "rationale": "The source declaration shows the documented value.",
        }

    def test_exact_evidence_and_sufficiency_gate_accept_structured_verdict(self) -> None:
        for verdict in ("true", "false"):
            with self.subTest(verdict=verdict):
                result = validate_verification_output(
                    self.claim,
                    self.response(verdict=verdict),
                    [self.snippet],
                    evidence_sufficient=lambda claim, snippets, citations: (
                        claim.id == self.claim.id
                        and snippets == (self.snippet,)
                        and citations[0].quote == "timeout=30"
                    ),
                )

                self.assertEqual(result.verdict, VerificationVerdict(verdict))
                self.assertEqual(result.evidence_refs[0].reference, self.snippet.reference)
                self.assertEqual(result.evidence_refs[0].quote, "timeout=30")

    def test_existing_core_evidence_adapts_and_reference_is_stable(self) -> None:
        evidence = Evidence("src/client.py", 12, "def connect(timeout=30): return timeout")
        first = VerificationSnippet.from_evidence(evidence)
        second = VerificationSnippet.from_evidence(evidence)

        self.assertEqual(first.reference, second.reference)
        self.assertEqual(first.path, evidence.path)
        self.assertEqual(first.line, evidence.line)
        self.assertEqual(first.text, evidence.snippet)

    def test_fabricated_reference_abstains(self) -> None:
        result = validate_verification_output(
            self.claim,
            self.response(reference="ev_fabricated"),
            [self.snippet],
            evidence_sufficient=lambda *_: True,
        )

        self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)
        self.assertIn("not supplied", result.rationale)

    def test_mismatched_quote_abstains(self) -> None:
        result = validate_verification_output(
            self.claim,
            self.response(quote="timeout=999"),
            [self.snippet],
            evidence_sufficient=lambda *_: True,
        )

        self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)
        self.assertIn("does not exactly match", result.rationale)

    def test_malformed_or_free_form_output_abstains_without_parsing(self) -> None:
        for output in (
            "The claim is true because timeout is 30.",
            '{"verdict":"true","evidence_refs":[],"rationale":"ok"}',
            {**self.response(), "extra": "untrusted fields are rejected"},
            {**self.response(), "verdict": "TRUE"},
            {**self.response(), "rationale": " "},
            {**self.response(), "evidence_refs": [{"ref": self.snippet.reference}]},
            {
                **self.response(),
                "evidence_refs": [
                    {"ref": self.snippet.reference, "quote": "timeout=30", "path": "fake.py"}
                ],
            },
        ):
            with self.subTest(output=output):
                result = validate_verification_output(
                    self.claim,
                    output,
                    [self.snippet],
                    evidence_sufficient=lambda *_: True,
                )
                self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)

    def test_empty_evidence_cannot_support_true_or_false(self) -> None:
        for verdict in ("true", "false"):
            with self.subTest(verdict=verdict):
                output: dict[str, object] = {
                    "verdict": verdict,
                    "evidence_refs": [],
                    "rationale": "A verdict without a source citation.",
                }
                result = validate_verification_output(
                    self.claim,
                    output,
                    [],
                    evidence_sufficient=lambda *_: True,
                )
                self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)

    def test_citation_alone_does_not_accept_verdict_without_sufficiency_gate(self) -> None:
        result = validate_verification_output(self.claim, self.response(), [self.snippet])

        self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)
        self.assertIn("sufficiency gate", result.rationale)

    def test_insufficient_or_failing_gate_abstains(self) -> None:
        gates: tuple[EvidenceSufficiencyGate, ...] = (
            lambda *_: False,
            cast(EvidenceSufficiencyGate, lambda *_: "yes"),
            cast(
                EvidenceSufficiencyGate,
                lambda *_: (_ for _ in ()).throw(RuntimeError("fail")),
            ),
        )
        for gate in gates:
            with self.subTest(gate=gate):
                result = validate_verification_output(
                    self.claim,
                    self.response(verdict="false"),
                    [self.snippet],
                    evidence_sufficient=gate,
                )
                self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)
                self.assertIn("not established as sufficient", result.rationale)

    def test_explicit_model_abstention_is_preserved(self) -> None:
        output = {
            "verdict": "abstain",
            "evidence_refs": [],
            "rationale": "The supplied source does not resolve the claim.",
        }

        result = validate_verification_output(self.claim, output, [self.snippet])

        self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)
        self.assertEqual(result.rationale, output["rationale"])

    def test_evidence_location_rejects_absolute_traversal_and_invalid_lines(self) -> None:
        for path, line in (("../outside.py", 1), ("/root/file.py", 1), ("src\\file.py", 1), ("src/file.py", 0)):
            with self.subTest(path=path, line=line), self.assertRaises(ValueError):
                VerificationSnippet(path, line, "source")


if __name__ == "__main__":
    unittest.main()
