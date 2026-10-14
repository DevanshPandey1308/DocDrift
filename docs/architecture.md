# Architecture

DocDrift is split into `docdrift-core` and `docdrift-cli`. The core owns stable domain models, Markdown extraction, a static source index, checker registry, and the reusable analysis pipeline. The CLI parses arguments and renders core results; it does not implement verification rules.

The pipeline discovers Markdown within explicit scopes, extracts supported claims, parses Python files using `ast.parse` without importing them, builds a source index, then dispatches each claim through a checker registry. Findings preserve original document text and stable IDs. Evidence snippets are read from the actual repository file at the recorded line. Source parse errors are separate structured errors.

The exact-name inverted index maps claim subjects to claim IDs for future changed-symbol selection. The initial command performs full scans. Future adapters can consume the core models without depending on CLI presentation.

Repository content is untrusted data. The engine does not execute code, use `eval`/`exec`, retrieve secrets or environment values, or make network calls. File path claims are resolved and constrained to the repository root.
