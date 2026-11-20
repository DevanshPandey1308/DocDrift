# Deterministic-engine benchmark

This package generates small, reproducible repositories, runs the shipped
DocDrift CLI in JSON mode, and reports precision, recall, F1, extraction, and
abstention metrics. The harness itself uses the Python standard library and
the core dependencies already required by DocDrift.

## Run it

From the repository root, with the core and CLI dependencies installed and the
monorepo source paths available:

```powershell
$env:PYTHONPATH = ".;apps/api/src;packages/core/src;packages/cli/src;apps/mcp/src"
python -m benchmark.harness generate --seed 1337 --repos 60
python -m benchmark.harness run --seed 1337 --repos 60 --write-baseline
python -m benchmark.harness check --baseline benchmark/baseline.json --tolerance 0.02
```

`generate` defaults to seed `1337` and 60 repositories. It replaces only the
generated `benchmark/datasets/synthetic/` directory. Repeating the same seed
and repository count produces byte-identical dataset files. `run` defaults to
`benchmark/results/latest` and prints progress after every ten repositories.
It writes `results.json`, `results.md`, and `failures.csv`; the Markdown report
is also copied to `benchmark/results/latest.md`. `--write-baseline` stores
overall precision/recall and per-claim-type recall in `benchmark/baseline.json`
without timestamps or latency. `check` compares those values to the most
recent results and exits 1 when any current metric is below its baseline by
more than the tolerance.

Options `--dataset-root` and `--out` are available on `generate` and `run` to
use temporary datasets or output directories. `check` also accepts `--out`.

## Dataset format (schema version 1)

Generator version 2 preserves the original seeded statements and appends four
third-party decoy commands from an independent `Random(seed + 1)` stream. The
same decoys occur in mutated and unmutated controls. Each case has a normal
repository tree and a manifest:

```text
benchmark/datasets/<name>/<case_id>/
  repo/                 # source, docs, metadata and referenced files
  manifest.json
```

The manifest is UTF-8 JSON with this shape:

```json
{
  "schema_version": 1,
  "generator_version": 2,
  "case_id": "case-0000",
  "mutated": false,
  "statements": [
    {
      "path": "docs/README.md",
      "line": 12,
      "claim_type": "default_value",
      "category": "canonical",
      "label": "correct",
      "subject": "timeout",
      "operator": null,
      "text": "The default timeout is 30."
    }
  ]
}
```

`path`, one-based `line`, and `text` must point to the exact line in the case
repository. Supported claim types are `default_value`, `function_signature`,
`cli_flag`, `environment_variable`, `file_path`, `python_version`, and `route`.
Categories are `canonical`, `natural_supported`, `unsupported_prose`,
`paraphrase_named`, `decoy`, and `decoy_third_party`. Labels are `correct`,
`drifted`, and `decoy`. An operator names the source-code mutation responsible
for a drifted statement; otherwise it is null. A v2 case has 18 statements
with one statement per line: the original 14 plus four third-party decoys,
including `pytest --cov`, `HTTP_PROXY`, `pip install -e .`, and
`docker run --rm`. Hand-mined real-repository cases can use the same schema.

The seeded generator includes at least six phrase templates each for
unsupported prose, named paraphrases, and the original decoys. Roughly 35% of generated
repositories are unmutated negative controls. Mutations change code, not docs,
using default changes, parameter/flag/environment/route renames, parameter
addition, file deletion, and Python-version changes.

## Metrics

A statement is flagged only when any finding on its line is `false`. A
drifted false line is a true positive; a correct or decoy false line is a false
positive. A drifted line without a false verdict is a false negative, classified
as `not_extracted`, `unverifiable`, or `judged_true`. Precision and recall are
null when their denominators are zero; F1 is null when it cannot be defined.
Extraction rate counts any finding on the statement line. Abstention rate is
reported over correct statements. Results include overall, per-claim-type,
per-category, per-operator, claim-type-by-category, and claim-type-by-label
metrics, plus per-case p50/p95 analysis latency.

N1, `flag-all-mentions`, flags a statement when its subject appears among
symbols or repository paths in the code of a mutated case. It deliberately
flags nothing in unmutated cases, making it a generous baseline. FP/FN details
are listed in `failures.csv`.

## Limitations

This dataset is synthetic and template-generated, so its metrics indicate
harness behavior and are not evidence of real-world accuracy. Recall by
category shows where the extractor does not look. A real-repository dataset is
still pending. Targets from the project blueprint are goals, **not benchmark
results**. `benchmark/results/before.json` contains aggregate metrics for the
v2 generator with the pre-policy engine on seeds 1337, 2024, and 777.
`benchmark/results/triage.md` records seed-1337 failure classifications. The
independent label audit checks generated statements against code using
standard-library AST/regex/TOML handling; it does not establish coverage for
arbitrary prose or repositories.

## Offline real-repository evaluation

The real-repository pipeline is standard-library-only and never clones or fetches
repositories. Place configured local clones under `benchmark/real/repos/`
(`repos.json` lists their GitHub URLs for attribution). It mines reviewed
documentation changes, builds fixed-revision cases, and scores the shipped core
engine:

```powershell
$env:PYTHONPATH = ".;apps/api/src;packages/core/src;packages/cli/src;apps/mcp/src"
python -m benchmark.real mine
# Review candidates.csv using REVIEW.md; fill human_label with drifted, not_drift, or skip.
python -m benchmark.real build
python -m benchmark.real evaluate
# Independently review current FALSE findings:
python -m benchmark.real fp-sample
# Fill fp_review.csv with real_drift, false_positive, or unclear.
python -m benchmark.real fp-score
```

Mining is limited to recent non-merge commits and selected README/docs text
files, with deterministic per-type balancing and caps. Evaluation archives the
parent and fixed commits into ignored snapshots; it does not run repository
code. Results and confirmed documentation bugs are written under
`benchmark/results/real/`.

Interpret these metrics cautiously. The mined sample is selection-biased toward
documentation changes maintainers noticed; mined lines are not random. Recall
applies only to reviewed mined lines, precision is measured only over sampled
`FALSE` findings, labels can be noisy, and small sample sizes are descriptive
counts rather than reliable rates. These figures are not an acceptance claim
for arbitrary repositories.
