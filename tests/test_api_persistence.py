from __future__ import annotations

import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from docdrift_api.models import Base, DeliveryStatus, Run, RunStatus

ROOT = Path(__file__).resolve().parents[1]


class PersistenceSchemaTests(unittest.TestCase):
    def test_initial_metadata_has_required_tables_constraints_and_foreign_keys(self) -> None:
        self.assertEqual(
            set(Base.metadata.tables),
            {"installations", "repositories", "runs", "webhook_deliveries"},
        )
        installations = Base.metadata.tables["installations"]
        repositories = Base.metadata.tables["repositories"]
        runs = Base.metadata.tables["runs"]
        deliveries = Base.metadata.tables["webhook_deliveries"]

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

    def test_alembic_has_one_initial_revision_and_async_environment(self) -> None:
        config = Config(str(ROOT / "apps/api/alembic.ini"))
        script = ScriptDirectory.from_config(config)
        revision = script.get_current_head()

        self.assertEqual(revision, "0001_initial")
        self.assertTrue((ROOT / "apps/api/migrations/env.py").is_file())


if __name__ == "__main__":
    unittest.main()
