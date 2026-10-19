"""MCP stdio server for local, read-only DocDrift checks."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from .service import MCPService


class EvidenceSummary(BaseModel):
    path: str
    line: int
    snippet: str
    truncated: bool
    redacted: bool


class FindingOutput(BaseModel):
    finding_id: str
    verdict: str
    claim_type: str
    claim: str
    claim_truncated: bool
    document_path: str
    line: int
    explanation: str
    verification_method: str
    severity: str | None
    evidence: list[EvidenceSummary] = Field(default_factory=list)
    evidence_truncated: bool = False


class AnalysisErrorOutput(BaseModel):
    path: str
    line: int | None
    column: int | None
    kind: str
    message: str


class CheckResponse(BaseModel):
    ok: bool
    error_code: str | None
    message: str
    check_id: str | None = None
    finding_count: int | None = None
    true_count: int | None = None
    false_count: int | None = None
    unverifiable_count: int | None = None
    error_count: int | None = None


class FindingsResponse(BaseModel):
    ok: bool
    error_code: str | None
    message: str
    check_id: str | None = None
    total: int | None = None
    offset: int | None = None
    findings: list[FindingOutput] = Field(default_factory=list)
    errors: list[AnalysisErrorOutput] = Field(default_factory=list)
    errors_truncated: bool = False


class EvidenceResponse(BaseModel):
    ok: bool
    error_code: str | None
    message: str
    check_id: str | None = None
    finding_id: str | None = None
    claim: FindingOutput | None = None
    evidence: list[EvidenceSummary] = Field(default_factory=list)
    evidence_truncated: bool = False


def create_server(service: MCPService | None = None) -> MCPServer:
    """Build a server instance; importing it does not require infrastructure credentials."""
    adapter = service or MCPService()
    server = MCPServer("DocDrift")

    @server.tool(name="docdrift_check_repository")
    def docdrift_check_repository(
        repo_path: str,
        docs_dirs: list[str] | None = None,
        source_dirs: list[str] | None = None,
    ) -> CheckResponse:
        """Run deterministic documentation analysis on a local repository."""
        return CheckResponse.model_validate(adapter.check_repository(repo_path, docs_dirs, source_dirs))

    @server.tool(name="docdrift_get_findings")
    def docdrift_get_findings(check_id: str, offset: int = 0, limit: int = 25) -> FindingsResponse:
        """Return one bounded page of structured findings from a prior check."""
        return FindingsResponse.model_validate(adapter.get_findings(check_id, offset, limit))

    @server.tool(name="docdrift_inspect_finding_evidence")
    def docdrift_inspect_finding_evidence(check_id: str, finding_id: str) -> EvidenceResponse:
        """Return bounded repository evidence for one finding from a prior check."""
        return EvidenceResponse.model_validate(adapter.inspect_finding_evidence(check_id, finding_id))

    return server


def main() -> None:
    """Run the DocDrift MCP server over stdio."""
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
