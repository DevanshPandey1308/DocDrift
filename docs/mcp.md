# Local MCP server

DocDrift provides an optional Model Context Protocol (MCP) server over local stdio. It
calls the same deterministic `docdrift_core.analyze` pipeline as the CLI. The server does
not require the GitHub App, PostgreSQL, Redis, or the optional Anthropic SDK, and does not
make provider or remote publication calls.

## Install and launch

From the repository root, install the workspace and MCP extra in the environment used by
your MCP client:

```powershell
python -m pip install -e ".[mcp]"
docdrift-mcp
```

The process speaks MCP on stdin/stdout and is normally launched by the client, rather
than run in an interactive terminal. The package extra selects the official Python MCP
SDK 2.x line. No MCP credentials or environment variables are required.

## Tools

All tool results have a typed MCP output schema, structured JSON data, and a concise
`message` for a human-readable summary. Errors use `ok: false`, a stable `error_code`, and
a safe message.

| Tool | Arguments | Behavior |
|---|---|---|
| `docdrift_check_repository` | `repo_path`; optional `docs_dirs`, `source_dirs` | Runs the full deterministic core scan and returns a short summary and opaque `check_id`. Omitted scopes default to `.`. |
| `docdrift_get_findings` | `check_id`; optional `offset`, `limit` | Returns a page of structured findings with verdict, claim, document line, verification method, up to three short supporting evidence snippets, and available severity. The current core model has no severity field, so severity is `null`. |
| `docdrift_inspect_finding_evidence` | `check_id`, `finding_id` | Returns the selected finding and its bounded evidence snippets with repository-relative paths and line numbers. |

Use the first tool's `check_id` with the other tools. Results are held only in the running
server process, with the most recent eight checks retained; expired or unknown IDs return
`check_not_found`. Finding pages are capped at 50 items. At most 20 detailed evidence items
are returned, with up to three additional short evidence snippets per finding page. Each
claim/explanation is capped at 2,000 characters, page evidence snippets at 400 characters,
and detailed evidence snippets at 1,000 characters. Truncation is marked in the response.
Evidence lines that look like credential assignments or private-key headers are replaced
with a redaction marker. This is a best-effort filter; callers should avoid scanning
repositories containing secrets because no heuristic can identify every secret format.

Repository roots must resolve to accessible directories. Scopes must exist, be relative,
and resolve within the selected root; symlinks found below a scope may not point outside
that root. This validation reduces accidental path escape but is not a complete operating
system filesystem sandbox. The server does not execute repository files or commands.
Repository content is treated as untrusted input. The server is read-only and does not
write check results into the repository.

The current core runs a full scan; it does not analyze a Git working-tree diff or resolve
a `base_ref`. Findings are only the claim types supported by the deterministic core.
`propose_fix` and `find_docs_for_symbol` are not exposed by this adapter.

## MCP client configuration

For clients that accept the common command-based stdio server configuration shape, add
the following entry after installing DocDrift in the client's environment:

```json
{
  "mcpServers": {
    "docdrift": {
      "command": "docdrift-mcp",
      "args": []
    }
  }
}
```

If the client does not inherit the installation environment's `PATH`, set `command` to
the absolute path of that environment's `docdrift-mcp` executable. This configuration is
a generic stdio example; no external MCP client has been certified by this project yet.

## Tests

Install development and MCP extras, then run the MCP adapter tests and existing core/CLI
tests:

```powershell
python -m pip install -e ".[mcp,dev]"
python -m pytest -q tests/test_mcp.py tests/test_core.py tests/test_cli.py
```
