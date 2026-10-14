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

For core and CLI development checks, install `.[dev]` from the root with `python -m pip install -e ".[dev]"`. For API/worker development and the full test suite, install `.[api,dev]` as described in [API and worker setup](#api-and-worker-setup). The core and CLI runtime have no third-party dependencies. `docdrift.py` is a source-checkout launcher that works without installation.

## CLI

```powershell
docdrift check --repo .
docdrift check --repo . --docs docs --source packages --format json
python .\docdrift.py check --repo . --docs docs --source packages
```

Repeat `--docs` or `--source` to configure several scopes. Defaults scan the repository root. JSON output has `schema_version`, `findings`, and `errors`; model fields are stable and enum values are lowercase strings.

## API and worker setup

Install the API, worker, and development dependencies from the repository root:

```powershell
python -m pip install -e ".[api,dev]"
```

The API and worker read configuration from environment variables. `DOCDRIFT_API_TITLE` (default `DocDrift API`) and `DOCDRIFT_API_VERSION` (default `0.1.0`) configure API metadata. `DOCDRIFT_GITHUB_WEBHOOK_SECRET`, `DOCDRIFT_DATABASE_URL`, and `DOCDRIFT_REDIS_URL` are required for pull-request intake and processing; app import and `GET /health` do not require GitHub credentials. Use an async PostgreSQL URL such as `postgresql+asyncpg://user:password@localhost:5432/docdrift`; common `postgresql://` URLs are adapted to asyncpg. Redis accepts `redis://` and TLS `rediss://` URLs. Never commit real credentials.

Start a local Redis instance with Docker:

```powershell
docker run --rm --name docdrift-redis -p 6379:6379 redis:7-alpine
```

Set local development configuration in the PowerShell session where you will run the API and worker:

```powershell
$env:DOCDRIFT_GITHUB_WEBHOOK_SECRET = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$env:DOCDRIFT_DATABASE_URL = "postgresql+asyncpg://docdrift:local-password@localhost:5432/docdrift"
$env:DOCDRIFT_REDIS_URL = "redis://localhost:6379/0"
```

Apply the initial schema to a fresh PostgreSQL database with Alembic:

```powershell
alembic -c .\apps\api\alembic.ini upgrade head
```

The schema stores GitHub installations, repositories, PR runs, and webhook delivery metadata. It includes an `awaiting_analysis` run state for the current worker placeholder. A distinct delivery for an existing `(repository, PR number, head SHA)` reuses that run. No raw webhook body, signature, or secret is persisted. When a valid pull-request event omits its installation object, the repository is stored without an installation association until one is known.

Start the local server with:

```powershell
uvicorn docdrift_api.main:app --reload
```

In another terminal with the same environment variables, start the ARQ worker:

```powershell
python -m docdrift_worker.worker
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

Responses are `202` only after the delivery and run are persisted and the job is queued, `200` for an already-recorded delivery or ignored event, `400` for invalid headers or payloads, `401` for an invalid signature, and `503` when required configuration, persistence, or Redis publication is unavailable. If Redis publication fails after persistence, retrying that webhook finds the existing run and safely retries publication. ARQ job IDs use `docdrift:run:<run_id>`; ARQ suppresses duplicate active/recent IDs, and the worker's state check keeps repeated executions safe. ARQ may execute jobs more than once; successful job results are retained for one hour. The worker allows up to three attempts with deferred retries and uses a 30-second timeout.

The worker validates the persisted run and transitions it from `pending` through `running` to `awaiting_analysis`. It does not clone repositories, analyze documentation, or publish GitHub results. The health endpoint reports API health only and does not claim Redis health. API unit tests use mocks and make no external network calls. Optional PostgreSQL integration tests use `DOCDRIFT_TEST_DATABASE_URL`; migrate a dedicated test database before running them.

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
