from __future__ import annotations

import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from docdrift_api.models import AnalysisFinding, Base, DeliveryStatus, Run, RunStatus

ROOT = Path(__file__).resolve().parents[1]


class PersistenceSchemaTests(unittest.TestCase):
    def test_initial_metadata_has_required_tables_constraints_and_foreign_keys(self) -> None:
        self.assertEqual(
            set(Base.metadata.tables),
            {"installations", "repositories", "runs", "webhook_deliveries", "analysis_findings"},
        )
        installations = Base.metadata.tables["installations"]
        repositories = Base.metadata.tables["repositories"]
        runs = Base.metadata.tables["runs"]
        deliveries = Base.metadata.tables["webhook_deliveries"]
        findings = Base.metadata.tables["analysis_findings"]

        self.assertIn("uq_installations_github_installation_id", {
            item.name for item in installations.constraints
        })
        self.assertIn("uq_repositories_full_name", {item.name for item in repositories.constraints})
        self.assertIn("uq_repositories_github_repository_id", {
            item.name for item in repositories.constraints
        })
        self.assertIn("uq_runs_pr_head", {item.name for item in runs.constraints})
        self.assertIn("uq_webhook_deliveries_delivery_id", {
            item.name for item in deliveries.constraints
        })
        self.assertIn("uq_analysis_findings_run_claim", {
            item.name for item in findings.constraints
        })
        self.assertEqual(findings.c.evidence.nullable, False)
        self.assertEqual(findings.c.verdict.nullable, False)
        self.assertEqual(runs.c.publication_status.nullable, False)
        self.assertEqual(runs.c.github_check_run_id.nullable, True)
        self.assertEqual(runs.c.base_sha.nullable, True)
        self.assertEqual(findings.c.ignored.nullable, False)
        self.assertEqual(findings.c.ignore_reason.nullable, True)
        self.assertIn("installations.id", {
            next(iter(column.foreign_keys)).target_fullname
            for column in repositories.columns
            if column.foreign_keys
        })
        self.assertIn("repositories.id", {
            next(iter(column.foreign_keys)).target_fullname
            for column in runs.columns
            if column.foreign_keys
        })

    def test_new_run_status_is_pending_and_never_prematurely_completed(self) -> None:
        self.assertEqual(Run.__table__.c.status.default.arg, RunStatus.PENDING)
        self.assertEqual(Run.__table__.c.status.server_default.arg, RunStatus.PENDING.value)
        self.assertIn(RunStatus.COMPLETED, tuple(RunStatus))
        self.assertEqual(DeliveryStatus.ACCEPTED.value, "accepted")

    def test_alembic_has_initial_schema_and_worker_state_revisions(self) -> None:
        config = Config(str(ROOT / "apps/api/alembic.ini"))
        script = ScriptDirectory.from_config(config)
        revision = script.get_current_head()

        self.assertEqual(revision, "0005_impact_and_noise_controls")
        self.assertIn(RunStatus.AWAITING_ANALYSIS, tuple(RunStatus))
        self.assertTrue((ROOT / "apps/api/migrations/env.py").is_file())
        self.assertEqual(AnalysisFinding.__tablename__, "analysis_findings")


if __name__ == "__main__":
    unittest.main()
