# Real-repository review instructions

## Mined documentation changes

Review each row in `candidates.csv` in about 30 seconds. Use `review_cmd` only
when the row is unclear.

- `drifted`: the old line asserted code behavior that was false at the parent
  revision, and the new line corrects that fact.
- `not_drift`: the change is a typo, rewording, reformatting, project behavior
  change, or documentation change unrelated to code behavior in this benchmark.
- `skip`: unsure; do not force a label.

Leave `human_label` empty until reviewed. `build` includes only `drifted` rows;
`not_drift` and `skip` are excluded.

## False-positive review

In `fp_review.csv`, leave `human_label` empty until reviewed:

- `real_drift`: the current documentation claim is genuinely wrong.
- `false_positive`: the documentation is right or the finding refers to
  something different.
- `unclear`: insufficient context to decide.

`fp-score` excludes `unclear` from precision's denominator and reports it
separately.
