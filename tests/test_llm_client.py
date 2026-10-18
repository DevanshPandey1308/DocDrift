from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import Mapping

from docdrift_core import (
    Claim,
    ClaimType,
    EvidenceSufficiencyGate,
    GroundedLLMClient,
    LLMProvider,
    VerificationRequest,
    VerificationSnippet,
    VerificationVerdict,
)
from docdrift_core.llm_client import (
    MAX_PROVIDER_TIMEOUT_SECONDS,
    adapt_provider_response,
)


class FakeProvider:
    def __init__(
        self,
        response: str | Mapping[str, object] = "{}",
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    async def verify(self, request: VerificationRequest) -> str | Mapping[str, object]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


class SlowProvider:
    async def verify(self, request: VerificationRequest) -> Mapping[str, object]:
        del request
        await asyncio.sleep(1)
        return {}


class WaitingProvider:
    async def verify(self, request: VerificationRequest) -> Mapping[str, object]:
        del request
        await asyncio.Event().wait()
        return {}


class LLMClientTests(unittest.IsolatedAsyncioTestCase):
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
            "src/client.py", 12, "def connect(timeout=30): return timeout"
        )
        self.request = VerificationRequest(self.claim, (self.snippet,))

    def response(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "verdict": "true",
            "evidence_refs": [
                {"ref": self.snippet.reference, "quote": "timeout=30"}
            ],
            "rationale": "The declaration supports the documented default.",
        }
        payload.update(overrides)
        return payload

    def client(
        self,
        provider: LLMProvider,
        *,
        gate: EvidenceSufficiencyGate | None = None,
        timeout: float = 2,
    ) -> GroundedLLMClient:
        return GroundedLLMClient(
            provider,
            evidence_sufficient=gate if gate is not None else (lambda *_: True),
            timeout_seconds=timeout,
        )

    async def test_valid_json_response_passes_existing_grounding_contract(self) -> None:
        provider = FakeProvider(response=json.dumps(self.response()))
        result = await self.client(provider).verify(self.request)

        self.assertEqual(result.verdict, VerificationVerdict.TRUE)
        self.assertEqual(result.evidence_refs[0].reference, self.snippet.reference)
        self.assertEqual(provider.calls, 1)

    async def test_malformed_json_and_invalid_structured_response_abstain(self) -> None:
        for response in (
            '{"verdict":"true",',
            "The claim is true.",
            '{"verdict":"true","verdict":"false","evidence_refs":[],"rationale":"duplicate"}',
        ):
            with self.subTest(response=response):
                result = await self.client(FakeProvider(response)).verify(self.request)
                self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)

    async def test_missing_fields_and_unknown_verdict_abstain(self) -> None:
        for response in (
            {"verdict": "true", "evidence_refs": []},
            self.response(verdict="unverifiable"),
            self.response(verdict="maybe"),
        ):
            with self.subTest(response=response):
                result = await self.client(FakeProvider(response)).verify(self.request)
                self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)

    async def test_fabricated_reference_and_quote_mismatch_abstain(self) -> None:
        responses = (
            self.response(
                evidence_refs=[{"ref": "ev_fabricated", "quote": "timeout=30"}]
            ),
            self.response(
                evidence_refs=[
                    {"ref": self.snippet.reference, "quote": "timeout=999"}
                ]
            ),
        )
        for response in responses:
            with self.subTest(response=response):
                result = await self.client(FakeProvider(response)).verify(self.request)
                self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)

    async def test_provider_exception_fails_closed_without_exposing_exception(self) -> None:
        result = await self.client(
            FakeProvider(error=RuntimeError("token=secret-provider-token"))
        ).verify(self.request)

        self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)
        self.assertNotIn("secret-provider-token", result.rationale)

    async def test_timeout_fails_closed(self) -> None:
        result = await self.client(SlowProvider(), timeout=0.01).verify(self.request)

        self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)
        self.assertIn("timed out", result.rationale)

    async def test_task_cancellation_propagates(self) -> None:
        task = asyncio.create_task(self.client(WaitingProvider()).verify(self.request))
        await asyncio.sleep(0)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_empty_or_insufficient_evidence_abstains(self) -> None:
        provider = FakeProvider(self.response())
        empty_request = VerificationRequest(self.claim, ())
        result = await self.client(provider).verify(empty_request)
        self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)
        self.assertEqual(provider.calls, 0)

        insufficient = await self.client(
            FakeProvider(self.response()), gate=lambda *_: False
        ).verify(self.request)
        self.assertEqual(insufficient.verdict, VerificationVerdict.ABSTAIN)


class ProviderAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.claim = Claim(
            "c1", ClaimType.FILE_PATH, "src/api.py", {}, "See `src/api.py`.",
            "README.md", 1, 0, 18
        )
        self.snippet = VerificationSnippet("src/api.py", 1, "def app(): pass")
        self.response: dict[str, object] = {
            "verdict": "true",
            "evidence_refs": [{"ref": self.snippet.reference, "quote": "def app"}],
            "rationale": "The source declares the file contents.",
        }

    def test_adapter_uses_existing_validator_for_structured_objects(self) -> None:
        result = adapt_provider_response(
            self.claim,
            self.response,
            (self.snippet,),
            evidence_sufficient=lambda *_: True,
        )

        self.assertEqual(result.verdict, VerificationVerdict.TRUE)

    def test_malformed_json_duplicate_keys_and_unsupported_response_abstain(self) -> None:
        for response in (
            '{"verdict":"true",',
            '{"verdict":"true","verdict":"false","evidence_refs":[],"rationale":"x"}',
            ["not", "a", "structured response"],
        ):
            with self.subTest(response=response):
                result = adapt_provider_response(
                    self.claim,
                    response,
                    (self.snippet,),
                    evidence_sufficient=lambda *_: True,
                )
                self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)

    def test_missing_fields_and_unknown_verdict_abstain(self) -> None:
        for response in (
            {"verdict": "true", "evidence_refs": []},
            {**self.response, "verdict": "unverifiable"},
        ):
            with self.subTest(response=response):
                result = adapt_provider_response(
                    self.claim,
                    response,
                    (self.snippet,),
                    evidence_sufficient=lambda *_: True,
                )
                self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)

    def test_fabricated_reference_and_mismatched_quote_abstain(self) -> None:
        responses = (
            {
                **self.response,
                "evidence_refs": [{"ref": "ev_fake", "quote": "def app"}],
            },
            {
                **self.response,
                "evidence_refs": [
                    {"ref": self.snippet.reference, "quote": "def missing"}
                ],
            },
        )
        for response in responses:
            with self.subTest(response=response):
                result = adapt_provider_response(
                    self.claim,
                    response,
                    (self.snippet,),
                    evidence_sufficient=lambda *_: True,
                )
                self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)


class ClientConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.claim = Claim(
            "c1", ClaimType.FILE_PATH, "src/api.py", {}, "See `src/api.py`.",
            "README.md", 1, 0, 18
        )

    def test_timeout_is_required_and_bounded(self) -> None:
        with self.assertRaises(ValueError):
            GroundedLLMClient(FakeProvider({}), evidence_sufficient=lambda *_: True, timeout_seconds=0)
        with self.assertRaises(ValueError):
            GroundedLLMClient(
                FakeProvider({}),
                evidence_sufficient=lambda *_: True,
                timeout_seconds=MAX_PROVIDER_TIMEOUT_SECONDS + 1,
            )
        with self.assertRaises(TypeError):
            GroundedLLMClient(FakeProvider({}), evidence_sufficient=None, timeout_seconds=1)  # type: ignore[arg-type]

    def test_request_evidence_size_is_bounded(self) -> None:
        too_large = VerificationSnippet("src/large.py", 1, "x" * 4_001)
        with self.assertRaises(ValueError):
            VerificationRequest(self.claim, (too_large,))


if __name__ == "__main__":
    unittest.main()
