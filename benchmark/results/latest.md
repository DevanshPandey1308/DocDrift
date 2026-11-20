# Deterministic DocDrift synthetic benchmark

Seed: `1337`<br>
Repos: `60`; statements: `840`<br>
Case latency p50/p95: `7.265` / `12.276` ms

## Overall

| System | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| DocDrift | 82 | 12 | 42 | 0.87234 | 0.66129 | 0.752293 |
| N1 flag-all-mentions | 74 | 394 | 50 | 0.15812 | 0.596774 | 0.25 |

### Per claim type

| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cli_flag | 60 | 0 | 0 | 6 | null | 0.0 | null | 1.0 | 0.0 |
| default_value | 480 | 45 | 12 | 18 | 0.789474 | 0.714286 | 0.75 | 0.66875 | 0.0 |
| environment_variable | 60 | 0 | 0 | 9 | null | 0.0 | null | 1.0 | 0.0 |
| file_path | 60 | 13 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| function_signature | 60 | 15 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| python_version | 60 | 9 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| route | 60 | 0 | 0 | 9 | null | 0.0 | null | 1.0 | 0.0 |

### Per phrasing category

| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| canonical | 420 | 46 | 0 | 24 | 1.0 | 0.657143 | 0.793104 | 1.0 | 0.0 |
| decoy | 60 | 0 | 12 | 0 | 0.0 | null | null | 0.35 | null |
| natural_supported | 240 | 36 | 0 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |
| paraphrase_named | 60 | 0 | 0 | 11 | null | 0.0 | null | 0.0 | 0.0 |
| unsupported_prose | 60 | 0 | 0 | 7 | null | 0.0 | null | 0.0 | 0.0 |

### Per mutation operator

| Group | Statements | TP | FP | FN | Precision | Recall | F1 | Extracted | Abstain (correct) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| add_param | 53 | 4 | 2 | 0 | 0.666667 | 1.0 | 0.8 | 0.830189 | 0.0 |
| change_default | 115 | 45 | 1 | 14 | 0.978261 | 0.762712 | 0.857143 | 0.8 | 0.0 |
| change_requires_python | 114 | 9 | 3 | 0 | 0.75 | 1.0 | 0.857143 | 0.807018 | 0.0 |
| delete_file | 151 | 13 | 2 | 0 | 0.866667 | 1.0 | 0.928572 | 0.807947 | 0.0 |
| rename_env_read | 103 | 0 | 1 | 9 | 0.0 | 0.0 | null | 0.805825 | 0.0 |
| rename_flag | 68 | 0 | 2 | 6 | 0.0 | 0.0 | null | 0.808824 | 0.0 |
| rename_param | 122 | 11 | 2 | 4 | 0.846154 | 0.733333 | 0.785714 | 0.795082 | 0.0 |
| rename_route | 103 | 0 | 2 | 9 | 0.0 | 0.0 | null | 0.805825 | 0.0 |

## Failure causes

### False positives

- Documented default 53 differs from source default 36.: 1
- Documented default 41 differs from source default 24.: 1
- Documented default 89 differs from source default 72.: 1
- Documented default 91 differs from source default 74.: 1
- Documented default 33 differs from source default 16.: 1

### False negatives

- unverifiable: 24
- not_extracted: 18

## Limitations

- The dataset is synthetic and template-generated; these metrics indicate harness behavior, not real-world accuracy.
- Per-category recall shows where the extractor does not look; a real-repository dataset is still pending.
- Blueprint targets are goals and are not benchmark results.
- N1 is deliberately generous: it flags mentions in mutated cases and never flags unmutated controls.
