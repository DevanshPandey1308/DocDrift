# Deterministic DocDrift synthetic benchmark

Seed: `1337`<br>
Repos: `60`; statements: `1080`<br>
Case latency p50/p95: `10.848` / `15.496` ms

## Overall

| System | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| DocDrift | 106 | 0 | 18 | 1.0 | 0.854839 | 0.921739 |
| N1 flag-all-mentions | 74 | 394 | 50 | 0.15812 | 0.596774 | 0.25 |

### Per claim type

| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cli_flag | 240 | 6 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.75 | 0.0 |
| default_value | 480 | 45 | 0 | 18 | 1.0 | 0.714286 | 0.833334 | 0.66875 | 0.0 |
| environment_variable | 120 | 9 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| file_path | 60 | 13 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| function_signature | 60 | 15 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| python_version | 60 | 9 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| route | 60 | 9 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |

### Per phrasing category

| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| canonical | 420 | 70 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| decoy | 60 | 0 | 0 | 0 | null | null | null | 0.35 | null |
| decoy_third_party | 240 | 0 | 0 | 0 | null | null | null | 0.75 | null |
| natural_supported | 240 | 36 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| paraphrase_named | 60 | 0 | 0 | 11 | null | 0.0 | null | 0.0 | 0.0 |
| unsupported_prose | 60 | 0 | 0 | 7 | null | 0.0 | null | 0.0 | 0.0 |

### Per mutation operator

| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| add_param | 69 | 4 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.811594 | 0.0 |
| change_default | 151 | 45 | 0 | 14 | 1.0 | 0.762712 | 0.865385 | 0.788079 | 0.0 |
| change_requires_python | 150 | 9 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.793333 | 0.0 |
| delete_file | 203 | 13 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.793103 | 0.0 |
| rename_env_read | 139 | 9 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.791367 | 0.0 |
| rename_flag | 92 | 6 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.793478 | 0.0 |
| rename_param | 166 | 11 | 0 | 4 | 1.0 | 0.733333 | 0.846154 | 0.783133 | 0.0 |
| rename_route | 139 | 9 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.791367 | 0.0 |

## Failure causes

### False positives


### False negatives

- not_extracted: 18

## Limitations

- The dataset is synthetic and template-generated; these metrics indicate harness behavior, not real-world accuracy.
- Per-category recall shows where the extractor does not look; a real-repository dataset is still pending.
- Blueprint targets are goals and are not benchmark results.
- N1 is deliberately generous: it flags mentions in mutated cases and never flags unmutated controls.
