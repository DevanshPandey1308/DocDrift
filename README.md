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

The API supports `DOCDRIFT_API_TITLE` (default `DocDrift API`), `DOCDRIFT_API_VERSION` (default `0.1.0`), `DOCDRIFT_GITHUB_WEBHOOK_SECRET` (unset by default), and `DOCDRIFT_DATABASE_URL` (unset by default). Configure the webhook secret and PostgreSQL URL for pull-request intake. The health endpoint and app import do not require either setting. For local development, use an async PostgreSQL URL such as `postgresql+asyncpg://user:password@localhost:5432/docdrift`; common `postgresql://` URLs are also adapted to the asyncpg driver. Never commit real credentials. Set a random, process-local webhook secret before starting the server:

```powershell
$env:DOCDRIFT_GITHUB_WEBHOOK_SECRET = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$env:DOCDRIFT_DATABASE_URL = "postgresql+asyncpg://docdrift:local-password@localhost:5432/docdrift"
```

Apply the initial schema to a fresh PostgreSQL database with Alembic:

```powershell
alembic -c .\apps\api\alembic.ini upgrade head
```

The initial schema stores GitHub installations, repositories, PR runs, and webhook delivery metadata. PR runs start with status `pending`; this milestone does not enqueue or analyze them. Delivery IDs are unique, so a repeated delivery returns `duplicate`; a distinct delivery for an already-recorded `(repository, PR number, head SHA)` is accepted without creating a second run. No raw webhook body, signature, or secret is persisted. When a valid pull-request event omits its installation object, the repository is stored without an installation association until one is known.

Start the local server with:

```powershell
uvicorn docdrift_api.main:app --reload
```

`GET /health` returns JSON with `status`, `service`, and `version` and does not require a webhook secret. Check it while the server is running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

`POST /webhooks/github` requires `X-Hub-Signature-256`, `X-GitHub-Event`, and `X-GitHub-Delivery`. Signatures use GitHub's `sha256=<hex HMAC-SHA256>` format over the exact request bytes. Only `pull_request` events are persisted; other validly signed event types return `200` with `status: "ignored"`.

To send a correctly signed local request from PowerShell while the server is running:

```powershell
$body = [Text.Encoding]::UTF8.GetBytes('{"action":"opened","repository":{"id":42,"full_name":"owner/project"},"pull_request":{"number":3,"head":{"sha":"abc123"}}}')
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

Responses are `202` after a pull-request delivery and its initial run persist successfully, `200` for duplicate deliveries or ignored event types, `400` for invalid headers or payloads, `401` for missing or invalid signatures, and `503` when webhook or database configuration is unavailable or persistence fails. Errors use `{"error":{"code":"...","message":"..."}}`. A successful response confirms durable intake only: no job is queued, no analysis runs, and no GitHub API response is published. API unit tests use FastAPI's in-process test client and make no external network calls. Optional PostgreSQL integration tests use `DOCDRIFT_TEST_DATABASE_URL`; migrate a dedicated test database before running them.

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
