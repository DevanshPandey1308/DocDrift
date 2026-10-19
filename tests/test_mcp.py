from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docdrift_mcp.service import MAX_EVIDENCE_ITEMS, MAX_PAGE_SIZE, MAX_SNIPPET_LENGTH, MCPService

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class MCPServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = MCPService()

    def test_check_and_get_structured_findings_and_evidence(self) -> None:
        before = {
            path.relative_to(FIXTURE).as_posix(): path.read_bytes()
            for path in FIXTURE.rglob("*")
            if path.is_file()
        }
        checked = self.service.check_repository(str(FIXTURE), ["docs"], ["src"])
        self.assertTrue(checked["ok"])
        self.assertGreater(checked["finding_count"], 0)
        page = self.service.get_findings(checked["check_id"])
        self.assertTrue(page["ok"])
        self.assertEqual(page["total"], checked["finding_count"])
        finding = page["findings"][0]
        self.assertEqual(finding["severity"], None)  # Severity is not part of the core model.
        evidence = self.service.inspect_finding_evidence(checked["check_id"], finding["finding_id"])
        self.assertTrue(evidence["ok"])
        self.assertIn("claim", evidence)
        after = {
            path.relative_to(FIXTURE).as_posix(): path.read_bytes()
            for path in FIXTURE.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_rejects_missing_repo_and_unsafe_scopes(self) -> None:
        self.assertEqual(
            self.service.check_repository(str(FIXTURE / "not-here"))["error_code"],
            "invalid_repository",
        )
        self.assertEqual(
            self.service.check_repository(str(FIXTURE), ["../outside"], ["src"])["error_code"],
            "invalid_scope",
        )
        self.assertEqual(
            self.service.check_repository(str(FIXTURE), [str(FIXTURE / "docs")], ["src"])["error_code"],
            "invalid_scope",
        )

    def test_rejects_scope_with_symlink_escaping_repository(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            parent = Path(directory)
            root = parent / "repo"
            docs = root / "docs"
            docs.mkdir(parents=True)
            outside = parent / "outside.md"
            outside.write_text("The default retries is 2.\n", encoding="utf-8")
            try:
                (docs / "linked.md").symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("this environment does not permit creating symlinks")
            result = self.service.check_repository(str(root), ["docs"], ["."])
            self.assertEqual(result["error_code"], "invalid_scope")

    def test_operational_failure_is_predictable_and_does_not_retain_a_check(self) -> None:
        with patch("docdrift_mcp.service.analyze", side_effect=OSError("private details")):
            result = self.service.check_repository(str(FIXTURE))
        self.assertEqual(result["error_code"], "analysis_failed")
        self.assertNotIn("private details", result["message"])
        self.assertEqual(self.service.get_findings("anything")["error_code"], "check_not_found")

    def test_findings_are_paginated_and_limits_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "docs.md").write_text(
                "".join(f"The example is implemented in `missing_{i}.py`.\n" for i in range(120)),
                encoding="utf-8",
            )
            checked = self.service.check_repository(str(root), ["docs.md"], ["."])
            self.assertTrue(checked["ok"])
            first = self.service.get_findings(checked["check_id"], limit=MAX_PAGE_SIZE)
            self.assertEqual(len(first["findings"]), MAX_PAGE_SIZE)
            self.assertEqual(first["total"], 120)
            self.assertEqual(
                self.service.get_findings(checked["check_id"], limit=MAX_PAGE_SIZE + 1)["error_code"],
                "invalid_pagination",
            )

    def test_evidence_output_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "long.py").write_text("#" + "x" * (MAX_SNIPPET_LENGTH + 500), encoding="utf-8")
            (root / "docs.md").write_text("The implementation is in `long.py`.\n", encoding="utf-8")
            checked = self.service.check_repository(str(root), ["docs.md"], ["long.py"])
            page = self.service.get_findings(checked["check_id"])
            evidence = self.service.inspect_finding_evidence(
                checked["check_id"], page["findings"][0]["finding_id"]
            )
            self.assertLessEqual(len(evidence["evidence"][0]["snippet"]), MAX_SNIPPET_LENGTH)
            self.assertTrue(evidence["evidence"][0]["truncated"])
            self.assertLessEqual(len(evidence["evidence"]), MAX_EVIDENCE_ITEMS)

    def test_evidence_redacts_obvious_credential_assignments(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "settings.py").write_text(
                'DOCDRIFT_ANTHROPIC_API_KEY = "must-not-leak"\n', encoding="utf-8"
            )
            (root / "docs.md").write_text("The configuration is stored in `settings.py`.\n", encoding="utf-8")
            checked = self.service.check_repository(str(root), ["docs.md"], ["settings.py"])
            page = self.service.get_findings(checked["check_id"])
            detailed = self.service.inspect_finding_evidence(
                checked["check_id"], page["findings"][0]["finding_id"]
            )
            self.assertTrue(detailed["evidence"][0]["redacted"])
            self.assertNotIn("must-not-leak", str(detailed))
            self.assertNotIn("must-not-leak", str(page))

    def test_check_results_expire_after_bounded_cache_capacity(self) -> None:
        check_ids = [self.service.check_repository(str(FIXTURE))["check_id"] for _ in range(9)]
        self.assertEqual(self.service.get_findings(check_ids[0])["error_code"], "check_not_found")
        self.assertTrue(self.service.get_findings(check_ids[-1])["ok"])


try:
    from docdrift_mcp.server import create_server
except ImportError:
    create_server = None  # type: ignore[assignment]


@unittest.skipIf(create_server is None, "install the optional .[mcp] dependency to test MCP registration")
class MCPRegistrationTests(unittest.TestCase):
    def test_protocol_tools_publish_schemas_and_return_structured_findings(self) -> None:
        async def exercise() -> None:
            from mcp import Client

            server = create_server(MCPService())
            async with Client(server, raise_exceptions=True) as client:
                tools = await client.list_tools()
                by_name = {tool.name: tool for tool in tools.tools}
                self.assertEqual(
                    set(by_name),
                    {
                        "docdrift_check_repository",
                        "docdrift_get_findings",
                        "docdrift_inspect_finding_evidence",
                    },
                )
                self.assertIn("repo_path", by_name["docdrift_check_repository"].input_schema["properties"])
                self.assertIn("findings", by_name["docdrift_get_findings"].output_schema["properties"])
                result = await client.call_tool(
                    "docdrift_check_repository",
                    {"repo_path": str(FIXTURE), "docs_dirs": ["docs"], "source_dirs": ["src"]},
                )
                self.assertFalse(result.is_error)
                self.assertIsInstance(result.structured_content, dict)
                self.assertTrue(result.structured_content["ok"])

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
