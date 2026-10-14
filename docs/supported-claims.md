# Supported claims and checker behavior

Only the explicit forms below are extracted. Other natural-language claims are ignored rather than treated as true.

| Claim | Supported form | Verification source | Abstains when |
|---|---|---|---|
| Function parameters | `` `name(a, b)` accepts ... `` or `takes` / `has parameters` | Unique statically indexed Python function and parameter order | Missing or duplicate name, unsupported parameter syntax, or source parse problem |
| Explicit default | `The default [for] name is 30.` Literals: `None`, booleans, numbers, quoted strings | Unique Python parameter with a safely parsed literal default | Ambiguous parameter name, dynamic default, or unsupported literal |
| CLI flag | A backticked `--flag`, optionally in `Run the CLI with ...` | Literal option passed to `argparse`-style `add_argument` | No recognized declaration, duplicate declaration, or dynamic help |
| Environment name | Backticked all-capital name such as `` `API_KEY` `` | Literal key in `os.getenv`, `os.environ.get`, or `os.environ[...]` | No recognized read; absence does not prove the variable is never used dynamically |
| Repository file | Backticked relative path containing `/`, or a `.py` filename | Repository filesystem existence | Absolute paths and traversal abstain; absent files are false |
| Python version | `Requires Python M.m or newer.` | `pyproject.toml` `requires-python = ">=M.m"` | Missing metadata or any other requirement range grammar |

Function parameter claims compare names and order; the prose after the signature is not interpreted as a semantic description. CLI checks establish flag declaration only; help text is not compared with arbitrary prose. Environment checks prove a recognized read only. A file's positive evidence points to its first actual source line.

Verdicts are `true`, `false`, and `unverifiable`. `false` is used only where source evidence directly contradicts the supported claim. Missing symbols and ambiguous evidence generally abstain. Source parse errors are reported separately and make the CLI exit with operational error code 2.
