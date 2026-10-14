from __future__ import annotations

import os
import unittest
import uuid

from docdrift_api.database import normalize_async_database_url
from docdrift_api.models import (
    AnalysisFinding,
    Installation,
    Repository,
    Run,
    RunStatus,
    WebhookDelivery,
)
from docdrift_api.persistence import PullRequestRecord, persist_pull_request
from docdrift_core.models import Claim, ClaimType, Evidence, Finding, Verdict
from docdrift_worker.pipeline import _persist_findings_and_complete
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DATABASE_URL = os.getenv("DOCDRIFT_TEST_DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "DOCDRIFT_TEST_DATABASE_URL is not configured")
class PostgreSQLPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        assert DATABASE_URL is not None
        self.engine = create_async_engine(normalize_async_database_url(DATABASE_URL))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        suffix = uuid.uuid4().hex
        self.delivery_ids = [f"test-{suffix}-{i}" for i in range(3)]
        self.repository_name = f"docdrift-tests/{suffix}"
        self.repository_id = int(suffix[:10], 16)
        self.installation_id = int(suffix[10:20], 16)

    async def asyncTearDown(self) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                delete(WebhookDelivery).where(WebhookDelivery.delivery_id.in_(self.delivery_ids))
            )
            await session.execute(
                delete(Repository).where(Repository.full_name == self.repository_name)
            )
            await session.execute(
                delete(Installation).where(
                    Installation.github_installation_id == self.installation_id
                )
            )
        await self.engine.dispose()

    async def test_delivery_and_logical_run_idempotency(self) -> None:
        record = PullRequestRecord(
            repository_full_name=self.repository_name,
            github_repository_id=self.repository_id,
            installation_id=self.installation_id,
            pull_request_number=1,
            head_sha="a" * 40,
        )
        first = await persist_pull_request(
            self.sessions, delivery_id=self.delivery_ids[0], pull_request=record
        )
        repeated_delivery = await persist_pull_request(
            self.sessions, delivery_id=self.delivery_ids[0], pull_request=record
        )
        new_delivery_same_head = await persist_pull_request(
            self.sessions, delivery_id=self.delivery_ids[1], pull_request=record
        )
        new_head = await persist_pull_request(
            self.sessions,
            delivery_id=self.delivery_ids[2],
            pull_request=PullRequestRecord(
                repository_full_name=self.repository_name,
                github_repository_id=self.repository_id,
                installation_id=self.installation_id,
                pull_request_number=1,
                head_sha="b" * 40,
            ),
        )

        self.assertFalse(first.duplicate_delivery)
        self.assertIsNotNone(first.run_id)
        self.assertTrue(repeated_delivery.duplicate_delivery)
        self.assertEqual(repeated_delivery.run_id, first.run_id)
        self.assertFalse(new_delivery_same_head.duplicate_delivery)
        self.assertFalse(new_head.duplicate_delivery)
        async with self.sessions() as session:
            deliveries = await session.scalar(
                select(func.count()).select_from(WebhookDelivery).where(
                    WebhookDelivery.delivery_id.in_(self.delivery_ids)
                )
            )
            repository_id = await session.scalar(
                select(Repository.id).where(Repository.full_name == self.repository_name)
            )
            runs = await session.scalar(
                select(func.count()).select_from(Run).where(Run.repository_id == repository_id)
            )
        self.assertEqual(deliveries, 3)
        self.assertEqual(runs, 2)

    async def test_failed_repository_identity_conflict_rolls_back_delivery(self) -> None:
        first = PullRequestRecord(
            repository_full_name=self.repository_name,
            github_repository_id=self.repository_id,
            installation_id=self.installation_id,
            pull_request_number=1,
            head_sha="a" * 40,
        )
        await persist_pull_request(self.sessions, delivery_id=self.delivery_ids[0], pull_request=first)

        conflicting = PullRequestRecord(
            repository_full_name=self.repository_name,
            github_repository_id=self.repository_id + 1,
            installation_id=self.installation_id,
            pull_request_number=1,
            head_sha="b" * 40,
        )
        with self.assertRaises(RuntimeError):
            await persist_pull_request(
                self.sessions, delivery_id=self.delivery_ids[1], pull_request=conflicting
            )

        async with self.sessions() as session:
            stored_delivery = await session.scalar(
                select(WebhookDelivery.id).where(
                    WebhookDelivery.delivery_id == self.delivery_ids[1]
                )
            )
            repository_id = await session.scalar(
                select(Repository.id).where(Repository.full_name == self.repository_name)
            )
            run_count = await session.scalar(
                select(func.count()).select_from(Run).where(Run.repository_id == repository_id)
            )
        self.assertIsNone(stored_delivery)
        self.assertEqual(run_count, 1)

    async def test_findings_and_evidence_persist_once_with_completed_state(self) -> None:
        created = await persist_pull_request(
            self.sessions,
            delivery_id=self.delivery_ids[0],
            pull_request=PullRequestRecord(
                repository_full_name=self.repository_name,
                github_repository_id=self.repository_id,
                installation_id=self.installation_id,
                pull_request_number=1,
                head_sha="c" * 40,
            ),
        )
        assert created.run_id is not None
        finding = Finding(
            Claim(
                id="claim-test-1",
                type=ClaimType.DEFAULT,
                subject="timeout",
                expected=30,
                text="The default timeout is 30.",
                document_path="README.md",
                line=2,
                span_start=1,
                span_end=30,
            ),
            Verdict.FALSE,
            "Documented default does not match source.",
            (Evidence("src/api.py", 1, "def connect(timeout=60):"),),
            "static_python",
        )

        async with self.sessions() as session, session.begin():
            run = await session.scalar(select(Run).where(Run.id == created.run_id))
            assert run is not None
            run.status = RunStatus.RUNNING

        state = await _persist_findings_and_complete(self.sessions, created.run_id, [finding])
        self.assertEqual(state, RunStatus.COMPLETED.value)

        async with self.sessions() as session:
            stored = await session.scalar(
                select(AnalysisFinding).where(
                    AnalysisFinding.run_id == created.run_id,
                    AnalysisFinding.claim_id == "claim-test-1",
                )
            )
            run = await session.scalar(select(Run).where(Run.id == created.run_id))
            count = await session.scalar(
                select(func.count()).select_from(AnalysisFinding).where(
                    AnalysisFinding.run_id == created.run_id
                )
            )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.verdict, Verdict.FALSE.value)
        self.assertEqual(stored.document_path, "README.md")
        self.assertEqual(stored.evidence, [
            {"path": "src/api.py", "line": 1, "snippet": "def connect(timeout=60):"}
        ])
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(count, 1)

        # A run re-entering RUNNING after an interrupted attempt replaces prior rows.
        async with self.sessions() as session, session.begin():
            run = await session.scalar(select(Run).where(Run.id == created.run_id))
            assert run is not None
            run.status = RunStatus.RUNNING
        state = await _persist_findings_and_complete(self.sessions, created.run_id, [finding])
        self.assertEqual(state, RunStatus.COMPLETED.value)
        async with self.sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(AnalysisFinding).where(
                    AnalysisFinding.run_id == created.run_id
                )
            )
        self.assertEqual(count, 1)

if __name__ == "__main__":
    unittest.main()
