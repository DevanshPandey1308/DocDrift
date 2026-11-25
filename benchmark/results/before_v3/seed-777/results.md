# Deterministic DocDrift synthetic benchmark

Seed: `777`<br>
Repos: `60`; statements: `1080`<br>
Case latency p50/p95: `28.752` / `37.948` ms

## Overall

| System | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| DocDrift | 156 | 0 | 27 | 1.0 | 0.852459 | 0.920354 |
| N1 flag-all-mentions | 115 | 503 | 68 | 0.186084 | 0.628415 | 0.287141 |

### Per claim type

| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cli_flag | 240 | 14 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.75 | 0.0 |
| default_value | 480 | 70 | 0 | 27 | 1.0 | 0.721649 | 0.838323 | 0.66875 | 0.0 |
| environment_variable | 120 | 11 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| file_path | 60 | 15 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| function_signature | 60 | 24 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| python_version | 60 | 8 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| route | 60 | 14 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |

### Per phrasing category

| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| canonical | 420 | 100 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| decoy | 60 | 0 | 0 | 0 | null | null | null | 0.35 | null |
| decoy_third_party | 240 | 0 | 0 | 0 | null | null | null | 0.75 | null |
| natural_supported | 240 | 56 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| paraphrase_named | 60 | 0 | 0 | 17 | null | 0.0 | null | 0.0 | 0.0 |
| unsupported_prose | 60 | 0 | 0 | 10 | null | 0.0 | null | 0.0 | 0.0 |

### Per mutation operator

| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| add_param | 215 | 15 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.786047 | 0.0 |
| change_default | 232 | 70 | 0 | 21 | 1.0 | 0.769231 | 0.869565 | 0.793103 | 0.0 |
| change_requires_python | 125 | 8 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.8 | 0.0 |
| delete_directory | 65 | 4 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.784615 | 0.0 |
| delete_file | 170 | 11 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.782353 | 0.0 |
| rename_env_read | 159 | 11 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.773585 | 0.0 |
| rename_flag | 221 | 14 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.791855 | 0.0 |
| rename_param | 145 | 9 | 0 | 6 | 1.0 | 0.6 | 0.75 | 0.772414 | 0.0 |
| rename_route | 219 | 14 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.799087 | 0.0 |

## Failure causes

### False positives


### False negatives

- not_extracted: 27

## Limitations

- The dataset is synthetic and template-generated; these metrics indicate harness behavior, not real-world accuracy.
- Per-category recall shows where the extractor does not look; a real-repository dataset is still pending.
- Blueprint targets are goals and are not benchmark results.
- N1 is deliberately generous: it flags mentions in mutated cases and never flags unmutated controls.
