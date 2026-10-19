# Architecture

DocDrift is split into `docdrift-core` and thin adapters including the CLI and optional local MCP server. The core owns stable domain models, Markdown extraction, a static source index, checker registry, and the reusable analysis pipeline. Adapters parse input and render core results; they do not implement verification rules.

The pipeline discovers Markdown within explicit scopes, extracts supported claims, parses Python files using `ast.parse` without importing them, builds a source index for symbols, CLI flags, and routes, then dispatches each claim through a checker registry. Findings preserve original document text and stable IDs. Evidence snippets are read from the actual repository file at the recorded line. Source parse errors are separate structured errors.

The exact-name inverted index maps claim subjects to claim IDs for future changed-symbol selection. The initial command performs full scans. Future adapters can consume the core models without depending on CLI presentation.

`docdrift_mcp` is an optional stdio adapter over `docdrift_core.analyze`. It exposes a
full local scan, paginated structured findings, and bounded evidence lookup. Check
snapshots are held in a small in-memory cache for the server process; this adapter does
not use GitHub, persistence, Redis, or the optional LLM provider. Repository scope
validation rejects explicit traversal and symlinks resolving outside the selected root,
but it is not an operating-system filesystem sandbox. See [docs/mcp.md](mcp.md) for
installation, tool schemas, output bounds, and the generic client configuration example.

## Grounded verification contract

`docdrift_core.verification` defines provider-neutral input snippets, structured
`true`/`false`/`abstain` results, and exact citation validation. Each evidence snippet
keeps a normalized repository-relative POSIX path and one-based starting line; its opaque
reference is a stable hash of that location and exact text. A result must be a decoded
object with the exact `verdict`, `evidence_refs`, and `rationale` fields. Each citation
contains only a supplied reference and a verbatim quote; model-provided file paths and line
numbers are not accepted.

Citation matching establishes that quoted text came from the current evidence set, but it
does not establish that the text entails the claim. For that reason a `true` or `false`
result is accepted only when a trusted deterministic evidence-sufficiency gate is supplied
and approves the cited snippets. Missing, failing, or negative gates and all malformed or
unmatched output become `abstain`. The worker's gate additionally confirms that cited
snippets are exact current lines from the static source index and contain the claim subject.

`docdrift_core.llm_client` adds an asynchronous `LLMProvider` protocol and a thin client
that accepts only a `VerificationRequest` built from the existing claim and evidence
snippet types. Requests are bounded to 16 snippets, 4,000 characters per snippet and
16,000 evidence characters total. The caller supplies a timeout greater than zero and no
more than 60 seconds, plus the trusted evidence-sufficiency gate. Provider responses may
be a JSON object or JSON text; duplicate JSON keys, malformed output, provider errors and
timeouts all abstain without exposing provider exception details. Cancellation propagates.
The optional worker adapter uses Anthropic's official async SDK and structured JSON output;
the SDK is an optional `llm` dependency. It has no tools or repository access. Enablement,
API key, and model identifiers are explicit environment settings. With the feature disabled
or incompletely configured, analysis remains deterministic-only.

`docdrift_core.orchestration.orchestrate_verification` is an explicit opt-in adapter
for callers that have a deterministic `unverifiable` finding and already-selected
repository evidence. It preserves deterministic `true` and `false` findings without
calling the client. Only function, default-value, CLI-flag, environment-variable,
Python-version, and route claims are eligible for LLM fallback; file-path claims remain
with the deterministic repository-path checker. Requests are bounded by the client
limits, and the function does not read files or give the provider tools. A client result
is accepted only when it is the existing `VerificationResult` contract and its citations
resolve to exact supplied snippets; true/false results must come from a `GroundedLLMClient`
that applied its required trusted evidence-sufficiency gate. Accepted findings record
`grounded_llm` and `deterministic=False`; client failures, malformed results, and invalid
citations become `unverifiable`. The worker routes only eligible deterministic
`unverifiable` findings through this function; deterministic `true` and `false` results
remain authoritative.

Each worker invocation creates a run-scoped serialized call/cache boundary. Its semantic
cache key includes claim, evidence, configured model, prompt version, and response schema.
The configured per-run token budget reserves a conservative UTF-8 request-size upper bound
plus the fixed output-token cap before each provider operation, then tracks actual
provider-reported input/output tokens after responses.
Usage summaries (calls, failures, token counts, and configured budget) are persisted on the
run by migration `0007_llm_usage`. The cache is in memory for that run and survives ARQ
retries handled by the same worker process, but is not shared across worker restarts. No
currency costs or monthly repository limits are estimated.

Repository content is untrusted data. The engine does not execute code, use `eval`/`exec`, retrieve secrets or environment values, or make network calls. File path claims are resolved and constrained to the repository root. Route and CLI recognizers use AST shape and literal values; they do not import FastAPI, Flask, Click, or Typer. Router mounting and prefixes are not fully resolved, so uncertain route claims abstain.

## Guarded documentation patches

`docdrift_core.patches` constructs immutable in-memory span patches. Callers can target
explicit zero-based character offsets, use a `Claim`'s one-based line and line-local
span, or request an exact-text target only when it occurs once. Every path verifies the
expected original text and rejects stale or ambiguous targets. The default guard limits
the larger of removed/inserted span lengths to 1,000 characters and the larger physical
line count to 5; callers can provide smaller or larger positive limits. Patch results
retain both full document versions and the affected span. No patch function writes files.

`docdrift_core.fix_verification.reverify_candidate_patch` uses `markdown-it-py` to parse
the candidate document, applies the edit only to an in-memory string, and re-extracts all
supported claims on the physical lines touched by the replacement. It requires the
intended claim to be located uniquely and verifies every extracted claim in that region
with the existing deterministic-first checker and, when configured and eligible, the
existing grounded verification orchestration. The structured result reports parser
status, extraction coverage, and each claim's finding separately. A candidate is
verified only when parsing and extraction succeed and every required claim is accepted
as `true`. CommonMark is permissive, so parser acceptance does not prove stylistic
validity or detect every malformed-looking construct; extraction covers only patterns
supported by DocDrift. It never writes the document or publishes a fix.

## Pull request reporting

The worker stores a review-comment line only when the claim's exact Markdown line appears as an added line in the no-renames unified diff between the run's pinned base and head. This covers additions and the new side of modifications; unchanged context, deletions, malformed diffs, and unsupported paths have no inline anchor and remain in the Check Run and summary. Publication rechecks the open pull request's base and head before sending plain `RIGHT`-side comments. It enforces the run's severity threshold and comment budget, excludes ignored findings, and uses a stable hidden marker so retries can update an existing comment.

`docdrift_worker.fix_integration` calls a configured `FixCandidateClient` only for deterministic false findings that have unignored, current-checkout evidence. The Anthropic adapter returns only a structured replacement string for the existing span; the core patch builder and full edited-region reverification then pass the complete result, patch, claim, revision, and changed-line map to the delivery planner. Without explicit LLM enablement and complete configuration, the default worker has no proposal provider configured. Provider errors, stale evidence, unsuccessful reverification, invalid locations, and out-of-diff targets produce no suggestion.

`docdrift_worker.fix_delivery.plan_verified_fix_delivery` is a pure planning boundary. It requires the complete successful edited-region verification result, a patch that exactly matches that result, a valid changed-line map, and matching analyzed/current PR head SHAs. A safe target line in the PR diff selects `inline_suggestion`; all other cases retain the existing finding as the annotation/summary fallback. The worker passes planner-approved suggestions to the publisher in memory during the same job invocation. The publisher substitutes a GitHub suggestion block into that finding's existing budgeted inline comment only when the plan, finding, location, and head still match and the live PR revision check succeeds. Existing severity thresholds, comment budgets, ignore rules, run idempotency, and stale-run protections still apply. Candidate patches and verification results are not persisted, so a retry after transient proposal data is lost safely publishes the original finding without a suggestion.

`<!-- docdrift-ignore -->` is a standalone, single-line exclusion marker. It applies only to claims extracted from the next physical Markdown line in the same file. It cannot cross blank lines or affect another document. The marker is recorded as the persisted ignore reason; ordinary HTML comments are unchanged. Repository-level claim-ID and document exclusions take precedence over marker reasons.
