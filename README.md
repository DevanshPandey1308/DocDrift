# DocDrift

DocDrift is an evidence-driven detector for a narrow set of documentation claims that can be checked against repository source. The reusable Python core and CLI are deterministic and make no network or model calls. The API worker can optionally use the configured Anthropic provider for grounded prose verification and verified in-diff fix suggestions.

## Current capabilities

- Deterministic extraction of supported claims from Markdown prose and inline code, excluding fenced code blocks.
- Static Python indexing for functions, parameter kinds and literal defaults, common `argparse`, Click, and Typer declarations, FastAPI/Flask route decorators, and literal environment-variable reads.
- Checks for function parameter names, defaults, recognized CLI flags and routes, environment-variable reads, repository-relative file paths, and a narrow Python version metadata form.
- Structured findings with stable verdict strings, document locations, actual source snippets, and syntax/read errors.
- Exact-name impact index for selecting claims linked to changed, statically indexed Python symbols.

Unsupported patterns are skipped by extraction. Recognized but ambiguous patterns abstain as `unverifiable`. A positive environment-variable finding proves only that a recognized read exists; it does not establish that the variable is required or describe runtime behavior.

Route checks support direct, statically constructed FastAPI and Flask apps with literal paths and methods. Router and blueprint declarations abstain when mounting/prefix resolution is uncertain. Click/Typer support is limited to common static command decorators and declarations. Function signatures compare the complete ordered parameter-name list; claims that assert optionality or calling convention abstain unless expressed as a separate supported default claim.

## Install and develop

Requires Python 3.12 or newer. From the repository root, install the two local packages in editable mode:

```powershell
python -m pip install -e .\packages\core -e .\packages\cli
```

For core and CLI development checks, install `.[dev]` from the root with `python -m pip install -e ".[dev]"`. For API/worker development and the full test suite, install `.[api,dev]` as described in [API and worker setup](#api-and-worker-setup). The core uses `markdown-it-py` to parse candidate documents during in-memory fix re-verification; the CLI otherwise has no third-party runtime dependencies. `docdrift.py` is a source-checkout launcher that works without installation.

## CLI

```powershell
docdrift check --repo .
docdrift check --repo . --docs docs --source packages --format json
python .\docdrift.py check --repo . --docs docs --source packages
```

Repeat `--docs` or `--source` to configure several scopes. Defaults scan the repository root. JSON output has `schema_version`, `findings`, and `errors`; model fields are stable and enum values are lowercase strings.

## MCP server

Install the optional MCP integration with `python -m pip install -e ".[mcp]"`, then configure your MCP client to launch `docdrift-mcp` over stdio. It exposes local deterministic repository checks and bounded finding/evidence inspection; it does not require the API infrastructure or Anthropic credentials. See [Local MCP server](docs/mcp.md) for the tool schemas, safety limits, and client configuration example.

## API and worker setup

From the repository root, create a virtual environment if needed and install the API, worker, and development dependencies:

```powershell
if (-not (Test-Path .venv\Scripts\python.exe)) { py -3 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -e ".[api,dev]"
```

The API and worker read configuration from environment variables. `DOCDRIFT_API_TITLE` (default `DocDrift API`) and `DOCDRIFT_API_VERSION` (default `0.1.0`) configure API metadata. `DOCDRIFT_GITHUB_WEBHOOK_SECRET`, `DOCDRIFT_DATABASE_URL`, and `DOCDRIFT_REDIS_URL` are required for pull-request intake and processing; app import and `GET /health` do not require GitHub credentials. Check Run publishing additionally requires `DOCDRIFT_GITHUB_APP_ID` and `DOCDRIFT_GITHUB_APP_PRIVATE_KEY`; the latter accepts PEM text with literal `\n` line breaks or real newlines. Dashboard sign-in requires `DOCDRIFT_GITHUB_OAUTH_CLIENT_ID`, `DOCDRIFT_GITHUB_OAUTH_CLIENT_SECRET`, and `DOCDRIFT_SESSION_ENCRYPTION_KEY` (a Fernet key generated with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`). Set `DOCDRIFT_GITHUB_OAUTH_REDIRECT_URI` to the registered HTTPS callback. For local HTTP development only, set `DOCDRIFT_DASHBOARD_COOKIE_SECURE=false`; production must use HTTPS and secure cookies. Keep all secrets in a secret store or environment, never in source control. Use an async PostgreSQL URL such as `postgresql+asyncpg://user:password@localhost:5432/docdrift`; common `postgresql://` URLs are adapted to asyncpg. Redis accepts `redis://` and TLS `rediss://` URLs.

Grounded Claude verification and candidate-fix proposals are opt-in in the worker. Install the optional SDK with `python -m pip install -e ".[api,llm,dev]"`, then configure the worker environment with `DOCDRIFT_LLM_ENABLED=true`, `ANTHROPIC_API_KEY`, `DOCDRIFT_ANTHROPIC_VERIFICATION_MODEL`, and `DOCDRIFT_ANTHROPIC_FIX_MODEL`. Model identifiers are supplied by the operator; DocDrift does not assume that a sample model name remains available. `DOCDRIFT_LLM_TIMEOUT_SECONDS` defaults to 20 and is bounded at 60 seconds. `DOCDRIFT_LLM_RUN_TOKEN_BUDGET` defaults to 20,000. Missing credentials, model identifiers, SDK, or explicit enablement leaves worker analysis deterministic-only. The SDK uses structured JSON output with no tools. Responses still pass through the existing citation validator, trusted source-evidence gate, edited-region re-verification, delivery planner, and publisher checks. Provider usage reported by Anthropic is summarized on the run; request caching and the token budget are scoped to one worker run. No token-to-currency estimate or monthly cap is provided.

For a disposable local integration environment, first start Docker Desktop and confirm its daemon is ready:

```powershell
docker info
```

There is no Compose configuration in this repository. Start these disposable containers in separate PowerShell windows. They bind only to loopback, have no persistent volumes, and are removed when stopped. The PostgreSQL container uses trust authentication only for this isolated local test instance; do not reuse this configuration for a shared or production service.

```powershell
docker run --rm --name docdrift-postgres -p 127.0.0.1:5432:5432 -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=docdrift -e POSTGRES_DB=docdrift postgres:16
```

```powershell
docker run --rm --name docdrift-redis -p 127.0.0.1:6379:6379 redis:7-alpine
```

In the PowerShell window used for migrations and tests, verify both services, configure the disposable database/queue URLs, and generate a local webhook secret:

```powershell
docker exec docdrift-postgres pg_isready -U docdrift -d docdrift
docker exec docdrift-redis redis-cli ping
$env:DOCDRIFT_DATABASE_URL = "postgresql+asyncpg://docdrift@127.0.0.1:5432/docdrift"
$env:DOCDRIFT_TEST_DATABASE_URL = $env:DOCDRIFT_DATABASE_URL
$env:DOCDRIFT_REDIS_URL = "redis://127.0.0.1:6379/0"
$env:DOCDRIFT_GITHUB_WEBHOOK_SECRET = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
```

Run migrations only against this fresh disposable database (or another database explicitly dedicated to DocDrift testing). The command applies all revisions through `head`; `current` verifies the applied revision:

```powershell
.\.venv\Scripts\alembic.exe -c .\apps\api\alembic.ini upgrade head
.\.venv\Scripts\alembic.exe -c .\apps\api\alembic.ini current
```

The PostgreSQL integration tests use unique test records and clean them up. Run the full test suite, including those tests, with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -rs
```

The current test suite has PostgreSQL integration coverage but no live Redis/ARQ integration test; queue behavior in the unit suite uses mocks. The Redis `PING` above confirms service availability only, not a live queue/worker run.

The schema stores GitHub installations, repositories, PR runs, webhook delivery metadata, and deterministic analysis findings with their claim locations and JSON evidence. `awaiting_analysis` remains as a legacy state; successful deterministic scans now transition runs from `pending` through `running` to `completed`. A distinct delivery for an existing `(repository, PR number, head SHA)` reuses that run. No raw webhook body, signature, or secret is persisted. When a valid pull-request event omits its installation object, the repository is stored without an installation association until one is known.

Start the local server with:

```powershell
.\.venv\Scripts\uvicorn.exe docdrift_api.main:app --reload
```

In another terminal with the same environment variables, start the ARQ worker:

```powershell
.\.venv\Scripts\python.exe -m docdrift_worker.worker
```

Set the same `DOCDRIFT_DATABASE_URL`, `DOCDRIFT_REDIS_URL`, and `DOCDRIFT_GITHUB_WEBHOOK_SECRET` values in each shell that runs the API or worker. For GitHub Check Run and summary publication, also set `DOCDRIFT_GITHUB_APP_ID` and `DOCDRIFT_GITHUB_APP_PRIVATE_KEY` in the worker shell. Those App credentials are not needed for local health checks or the PostgreSQL persistence tests.

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

The worker fetches the persisted 40-character PR head SHA from a URL constructed only for `github.com/{owner}/{repo}.git`, checks out that exact commit detached, and runs the deterministic `docdrift_core` scan. Git operations have bounded timeouts, use a temporary checkout that is removed after processing, disable interactive credential helpers and redirects, and never execute repository code. Only public GitHub repositories are currently supported for checkout; private-repository installation-token authentication for cloning is not implemented.

PR runs retain the webhook's base SHA when available. The worker maps addition-only Python diffs to exact indexed function, default, flag, environment-variable, and route claims. It falls back to the full deterministic scan when the base is unavailable, old lines are deleted or modified, documentation or unsupported files change, source paths are not indexed, or no exact claim mapping is found. This conservative fallback keeps uncertain impact from omitting findings. Findings are replaced transactionally before a run is marked `completed`.

Repositories may add a safe, versioned `.docdrift.yml` to exclude known noise. The supported subset is:

```yaml
version: 1
docs:
  exclude:
    - "docs/changelog.md"
    - "docs/legacy/**"
findings:
  ignore:
    - claim_id: "0123456789abcdef"
      reason: "Generated reference page; reviewed separately."
severity:
  inline_min: breaking
  max_inline_comments: 5
```

`docs.exclude` uses repository-relative glob patterns. `findings.ignore` matches an exact stable claim ID and requires a reason. Markdown also supports the exact standalone comment `<!-- docdrift-ignore -->`: it applies only to claims on the immediately following physical line in that same Markdown file. It does not cross a blank line, apply to a whole section, or suppress other files; no reason is required because the marker itself is recorded as the ignore reason. Other HTML comments and malformed variants have no ignore effect. Marker-ignored findings remain persisted and visible in reporting. Explicit claim-ID ignores take precedence over document patterns, which take precedence over the marker reason. The parser accepts the documented two-space block lists and JSON-style double-quoted flow lists; it rejects other YAML constructs, unknown keys, malformed input, absolute or parent-traversing paths, duplicate keys, and unsupported versions rather than silently disabling configuration. This strict subset has no tags, aliases, or executable YAML features. Excluded findings are retained in persistence with their reason. The Check Run shows them separately, omits their annotations and active verdict counts, and concludes `neutral` when exclusions are the only otherwise-clean result; any remaining false finding still concludes `failure`.

`severity.inline_min` accepts `breaking`, `misleading`, or `cosmetic` (default `breaking`); `severity.max_inline_comments` accepts an integer from 0 through 50 (default `5`). Current deterministic finding types map verified false function signature, CLI flag, environment variable, route, file path, and Python version claims to `breaking`, and default-value mismatches to `misleading`. Unverifiable and ignored findings are never classified as active failures. Findings are ordered by severity, count of distinct valid source evidence records, then stable document location. There is no confidence value in the current finding model, so evidence count is a deterministic ranking proxy rather than a calibrated probability.

For GitHub reporting, create a GitHub App and install it on each target repository. Grant the app `Checks: Read and write` and `Pull requests: Read and write` (the publisher requests only `checks: write` or `pull_requests: write` on the corresponding short-lived installation token); GitHub's default `Metadata: Read-only` permission is also required. Pull request write access is used for the PR issue-comment endpoints that create and edit the summary and inline review comments; read access is used to verify that the PR is still open at the exact analyzed base and head. The webhook's installation ID and repository ID scope each token to that repository. Set `DOCDRIFT_GITHUB_APP_ID` and `DOCDRIFT_GITHUB_APP_PRIVATE_KEY` in the worker environment. Without this configuration, analysis still completes and persists normally, while the separate run publication status becomes `failed` with a safe configuration diagnostic. No private key or token is logged or stored.

Each completed run creates or updates a Check Run for its exact analyzed head SHA, using a stable run-and-SHA external ID to find/reuse the same Check Run on retries. It also creates or edits one PR summary comment marked `<!-- docdrift-summary:v1 -->`; lookup is scoped to the repository and PR, and a PostgreSQL advisory lock serializes concurrent updates. An older run is skipped if a newer head is already persisted. Before retrying annotation batches, the worker reads the Check Run's existing annotations and sends only missing ones. Active false findings conclude `failure`; active unverifiable findings or exclusions with no active false findings conclude `neutral`; a clean scan concludes `success`. False findings with safe repository-relative documentation paths and valid line numbers receive annotations, sent in batches of at most 50; ignored findings do not receive annotations and remain visible in both summaries. Publishing has a separate persisted status and bounded retry handling, so GitHub failures do not change a completed analysis to failed.

The summary groups findings by severity and verdict and retains findings that exceed any inline budget. Inline review comments are published only for active, deterministic `false` findings with valid source evidence whose exact Markdown claim line was added in the pinned base-to-head diff. A verified suggestion may be included only after full edited-region verification and planner approval. Changed or added lines qualify; unchanged context lines and deleted lines do not. The worker parses a no-renames unified diff and stores a line anchor only when it is unambiguous; GitHub receives the changed file path, new-side line, `RIGHT` side, and analyzed head commit. Immediately before publication, the worker checks that the PR remains open and that both its head and base still match the analyzed run. `severity.inline_min` and `severity.max_inline_comments` bound publication; ignored, low-ranked, over-budget, unchanged-line, unsupported, or unmappable findings stay in the Check Run and summary. Rejected review positions are skipped with the same fallbacks. Stable hidden identities let retries update an existing DocDrift review comment rather than duplicate it. Successfully published verified suggestions are now recorded with a stable patch fingerprint and GitHub comment ID. The health endpoint reports API health only and does not claim GitHub or Redis health. Unit tests use local Git repositories and mocked GitHub responses without external GitHub requests. Optional PostgreSQL integration tests use `DOCDRIFT_TEST_DATABASE_URL`; migrate a dedicated test database before running them.

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

Current limitations include a limited Markdown grammar, top-level/module-name ambiguity for same-named functions, literal-only static resolution, `argparse`-focused CLI recognition, and no general Python version range parser. Impact selection currently uses exact static names and narrows only addition-only Python diffs; other changes use a full scan. The `.docdrift.yml` support is intentionally limited to document path exclusions and exact claim-ID ignores. Click and Typer declarations, overloaded/dynamic semantics, and arbitrary prose are not supported. LLM verification remains subject to bounded indexed evidence and strict citation/sufficiency checks. Fix suggestions are only possible when every claim in the edited region verifies true and the existing in-diff publisher guards pass. No benchmark performance claim has been made.

Future milestones may add broader checker coverage, changed-symbol selection using Git diffs, a reproducible benchmark, and hosted adapters. They are not implemented in this phase.

### Feedback capture status

Migrations `0008_feedback_events` and `0009_dashboard_auth_suggestions` store explicit `fix_accepted`, `fix_rejected`, and
`false_positive` events with finding/run/repository integrity constraints and idempotency
keys, GitHub OAuth sessions, and exact successfully published suggestion identities. Apply them to a dedicated DocDrift database with
`.\.venv\Scripts\alembic.exe -c .\apps\api\alembic.ini upgrade head`. Internal persistence
and retrieval routes are available after GitHub OAuth sign-in: `GET /auth/github/login`,
`POST /suggestions/{id}/feedback`, `POST /findings/{id}/feedback`, `GET /runs/{id}/feedback`,
`GET /findings/{id}/feedback`, and `GET /repositories/{id}/feedback/metrics`. Feedback POSTs
require the `docdrift_csrf` cookie value in `X-CSRF-Token` and a JSON body such as
`{"category":"fix_accepted","idempotency_key":"review-123","reason":"Applied"}`.
The session cookie is HttpOnly; OAuth access tokens are encrypted at rest and repository
access is rechecked against GitHub for each request. GitHub events still do not reliably prove
per-suggestion acceptance. Acceptance rate is accepted fixes / (accepted + rejected fixes);
false-positive and unknown outcomes are excluded, and a repository with no such outcomes has
an unknown (`null`) rate. See
[`docs/architecture.md`](docs/architecture.md#explicit-feedback-persistence) for the trust
boundaries and dashboard prerequisites.

For local OAuth, register a GitHub OAuth App with callback `http://localhost:8000/auth/github/callback`,
set the three OAuth/encryption variables above, and set `DOCDRIFT_DASHBOARD_COOKIE_SECURE=false`
for localhost HTTP. In production, register the exact HTTPS callback, keep secure cookies enabled,
and use a dedicated encryption key from the deployment secret store. OAuth scopes request `repo`
and `read:org`; repository access is checked live against the signed-in user's OAuth token and the
DocDrift installation record before each feedback read or write.
