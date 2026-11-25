# Deterministic DocDrift synthetic benchmark

Seed: `2024`<br>
Repos: `60`; statements: `1080`<br>
Case latency p50/p95: `33.455` / `42.705` ms

## Overall

| System | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| DocDrift | 128 | 0 | 22 | 1.0 | 0.853333 | 0.920863 |
| N1 flag-all-mentions | 93 | 410 | 57 | 0.184891 | 0.62 | 0.28484 |

### Per claim type

| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cli_flag | 240 | 11 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.75 | 0.0 |
| default_value | 480 | 55 | 0 | 22 | 1.0 | 0.714286 | 0.833334 | 0.66875 | 0.0 |
| environment_variable | 120 | 8 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| file_path | 60 | 14 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| function_signature | 60 | 20 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| python_version | 60 | 7 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| route | 60 | 13 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |

### Per phrasing category

| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| canonical | 420 | 84 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| decoy | 60 | 0 | 0 | 0 | null | null | null | 0.35 | null |
| decoy_third_party | 240 | 0 | 0 | 0 | null | null | null | 0.75 | null |
| natural_supported | 240 | 44 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| paraphrase_named | 60 | 0 | 0 | 11 | null | 0.0 | null | 0.0 | 0.0 |
| unsupported_prose | 60 | 0 | 0 | 11 | null | 0.0 | null | 0.0 | 0.0 |

### Per mutation operator

| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| add_param | 208 | 13 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.778846 | 0.0 |
| change_default | 179 | 55 | 0 | 18 | 1.0 | 0.753425 | 0.859375 | 0.776536 | 0.0 |
| change_requires_python | 104 | 7 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.798077 | 0.0 |
| delete_directory | 27 | 2 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.740741 | 0.0 |
| delete_file | 194 | 12 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.783505 | 0.0 |
| rename_env_read | 126 | 8 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.785714 | 0.0 |
| rename_flag | 171 | 11 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.777778 | 0.0 |
| rename_param | 96 | 7 | 0 | 4 | 1.0 | 0.636364 | 0.777778 | 0.770833 | 0.0 |
| rename_route | 189 | 13 | 0 | 0 | 1.0 | 1.0 | 1.0 | 0.798942 | 0.0 |

## Failure causes

### False positives


### False negatives

- not_extracted: 22

## Limitations

- The dataset is synthetic and template-generated; these metrics indicate harness behavior, not real-world accuracy.
- Per-category recall shows where the extractor does not look; a real-repository dataset is still pending.
- Blueprint targets are goals and are not benchmark results.
- N1 is deliberately generous: it flags mentions in mutated cases and never flags unmutated controls.
