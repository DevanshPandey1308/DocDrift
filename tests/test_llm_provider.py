from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import docdrift_worker.llm_provider as llm_provider_module
from docdrift_api.config import Settings
from docdrift_core import (
    Claim,
    ClaimType,
    Evidence,
    Finding,
    GroundedLLMClient,
    Verdict,
    VerificationRequest,
    VerificationSnippet,
)
from docdrift_core.verification import VerificationVerdict
from docdrift_worker.llm_provider import AnthropicDocDriftProvider, RunLLMControls


class FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.text = ""

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.text)],
            usage=SimpleNamespace(input_tokens=12, output_tokens=5),
        )


class AnthropicAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.messages = FakeMessages()
        self.provider = AnthropicDocDriftProvider(
            SimpleNamespace(messages=self.messages),
            verification_model="configured-verifier",
            fix_model="configured-fixer",
        )
        self.claim = Claim(
            "c1", ClaimType.DEFAULT, "timeout", {"value": "30"},
            "The timeout defaults to 30.", "docs/guide.md", 1, 0, 29,
        )
        self.snippet = VerificationSnippet("src/client.py", 2, "def connect(timeout=30): pass")
        self.request = VerificationRequest(self.claim, (self.snippet,))

    async def test_verification_uses_structured_output_and_reports_sdk_usage(self) -> None:
        self.messages.text = (
            '{"verdict":"true","evidence_refs":[{"ref":"'
            + self.snippet.reference
            + '","quote":"timeout=30"}],"rationale":"The source default matches."}'
        )
        controls = RunLLMControls(self.provider, token_budget=10_000)
        client = GroundedLLMClient(
            controls,
            evidence_sufficient=lambda claim, snippets, citations: True,
            timeout_seconds=1,
        )

        result = await client.verify(self.request)
        self.assertEqual(result.verdict, VerificationVerdict.TRUE)
        self.assertEqual((result.input_tokens, result.output_tokens), (12, 5))
        call = self.messages.calls[0]
        self.assertEqual(call["model"], "configured-verifier")
        self.assertEqual(call.get("tools", None), None)
        self.assertIn("format", call["output_config"])

    async def test_cache_avoids_duplicates_and_invalidates_model_evidence_and_prompt(self) -> None:
        self.messages.text = "{}"
        controls = RunLLMControls(self.provider, token_budget=10_000)
        await controls.verify(self.request)
        await controls.verify(self.request)
        self.assertEqual(len(self.messages.calls), 1)
        self.assertEqual(controls.usage_record()["input_tokens"], 12)
        self.provider._verification_model = "new-model"
        await controls.verify(self.request)
        self.assertEqual(len(self.messages.calls), 2)
        changed_evidence = VerificationRequest(
            self.claim,
            (VerificationSnippet("src/client.py", 3, "def connect(timeout=31): pass"),),
        )
        await controls.verify(changed_evidence)
        self.assertEqual(len(self.messages.calls), 3)
        with patch.object(llm_provider_module, "VERIFY_SYSTEM_PROMPT", "new prompt version"):
            await controls.verify(changed_evidence)
        self.assertEqual(len(self.messages.calls), 4)

    async def test_fix_response_is_strict_and_carries_usage_without_applying_files(self) -> None:
        self.messages.text = '{"replacement":"The timeout defaults to 30."}'
        finding = Finding(
            self.claim,
            Verdict.FALSE,
            "Mismatch",
            (Evidence("src/client.py", 2, self.snippet.text),),
            "static_python",
        )
        controls = RunLLMControls(self.provider, token_budget=10_000)
        response = await controls.propose_replacement(finding, finding.evidence)
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.replacement, "The timeout defaults to 30.")
        self.assertEqual((response.input_tokens, response.output_tokens), (12, 5))
        self.assertEqual(controls.usage_record()["used_tokens"], 17)

        self.messages.text = '{"replacement":"ok","unexpected":"whole document"}'
        invalid = await self.provider.propose_replacement(finding, finding.evidence)
        self.assertIsNone(invalid)

    async def test_budget_exhaustion_fails_closed_through_grounded_client(self) -> None:
        self.messages.text = "{}"
        controls = RunLLMControls(self.provider, token_budget=1)
        client = GroundedLLMClient(
            controls,
            evidence_sufficient=lambda *_: True,
            timeout_seconds=1,
        )
        result = await client.verify(self.request)
        self.assertEqual(result.verdict, VerificationVerdict.ABSTAIN)
        self.assertEqual(self.messages.calls, [])


class LLMSettingsTests(unittest.TestCase):
    def test_provider_is_disabled_without_explicit_configuration(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings.from_env()
        self.assertFalse(settings.llm_enabled)
        self.assertIsNone(settings.anthropic_api_key)

    def test_provider_settings_are_environment_backed_and_bounded(self) -> None:
        values = {
            "DOCDRIFT_LLM_ENABLED": "true",
            "ANTHROPIC_API_KEY": "example-secret",
            "DOCDRIFT_ANTHROPIC_VERIFICATION_MODEL": "verify-model",
            "DOCDRIFT_ANTHROPIC_FIX_MODEL": "fix-model",
            "DOCDRIFT_LLM_TIMEOUT_SECONDS": "12.5",
            "DOCDRIFT_LLM_RUN_TOKEN_BUDGET": "25000",
        }
        with patch.dict("os.environ", values, clear=True):
            settings = Settings.from_env()
        self.assertTrue(settings.llm_enabled)
        self.assertEqual(settings.anthropic_verification_model, "verify-model")
        self.assertEqual(settings.llm_timeout_seconds, 12.5)
        self.assertEqual(settings.llm_run_token_budget, 25_000)
        self.assertNotIn("example-secret", repr(settings))

        with self.assertRaises(ValueError):
            Settings(llm_run_token_budget=100_001)

if __name__ == "__main__":
    unittest.main()
