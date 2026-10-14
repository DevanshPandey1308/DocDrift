from __future__ import annotations

import subprocess
from pathlib import Path


def create_git_repository(path: Path, snapshots: tuple[dict[str, str], ...]) -> list[str]:
    """Create local commits with fixed identity for network-free Git tests."""
    path.mkdir(parents=True)
    _run("git", "init", str(path))
    _run("git", "-C", str(path), "config", "user.name", "DocDrift Tests")
    _run("git", "-C", str(path), "config", "user.email", "docdrift-tests@example.invalid")
    revisions: list[str] = []
    for number, snapshot in enumerate(snapshots):
        for relative, contents in snapshot.items():
            target = path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")
        _run("git", "-C", str(path), "add", "--all")
        _run("git", "-C", str(path), "commit", "--quiet", "-m", f"snapshot {number}")
        revisions.append(_run("git", "-C", str(path), "rev-parse", "HEAD"))
    return revisions


def _run(*arguments: str) -> str:
    result = subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()
