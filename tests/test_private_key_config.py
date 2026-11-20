from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from docdrift_api.config import Settings
from docdrift_api.github_installations import (
    GitHubInstallationClient,
    GitHubInstallationSyncError,
)


class GitHubAppPrivateKeyConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        cls._pem = cls._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")

    def _settings_with_key(self, value: str) -> Settings:
        with patch.dict(
            "os.environ",
            {
                "DOCDRIFT_GITHUB_APP_ID": "123",
                "DOCDRIFT_GITHUB_APP_PRIVATE_KEY": value,
            },
            clear=True,
        ):
            return Settings.from_env()

    def _assert_parses_as_rsa(self, value: str | None) -> None:
        if value is None:
            self.fail("Expected a normalized private key.")
        parsed = serialization.load_pem_private_key(value.encode("utf-8"), password=None)
        self.assertIsInstance(parsed, rsa.RSAPrivateKey)
        self.assertEqual(
            parsed.public_key().public_numbers(),
            self._private_key.public_key().public_numbers(),
        )

    def test_normalizes_supported_inline_key_representations(self) -> None:
        variants = (
            self._pem,
            self._pem.replace("\n", r"\n"),
            f'"{self._pem}"',
            self._pem.replace("\n", "\r\n"),
            " ".join(self._pem.splitlines()),
        )
        for variant in variants:
            with self.subTest(representation="PEM form"):
                settings = self._settings_with_key(variant)
                self._assert_parses_as_rsa(settings.github_app_private_key)
                self.assertFalse(self._pem in repr(settings))

    def test_normalizes_single_line_rsa_private_key_header(self) -> None:
        traditional_pem = self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        settings = self._settings_with_key(" ".join(traditional_pem.splitlines()))
        self._assert_parses_as_rsa(settings.github_app_private_key)

    def test_private_key_path_takes_precedence_and_loads_at_settings_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            key_path = Path(temporary_directory) / "github-app.pem"
            key_path.write_text(self._pem, encoding="utf-8")
            with patch.dict(
                "os.environ",
                {
                    "DOCDRIFT_GITHUB_APP_ID": "123",
                    "DOCDRIFT_GITHUB_APP_PRIVATE_KEY": "invalid inline value",
                    "DOCDRIFT_GITHUB_APP_PRIVATE_KEY_PATH": str(key_path),
                },
                clear=True,
            ):
                settings = Settings.from_env()
        self._assert_parses_as_rsa(settings.github_app_private_key)
        self.assertFalse(self._pem in repr(settings))

    def test_unreadable_key_path_has_secret_free_configuration_error(self) -> None:
        with patch.dict(
            "os.environ",
            {"DOCDRIFT_GITHUB_APP_PRIVATE_KEY_PATH": "missing-private-key-file.pem"},
            clear=True,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "DOCDRIFT_GITHUB_APP_PRIVATE_KEY_PATH could not be read as UTF-8",
            ) as raised:
                Settings.from_env()
        self.assertFalse(self._pem in str(raised.exception))

    def test_garbage_key_has_safe_format_diagnostic_and_no_key_text(self) -> None:
        private_text = "private-key-content-must-not-appear"
        client = GitHubInstallationClient("123", private_text)
        with self.assertRaises(GitHubInstallationSyncError) as raised:
            client._app_jwt()

        error = raised.exception
        self.assertEqual(error.diagnostic_code, "invalid_private_key_format")
        self.assertEqual(
            error.upstream_detail,
            "pem_lines=1;pem_header_present=false;pem_footer_present=false",
        )
        self.assertFalse(private_text in str(error))
        self.assertFalse(private_text in (error.upstream_detail or ""))


if __name__ == "__main__":
    unittest.main()
