from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise TypeError(f"{path.name} must contain a YAML mapping")
    return value


class InfrastructureFileTests(unittest.TestCase):
    def test_compose_and_ci_are_valid_yaml(self) -> None:
        compose = _load_yaml(ROOT / "docker-compose.yml")
        workflow = _load_yaml(ROOT / ".github/workflows/ci.yml")

        self.assertEqual(set(compose["services"]), {"postgres", "redis", "migrate", "api", "worker"})
        self.assertIn("jobs", workflow)
        self.assertIn("permissions", workflow)

    def test_compose_services_have_required_health_and_startup_order(self) -> None:
        services = _load_yaml(ROOT / "docker-compose.yml")["services"]
        for name in ("postgres", "redis", "api"):
            self.assertIn("healthcheck", services[name])
        self.assertEqual(services["postgres"]["image"], "postgres:16")
        self.assertEqual(services["redis"]["image"], "redis:7")
        self.assertIn("pg_isready", " ".join(services["postgres"]["healthcheck"]["test"]))
        self.assertIn("redis-cli", " ".join(services["redis"]["healthcheck"]["test"]))
        self.assertIn("/health", " ".join(services["api"]["healthcheck"]["test"]))
        self.assertEqual(
            services["migrate"]["depends_on"]["postgres"]["condition"], "service_healthy"
        )
        for name in ("api", "worker"):
            self.assertEqual(
                services[name]["depends_on"]["migrate"]["condition"],
                "service_completed_successfully",
            )
        self.assertEqual(
            services["worker"]["depends_on"]["redis"]["condition"], "service_healthy"
        )
        self.assertIn("postgres-data:/var/lib/postgresql/data", services["postgres"]["volumes"])
        self.assertEqual(
            services["migrate"]["command"],
            ["python", "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"],
        )
        self.assertEqual(services["api"]["command"][0:2], ["uvicorn", "docdrift_api.main:app"])
        self.assertEqual(services["worker"]["command"], ["python", "-m", "docdrift_worker.worker"])
        for name in ("migrate", "api", "worker"):
            self.assertEqual(services[name]["env_file"], ".env")
        for name in ("api", "worker"):
            self.assertEqual(
                services[name]["volumes"],
                ["./secrets/github-app.pem:/run/secrets/github-app.pem:ro"],
            )
            self.assertEqual(
                services[name]["environment"]["DOCDRIFT_GITHUB_APP_PRIVATE_KEY_PATH"],
                "/run/secrets/github-app.pem",
            )

    def test_secrets_are_not_committed_to_infrastructure_files(self) -> None:
        paths = (ROOT / "docker-compose.yml", ROOT / ".github/workflows/ci.yml")
        forbidden = re.compile(r"BEGIN RSA|ghp_|sk-ant", re.IGNORECASE)
        for path in paths:
            self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")))

    def test_dockerfile_installs_git_and_uses_non_root_user(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8").replace("\\\n", " ")
        self.assertRegex(dockerfile, r"apt-get install .*git")
        self.assertRegex(dockerfile, r"(?m)^USER\s+docdrift\s*$")
        self.assertIn(".[api,llm]", dockerfile)

    def test_environment_example_matches_application_configuration(self) -> None:
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        configured = set(re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", example))
        code_text = "\n".join(
            path.read_text(encoding="utf-8")
            for root in (ROOT / "apps", ROOT / "packages")
            for path in root.rglob("*.py")
        )
        for variable in configured:
            with self.subTest(variable=variable):
                self.assertRegex(code_text, rf'os\.getenv\(\s*["\']{re.escape(variable)}["\']')

        config = (ROOT / "apps/api/src/docdrift_api/config.py").read_text(encoding="utf-8")
        read_variables = {
            variable
            for variable in re.findall(r'os\.getenv\("([A-Z][A-Z0-9_]*)"', config)
            if variable.startswith("DOCDRIFT_")
        }
        self.assertLessEqual(read_variables, configured)


if __name__ == "__main__":
    unittest.main()
