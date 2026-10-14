# DocDrift

DocDrift is a local, evidence-driven detector for a narrow set of documentation claims that can be checked against repository source. Phase 1 provides a reusable Python core and a CLI; it never imports scanned code and makes no network or model calls.

## Current capabilities

- Deterministic extraction of supported claims from Markdown prose and inline code, excluding fenced code blocks.
- Static Python indexing for functions, parameter kinds and literal defaults, common `argparse`, Click, and Typer declarations, FastAPI/Flask route decorators, and literal environment-variable reads.
- Checks for function parameter names, defaults, recognized CLI flags and routes, environment-variable reads, repository-relative file paths, and a narrow Python version metadata form.
- Structured findings with stable verdict strings, document locations, actual source snippets, and syntax/read errors.
- Exact-name impact index available to later incremental-analysis adapters.

Unsupported patterns are skipped by extraction. Recognized but ambiguous patterns abstain as `unverifiable`. A positive environment-variable finding proves only that a recognized read exists; it does not establish that the variable is required or describe runtime behavior.

Route checks support direct, statically constructed FastAPI and Flask apps with literal paths and methods. Router and blueprint declarations abstain when mounting/prefix resolution is uncertain. Click/Typer support is limited to common static command decorators and declarations. Function signatures compare the complete ordered parameter-name list; claims that assert optionality or calling convention abstain unless expressed as a separate supported default claim.

## Install and develop

Requires Python 3.12 or newer. From the repository root, install the two local packages in editable mode:

```powershell
python -m pip install -e .\packages\core -e .\packages\cli
```

For core and CLI development checks, install `.[dev]` from the root with `python -m pip install -e ".[dev]"`. For API development and the full test suite, install `.[api,dev]` as described in [API foundation](#api-foundation). The core and CLI runtime have no third-party dependencies. `docdrift.py` is a source-checkout launcher that works without installation.

## CLI

```powershell
docdrift check --repo .
docdrift check --repo . --docs docs --source packages --format json
python .\docdrift.py check --repo . --docs docs --source packages
```

Repeat `--docs` or `--source` to configure several scopes. Defaults scan the repository root. JSON output has `schema_version`, `findings`, and `errors`; model fields are stable and enum values are lowercase strings.

## API foundation

Install the API and development dependencies from the repository root:

```powershell
python -m pip install -e ".[api,dev]"
```

The API supports `DOCDRIFT_API_TITLE` (default `DocDrift API`), `DOCDRIFT_API_VERSION` (default `0.1.0`), and `DOCDRIFT_GITHUB_WEBHOOK_SECRET` (unset by default). Set a random, process-local development secret before starting the server; this PowerShell command does not write it to the repository:

```powershell
$env:DOCDRIFT_GITHUB_WEBHOOK_SECRET = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
```

Start the local server with:

```powershell
uvicorn docdrift_api.main:app --reload
```

`GET /health` returns JSON with `status`, `service`, and `version` and does not require a webhook secret. Check it while the server is running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

`POST /webhooks/github` requires `X-Hub-Signature-256`, `X-GitHub-Event`, and `X-GitHub-Delivery`. Signatures use GitHub's `sha256=<hex HMAC-SHA256>` format over the exact request bytes. Only `pull_request` events are accepted for validation; other validly signed event types return `200` with `status: "ignored"`.

To send a correctly signed local request from PowerShell while the server is running:

```powershell
$body = [Text.Encoding]::UTF8.GetBytes('{"action":"opened","pull_request":{}}')
$hmac = [Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($env:DOCDRIFT_GITHUB_WEBHOOK_SECRET))
$signature = 'sha256=' + [Convert]::ToHexString($hmac.ComputeHash($body)).ToLowerInvariant()
$headers = @{
  'X-Hub-Signature-256' = $signature
  'X-GitHub-Event' = 'pull_request'
  'X-GitHub-Delivery' = [guid]::NewGuid().ToString()
}
Invoke-RestMethod -Uri http://127.0.0.1:8000/webhooks/github -Method Post -Headers $headers -ContentType 'application/json' -Body $body
$hmac.Dispose()
```

Responses are `200` for validated pull requests or ignored event types, `400` for missing event/delivery headers or invalid delivery IDs and payloads, `401` for missing or invalid signatures, and `503` when the server secret is not configured. Errors use `{"error":{"code":"...","message":"..."}}`. A validated response means signature and JSON validation only; no persistence, duplicate-delivery detection, queueing, analysis, or GitHub API response exists yet. The validated delivery ID is returned for a future idempotency layer. API tests use FastAPI's in-process test client and make no external network calls.

Example text finding:

```text
FALSE docs/guide.md:5 [default_value] The default retries is 3.
  Documented default 3 differs from source default 2.
  source src/api.py:5: def create_user(name, email, timeout=30, *, retries=2):
```

## Grammar, verdicts, and exit codes

The supported claim grammar is intentionally small; see [docs/supported-claims.md](docs/supported-claims.md). `true` means static evidence supports the claim, `false` means sufficient evidence contradicts it, and `unverifiable` means the engine cannot safely decide. Unverifiable is not reported as a confirmed defect.

- `0`: no verified drift and no source/configuration errors (including scans with only unverifiable claims).
- `1`: at least one supported claim has a `false` verdict.
- `2`: invalid repository/scope or source analysis errors.

Syntax errors are included in JSON and printed to stderr in text mode. They produce exit code 2, even when findings are also returned.

## Tests and quality checks

Run the full test suite after installing `.[api,dev]` with:

```powershell
$env:PYTHONPATH = "apps/api/src;packages/core/src;packages/cli/src"
python -m unittest discover -s tests -v
```

Optional development tools are pytest, Ruff, and mypy. Their configuration is in `pyproject.toml`; install them with the `dev` extra before running `pytest`, `ruff check .`, or `mypy packages`.

## Architecture and limitations

The dependency direction is CLI adapter → core. The core has no CLI, web, GitHub, or infrastructure dependencies. Source indexing uses `ast.parse` and safe literal parsing only. Paths are resolved within the repository root. No source code or documentation examples are executed.

Current limitations include a limited Markdown grammar, top-level/module-name ambiguity for same-named functions, literal-only static resolution, `argparse`-focused CLI recognition, and no general Python version range parser. Click and Typer declarations, overloaded/dynamic semantics, arbitrary prose, Git diffs, and automatic fixes are not yet supported. No benchmark performance claim has been made.

Future milestones may add broader checker coverage, changed-symbol selection using Git diffs, a reproducible benchmark, and hosted adapters. They are not implemented in this phase.
