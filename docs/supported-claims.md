# Supported claims and checker behavior

Only the explicit forms below are extracted. Other natural-language claims are ignored rather than treated as true.

| Claim | Supported form | Verification source | Abstains when |
|---|---|---|---|
| Function parameters | `` `name(a, b)` accepts ... `` or `takes` / `has parameters` | Unique statically indexed Python function and the complete ordered list of parameter names | Missing or duplicate function, `*`/`**`/`/` calling-convention syntax, explicit `=default` optionality, or source parse problem |
| Explicit default | `The default [for] name is 30.` Literals: `None`, booleans, numbers, quoted strings | Unique Python parameter with a safely parsed literal default | Ambiguous parameter name, dynamic default, or unsupported literal |
| CLI flag | A backticked `--flag`, optionally in `Run the CLI with ...` | Literal `argparse` `add_argument`, Click `@click.option` on a command-decorated function, or literal `typer.Option` on a function decorated by a recognized `Typer` app command | No recognized declaration, duplicate declaration, or computed flag spelling |
| Environment name | Backticked all-capital name such as `` `API_KEY` `` | Literal key in `os.getenv`, `os.environ.get`, or `os.environ[...]` | No recognized read; absence does not prove the variable is never used dynamically |
| Repository file | Backticked relative path containing `/`, or a `.py` filename | Repository filesystem existence | Absolute paths and traversal abstain; absent files are false |
| Python version | `Requires Python M.m or newer.` | `pyproject.toml` `requires-python = ">=M.m"` | Missing metadata or any other requirement range grammar |
| HTTP route | ``The GET `/health` endpoint exists.`` (also `route`; methods GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD) | Literal FastAPI `@app.get(...)` and equivalent method decorators, or Flask `@app.route(..., methods=[...])` on a statically constructed application | Dynamic path or methods, unresolved router/blueprint mounting or prefix, duplicate declarations, or incomplete source indexing |

Function parameter claims require a complete, ordered name list, but names alone do not assert required/optional status or calling convention. Claims spelling `*args`, `**kwargs`, positional-only `/`, or a default in the signature abstain; use a separate supported default claim to verify a literal default. Click/Typer indexing is syntactic and does not import either framework. It covers the common decorator and `Option` forms above, not arbitrary APIs or dynamically generated commands. CLI checks establish flag declaration only; help text is not compared with arbitrary prose. Environment checks prove a recognized read only. A file's positive evidence points to its first actual source line.

Route methods are normalized to uppercase. Flask GET declarations also include Flask's implicit HEAD and OPTIONS support. An exact path declared only for other methods is a contradictory finding. Direct static application routes can be checked. APIRouter and Blueprint routes are indexed but abstain because endpoint reachability and mount prefixes are not fully resolved; dynamic route declarations make route conclusions abstain for the scan.

Verdicts are `true`, `false`, and `unverifiable`. `false` is used only where source evidence directly contradicts the supported claim. Missing symbols and ambiguous evidence generally abstain. Source parse errors are reported separately and make the CLI exit with operational error code 2.
