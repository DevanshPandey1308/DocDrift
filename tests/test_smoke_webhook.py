from __future__ import annotations

import json
import unittest

from docdrift_api.webhooks import _pull_request_record, _signature_is_valid

from scripts.smoke_webhook import build_payload, signature_header


class SmokeWebhookTests(unittest.TestCase):
    def test_signature_matches_api_verification_for_exact_payload_bytes(self) -> None:
        secret = "test-only-webhook-secret"
        body = build_payload(
            repo="owner/project",
            pull_request=4,
            head_sha="a" * 40,
            base_sha="b" * 40,
            installation_id=7,
            repository_id=42,
        )

        self.assertTrue(_signature_is_valid(signature_header(body, secret), secret, body))

    def test_payload_has_all_api_required_pull_request_fields(self) -> None:
        payload = build_payload(
            repo="owner/project",
            pull_request=4,
            head_sha="a" * 40,
            base_sha="b" * 40,
            installation_id=7,
            repository_id=42,
        )

        record = _pull_request_record(json.loads(payload))
        self.assertEqual(record.repository_full_name, "owner/project")
        self.assertEqual(record.github_repository_id, 42)
        self.assertEqual(record.installation_id, 7)
        self.assertEqual(record.pull_request_number, 4)
        self.assertEqual(record.head_sha, "a" * 40)
        self.assertEqual(record.base_sha, "b" * 40)


if __name__ == "__main__":
    unittest.main()
