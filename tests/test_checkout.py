from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from docdrift_worker.checkout import (
    CheckoutTimeoutError,
    GitHubRepositoryCheckout,
    RepositoryCheckoutError,
    _run_command,
    github_repository_url,
)
from git_helpers import create_git_repository


class RepositoryCheckoutTests(unittest.IsolatedAsyncioTestCase):
    def test_github_url_is_fixed_and_repository_names_are_validated(self) -> None:
        self.assertEqual(
            github_repository_url("fastapi/fastapi"),
            "https://github.com/fastapi/fastapi.git",
        )
        for invalid in ("../repo", "owner/../repo", "a/b/c", "https://evil.test/x", "owner/repo?x"):
            with self.subTest(repository=invalid), self.assertRaises(ValueError):
                github_repository_url(invalid)

    async def test_checkout_rejects_untrusted_remote_and_invalid_pr_number(self) -> None:
        remote = GitHubRepositoryCheckout(
            remote_url_factory=lambda _name: "https://example.invalid/owner/repo.git"
        )
        with self.assertRaisesRegex(ValueError, "GitHub HTTPS"):
            async with remote.checkout("owner/repo", 1, "a" * 40):
                self.fail("arbitrary remote URL must not be fetched")

        local = GitHubRepositoryCheckout(remote_url_factory=lambda _name: "unused")
        for invalid_number in (0, -1, True):
            with self.subTest(number=invalid_number), self.assertRaisesRegex(
                ValueError, "positive integer"
            ):
                async with local.checkout("owner/repo", invalid_number, "a" * 40):
                    self.fail("invalid pull request number must not start checkout")

    async def test_checkout_is_detached_at_requested_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            source = temporary_path / "source"
            first_sha, _second_sha = create_git_repository(
                source,
                (
                    {"README.md": "The default timeout is 30.\n", "src/api.py": "def f():\n    return 30\n"},
                    {"README.md": "The default timeout is 60.\n", "src/api.py": "def f():\n    return 60\n"},
                ),
            )
            checkout = GitHubRepositoryCheckout(
                temporary_parent=temporary_path / "checkouts",
                remote_url_factory=lambda _name: str(source),
            )

            async with checkout.checkout("owner/repo", 17, first_sha) as root:
                self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "The default timeout is 30.\n")
                self.assertEqual((root / ".git" / "HEAD").read_text().strip(), first_sha)

            self.assertEqual(list((temporary_path / "checkouts").iterdir()), [])

    async def test_invalid_revision_and_fetch_failure_clean_up_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            source = temporary_path / "source"
            (source / "unused").mkdir(parents=True)
            parent = temporary_path / "checkouts"
            checkout = GitHubRepositoryCheckout(
                temporary_parent=parent,
                remote_url_factory=lambda _name: str(source),
            )

            with self.assertRaisesRegex(ValueError, "40-character"):
                async with checkout.checkout("owner/repo", 1, "bad-sha"):
                    self.fail("invalid revision must not start checkout")
            self.assertFalse(parent.exists())

            with self.assertRaises(RepositoryCheckoutError):
                async with checkout.checkout("owner/repo", 1, "a" * 40):
                    self.fail("an unavailable revision must fail")
            self.assertEqual(list(parent.iterdir()), [])

    async def test_git_process_timeout_kills_process_and_checkout_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            with self.assertRaises(CheckoutTimeoutError):
                await _run_command(
                    sys.executable,
                    ["-c", "import time; time.sleep(2)"],
                    cwd=temporary_path,
                    timeout_seconds=0.02,
                )

            source = temporary_path / "source"
            create_git_repository(source, ({"README.md": "test\n"},))
            parent = temporary_path / "timed-checkouts"
            checkout = GitHubRepositoryCheckout(
                timeout_seconds=1e-9,
                temporary_parent=parent,
                remote_url_factory=lambda _name: str(source),
            )
            with self.assertRaises(CheckoutTimeoutError):
                async with checkout.checkout("owner/repo", 1, "a" * 40):
                    self.fail("a near-zero command timeout must stop checkout")
            self.assertEqual(list(parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
