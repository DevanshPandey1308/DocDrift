"""Deterministic PR analysis orchestration over the shared core library."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from docdrift_api.models import AnalysisFinding, Run, RunStatus
from docdrift_core.models import Claim, ClaimType, Evidence, Finding, Verdict
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from .checkout import GitHubRepositoryCheckout, RepositoryCheckoutError, changed_python_paths
from .noise import FindingExclusions, RepositoryConfigError, load_finding_exclusions

logger = logging.getLogger(__name__)
CORE_ANALYSIS_TIMEOUT_SECONDS = 25.0


class AnalysisExecutionError(RuntimeError):
    """The deterministic core could not complete a repository scan."""


@dataclass(frozen=True, slots=True)
class RunIdentity:
    run_id: int
    repository_full_name: str
    pull_request_number: int
    head_sha: str
    base_sha: str | None


async def _claim_run(
    session_factory: async_sessionmaker[AsyncSession], run_id: int
) -> tuple[RunIdentity | None, str]:
    """Mark a pending run active and load only its persisted repository revision."""
    async with session_factory() as session, session.begin():
        run = await session.scalar(
            select(Run)
            .options(selectinload(Run.repository))
            .where(Run.id == run_id)
            .with_for_update()
        )
        if run is None:
            return None, "missing"
        if run.status not in (
            RunStatus.PENDING,
            RunStatus.RUNNING,
            RunStatus.AWAITING_ANALYSIS,
        ):
            return None, run.status.value
        if (
            run.repository is None
            or not run.repository.full_name
            or run.pull_request_number < 1
            or not run.head_sha
        ):
            raise AnalysisExecutionError("Persisted run is missing repository revision metadata.")
        if run.status != RunStatus.RUNNING:
            run.status = RunStatus.RUNNING
            await session.flush()
        return (
            RunIdentity(
                run_id=run.id,
                repository_full_name=run.repository.full_name,
                pull_request_number=run.pull_request_number,
                head_sha=run.head_sha,
                base_sha=getattr(run, "base_sha", None),
            ),
            RunStatus.RUNNING.value,
        )


def _finding_record(
    run_id: int, finding: Finding, ignore_reason: str | None = None
) -> AnalysisFinding:
    """Convert the shared core result without changing its verdict or evidence."""
    try:
        expected = json.loads(json.dumps(finding.claim.expected, ensure_ascii=False))
        evidence = [
            {"path": item.path, "line": item.line, "snippet": item.snippet}
            for item in finding.evidence
        ]
    except (TypeError, ValueError) as error:
        raise AnalysisExecutionError("Core analysis produced data that cannot be persisted.") from error

    claim = finding.claim
    return AnalysisFinding(
        run_id=run_id,
        claim_id=claim.id,
        claim_type=claim.type.value,
        subject=claim.subject,
        expected=expected,
        claim_text=claim.text,
        document_path=claim.document_path,
        document_line=claim.line,
        span_start=claim.span_start,
        span_end=claim.span_end,
        verdict=finding.verdict.value,
        explanation=finding.explanation,
        verification_method=finding.verification_method,
        deterministic=finding.deterministic,
        evidence=evidence,
        ignored=ignore_reason is not None,
        ignore_reason=ignore_reason,
    )


def _decode_findings(stdout: bytes) -> list[Finding]:
    """Decode the existing CLI's JSON adapter into the shared core result models."""
    try:
        payload = json.loads(stdout.decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("findings"), list):
            raise TypeError("analysis output is not a findings object")
        if payload.get("errors"):
            raise AnalysisExecutionError(
                f"Deterministic analysis reported {len(payload['errors'])} repository error(s)."
            )
        decoded: list[Finding] = []
        for item in payload["findings"]:
            claim_data = item["claim"]
            claim = Claim(
                id=claim_data["id"],
                type=ClaimType(claim_data["type"]),
                subject=claim_data["subject"],
                expected=claim_data["expected"],
                text=claim_data["text"],
                document_path=claim_data["document_path"],
                line=claim_data["line"],
                span_start=claim_data["span_start"],
                span_end=claim_data["span_end"],
            )
            evidence = tuple(
                Evidence(path=source["path"], line=source["line"], snippet=source["snippet"])
                for source in item["evidence"]
            )
            decoded.append(
                Finding(
                    claim=claim,
                    verdict=Verdict(item["verdict"]),
                    explanation=item["explanation"],
                    evidence=evidence,
                    verification_method=item["verification_method"],
                    deterministic=item["deterministic"],
                )
            )
        return decoded
    except AnalysisExecutionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise AnalysisExecutionError("Deterministic analysis returned invalid output.") from error


async def _analyze_repository(
    repository_root: Path, changed_paths: tuple[str, ...] | None = None
) -> list[Finding]:
    """Run the existing deterministic CLI in a killable, bounded subprocess."""
    try:
        arguments = [
            sys.executable,
            "-I",
            "-m",
            "docdrift_cli.main",
            "check",
            "--repo",
            str(repository_root),
            "--format",
            "json",
        ]
        for changed_path in changed_paths or ():
            arguments.append(f"--changed-path={changed_path}")
        process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=repository_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise AnalysisExecutionError("The deterministic analysis process could not start.") from error

    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=CORE_ANALYSIS_TIMEOUT_SECONDS
        )
    except TimeoutError as error:
        process.kill()
        await process.wait()
        raise TimeoutError("Deterministic analysis exceeded its time limit.") from error
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise

    findings = _decode_findings(stdout)
    if process.returncode not in (0, 1):
        raise AnalysisExecutionError("The deterministic analysis process failed.")
    return findings


async def _persist_findings_and_complete(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: int,
    findings: list[Finding],
    exclusions: FindingExclusions | None = None,
) -> str:
    """Replace results and mark the run complete atomically, only while it is active."""
    records = [
        _finding_record(
            run_id,
            finding,
            (exclusions or FindingExclusions()).reason_for(
                finding.claim.id, finding.claim.document_path
            ),
        )
        for finding in findings
    ]
    async with session_factory() as session, session.begin():
        run = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None:
            return "missing"
        if run.status != RunStatus.RUNNING:
            return run.status.value
        await session.execute(delete(AnalysisFinding).where(AnalysisFinding.run_id == run_id))
        session.add_all(records)
        run.status = RunStatus.COMPLETED
        await session.flush()
    return RunStatus.COMPLETED.value


async def run_analysis_pipeline(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: int,
    *,
    checkout_component: GitHubRepositoryCheckout | None = None,
) -> str:
    """Analyze impacted claims when exact mapping is safe; otherwise keep the full scan."""
    identity, state = await _claim_run(session_factory, run_id)
    if identity is None:
        return state

    logger.info(
        "run_analysis_started",
        extra={"run_id": identity.run_id, "repository": identity.repository_full_name},
    )

    checkout = checkout_component or GitHubRepositoryCheckout()
    try:
        async with checkout.checkout(
            identity.repository_full_name,
            identity.pull_request_number,
            identity.head_sha,
            base_sha=identity.base_sha,
        ) as repository_root:
            root = Path(repository_root)
            changed_paths = await changed_python_paths(
                root, identity.base_sha, identity.head_sha
            )
            findings = await _analyze_repository(root, changed_paths)
            exclusions = load_finding_exclusions(root)
    except RepositoryCheckoutError:
        raise
    except AnalysisExecutionError:
        raise
    except RepositoryConfigError as error:
        raise AnalysisExecutionError(str(error)) from error
    except (OSError, UnicodeError, ValueError) as error:
        raise AnalysisExecutionError("Deterministic analysis could not read the checked-out repository.") from error

    return await _persist_findings_and_complete(session_factory, run_id, findings, exclusions)
