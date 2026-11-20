# Seed 1337 failure triage

Source: `benchmark/results/latest/failures.csv` from the 60-repository,
840-statement seed-1337 run. The file has 12 false positives and 42 false
negatives.

## False positives

| Root cause | Class | Count | Example rows |
|---|---|---:|---|
| A generic repository `timeout` parameter was incorrectly used to judge prose explicitly about a different project. The extractor retained the number and subject but not ownership context, so the checker compared it to this repository's `fetch(timeout=...)`. | ENGINE_BUG | 12 | `case-0003`, `docs/README.md:16`: “A different project's default timeout is 53.” (source default 36); `case-0004`, `docs/README.md:16`: “A different project's default timeout is 41.” (source default 24). |

## False negatives

| Root cause | Class | Count | Example rows |
|---|---|---:|---|
| The checker abstained when the named environment read was absent, even though the source contained multiple literal environment reads under the same project namespace and no dynamic read. | ENGINE_ABSTAINS_BY_DESIGN | 9 | `case-0000`, `docs/README.md:10`: “The `APP_MODE_000` environment variable is read.”; `case-0001`, `docs/README.md:10`: “The `APP_MODE_001` environment variable is read.” Both are absent after a rename while the project still reads literal `APP_*` names. |
| The checker abstained when a documented flag was absent. The generated code uses a recognized `argparse.ArgumentParser` and literal `add_argument` declaration; a renamed `--max-records-NNN` is a bounded likely near-miss. | ENGINE_ABSTAINS_BY_DESIGN | 6 | `case-0002`, `docs/README.md:9`: “Run the CLI with `--max-records-002`.”; `case-0003`, `docs/README.md:9`: “Run the CLI with `--max-records-003`.” |
| The checker abstained when a route was absent even though the generated application uses a directly constructed FastAPI app and statically resolved literal route decorator. | ENGINE_ABSTAINS_BY_DESIGN | 9 | `case-0001`, `docs/README.md:13`: “The GET `/items/001` endpoint exists.”; `case-0006`, `docs/README.md:13`: “The GET `/items/006` endpoint exists.” These are renamed routes in otherwise fully indexed source. |
| A duration has no parameter name tying it to a source symbol, so it is outside the default-claim grammar. | OUT_OF_SCOPE_PHRASING | 7 | `case-0000`, `docs/README.md:14`: “By default, calls time out after 83 seconds.”; `case-0012`, `docs/README.md:14`: “By default, calls time out after 60 seconds.” |
| Named parameter paraphrases do not use any supported explicit-default syntax, so they are not extracted. | OUT_OF_SCOPE_PHRASING | 11 | `case-0000`, `docs/README.md:15`: “`timeout` is set to 83 unless overridden.”; `case-0002`, `docs/README.md:15`: “Pass `retries=5` for the usual behaviour.” |

There were no false negatives classified as `judged_true`. The generated route
construction is already realistic (`from fastapi import FastAPI`,
`app = FastAPI()`, and `@app.get(...)`); route recall was zero because absence
was deliberately unverifiable. Likewise, the CLI and environment misses are
policy abstentions, not absent declarations that the existing index failed to
recognize. The benchmark therefore does not justify further code-realism
changes for these causes. Generator v2 adds third-party decoys separately from
the original seeded statement stream.
