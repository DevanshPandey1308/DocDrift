# Architecture

DocDrift is split into `docdrift-core` and `docdrift-cli`. The core owns stable domain models, Markdown extraction, a static source index, checker registry, and the reusable analysis pipeline. The CLI parses arguments and renders core results; it does not implement verification rules.

The pipeline discovers Markdown within explicit scopes, extracts supported claims, parses Python files using `ast.parse` without importing them, builds a source index for symbols, CLI flags, and routes, then dispatches each claim through a checker registry. Findings preserve original document text and stable IDs. Evidence snippets are read from the actual repository file at the recorded line. Source parse errors are separate structured errors.

The exact-name inverted index maps claim subjects to claim IDs for future changed-symbol selection. The initial command performs full scans. Future adapters can consume the core models without depending on CLI presentation.

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
unmatched output become `abstain`. This contract is not connected to the analysis worker,
persisted findings, or GitHub publication; current PR behavior remains deterministic.

Repository content is untrusted data. The engine does not execute code, use `eval`/`exec`, retrieve secrets or environment values, or make network calls. File path claims are resolved and constrained to the repository root. Route and CLI recognizers use AST shape and literal values; they do not import FastAPI, Flask, Click, or Typer. Router mounting and prefixes are not fully resolved, so uncertain route claims abstain.

## Pull request reporting

The worker stores a review-comment line only when the claim's exact Markdown line appears as an added line in the no-renames unified diff between the run's pinned base and head. This covers additions and the new side of modifications; unchanged context, deletions, malformed diffs, and unsupported paths have no inline anchor and remain in the Check Run and summary. Publication rechecks the open pull request's base and head before sending plain `RIGHT`-side comments. It enforces the run's severity threshold and comment budget, excludes ignored findings, and uses a stable hidden marker so retries can update an existing comment. It does not generate suggested changes.

`<!-- docdrift-ignore -->` is a standalone, single-line exclusion marker. It applies only to claims extracted from the next physical Markdown line in the same file. It cannot cross blank lines or affect another document. The marker is recorded as the persisted ignore reason; ordinary HTML comments are unchanged. Repository-level claim-ID and document exclusions take precedence over marker reasons.
