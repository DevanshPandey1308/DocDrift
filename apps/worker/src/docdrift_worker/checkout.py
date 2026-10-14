"""Fetch a public GitHub PR revision into a temporary, non-executed checkout."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

_REPOSITORY_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


class RepositoryCheckoutError(RuntimeError):
    """Sanitized error raised when repository metadata or Git retrieval fails."""


class CheckoutTimeoutError(TimeoutError):
    """A bounded Git command exceeded its configured timeout."""


def github_repository_url(full_name: str) -> str:
    """Construct the only production-supported remote URL from an owner/repo name."""
    if not isinstance(full_name, str) or full_name.count("/") != 1:
        raise ValueError("Repository identity must be an owner/repository pair.")
    owner, repository = full_name.split("/", 1)
    if (
        not _REPOSITORY_COMPONENT.fullmatch(owner)
        or not _REPOSITORY_COMPONENT.fullmatch(repository)
        or owner in {".", ".."}
        or repository in {".", ".."}
    ):
        raise ValueError("Repository identity contains unsupported characters.")
    return f"https://github.com/{owner}/{repository}.git"


def _git_environment() -> dict[str, str]:
    """Disable inherited Git config and credential helpers for untrusted repo work."""
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.upper().startswith("GIT_"):
            environment.pop(key, None)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


async def _run_command(
    executable: str,
    arguments: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> str:
    """Run a bounded process while keeping command output out of logs/errors."""
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            *arguments,
            cwd=cwd,
            env=_git_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise RepositoryCheckoutError("Git could not be started.") from error

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as error:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise CheckoutTimeoutError("A Git operation exceeded its time limit.") from error
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise

    if process.returncode != 0:
        raise RepositoryCheckoutError("Git could not retrieve the requested revision.")
    return stdout.decode("utf-8", errors="replace").strip()


class GitHubRepositoryCheckout:
    """Checkout an exact commit from GitHub HTTPS, without running repository code.

    ``remote_url_factory`` is an internal dependency seam for local Git tests. The
    production default always validates an owner/repository pair and constructs a
    fixed GitHub HTTPS URL; no webhook field can supply an arbitrary URL.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        temporary_parent: Path | None = None,
        remote_url_factory: Callable[[str], str] = github_repository_url,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self.timeout_seconds = timeout_seconds
        self.temporary_parent = temporary_parent
        self.remote_url_factory = remote_url_factory

    @asynccontextmanager
    async def checkout(
        self,
        repository_full_name: str,
        pull_request_number: int,
        head_sha: str,
    ) -> AsyncIterator[Path]:
        """Yield a temporary checkout whose detached HEAD matches ``head_sha``."""
        # Always validate identity before calling an injected resolver.
        github_repository_url(repository_full_name)
        if (
            isinstance(pull_request_number, bool)
            or not isinstance(pull_request_number, int)
            or pull_request_number < 1
        ):
            raise ValueError("Pull request number must be a positive integer.")
        if not isinstance(head_sha, str) or not _COMMIT_SHA.fullmatch(head_sha):
            raise ValueError("Pull request head revision must be a 40-character commit SHA.")
        expected_origin = github_repository_url(repository_full_name)
        origin = self.remote_url_factory(repository_full_name)
        local_origin = Path(origin).is_dir()
        if not local_origin and origin != expected_origin:
            raise ValueError("Only the constructed GitHub HTTPS remote is supported.")

        parent: Path | None = None
        if self.temporary_parent is not None:
            parent = self.temporary_parent.resolve()
            parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="docdrift-checkout-", dir=parent) as directory:
            checkout_parent = Path(directory)
            repository_path = checkout_parent / "repo"
            git = shutil.which("git")
            if git is None:
                raise RepositoryCheckoutError("Git executable is unavailable.")

            git_config = ["-c", "credential.helper=", "-c", "http.followRedirects=false"]
            if local_origin:
                # Internal test seam: copy a controlled local Git database without
                # invoking file transports or MSYS upload-pack helper processes.
                local_git_dir = Path(origin) / ".git"
                if not local_git_dir.is_dir():
                    raise RepositoryCheckoutError("Local test repository has no Git database.")
                repository_path.mkdir()
                shutil.copytree(local_git_dir, repository_path / ".git")
            else:
                await _run_command(
                    git,
                    [*git_config, "init", "--quiet", str(repository_path)],
                    cwd=checkout_parent,
                    timeout_seconds=self.timeout_seconds,
                )

            async def run(*arguments: str) -> str:
                return await _run_command(
                    git,
                    [
                        *git_config,
                        "-C",
                        str(repository_path),
                        *arguments,
                    ],
                    cwd=repository_path,
                    timeout_seconds=self.timeout_seconds,
                )

            if not local_origin:
                await run("remote", "add", "origin", origin)
                await run(
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    "--depth=1",
                    "origin",
                    f"refs/pull/{pull_request_number}/head",
                )
                pull_request_sha = await run("rev-parse", "FETCH_HEAD^{commit}")
                if pull_request_sha.lower() != head_sha.lower():
                    await run(
                        "fetch", "--quiet", "--no-tags", "--depth=1", "origin", head_sha.lower()
                    )
            await run("checkout", "--quiet", "--force", "--detach", head_sha.lower())
            checked_out_sha = await run("rev-parse", "HEAD^{commit}")
            if checked_out_sha.lower() != head_sha.lower():
                raise RepositoryCheckoutError("Git checked out a different revision than requested.")
            yield repository_path
