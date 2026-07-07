# CLI reference

One installed command, `skrooge-firefly`, with two mutually-exclusive
directions. Run `uv run skrooge-firefly --help` for the built-in version.

```
skrooge-firefly (--import FILE | --export FILE) [options]
```

Exactly one of `--import` / `--export` is required; passing both is rejected as
ambiguous. Flags that apply only to the other direction are rejected with a
clear error (exit `2`).

## Direction

| Flag | Meaning |
|---|---|
| `--import FILE` | Import this Skrooge `.sqlite` into Firefly III. |
| `--export FILE` | Export Firefly III to this file (`.qif`, or `.sqlite`/`.skg` for native Skrooge). |

## Connection (both directions)

| Flag | Default | Meaning |
|---|---|---|
| `--url URL` | `$FIREFLY_URL` | Firefly base URL. |
| `--token TOKEN` | `$FIREFLY_TOKEN` | Firefly Personal Access Token. |
| `--timeout SECONDS` | `30` | Per-request HTTP timeout. |
| `--log-level LEVEL` | `INFO` | Logging level. |
| `-v`, `--verbose` | off | Shortcut for `--log-level DEBUG`. |

## Import options (with `--import`)

| Flag | Default | Meaning |
|---|---|---|
| `--target {api,csv}` | `api` | Write to the API, or emit CSV for the Data Importer. |
| `--csv-out DIR` | `out` | Output directory for `--target csv`. |
| `--ledger PATH` | `.import-state.json` | Idempotency ledger path. |
| `--only SECTIONS` | all | Comma-separated subset of `accounts,transactions,budgets,subscriptions`. |
| `--since YYYY-MM-DD` | none | Only import transactions on/after this date. |
| `--until YYYY-MM-DD` | none | Only import transactions on/before this date. |
| `--dry-run` | off | Pre-flight only: validate, write nothing. |
| `--update` | off | Re-sync existing records instead of skipping. |
| `--orphans {report,delete,ignore}` | prompt if interactive, else `report` | With `--update`, what to do with records Firefly has that the file no longer does. |
| `--decisions PATH` | none | Orphan-decisions plan file: written by `--dry-run`, applied on the real run. |
| `--verify` | off | Read-only parity check against the file (no writes). |
| `--no-drill-down` | off | With `--verify`, skip per-month deltas. |
| `--skip-verify` | off | Skip the automatic post-import verification. |
| `--strict` | off | Abort on the first row error. |
| `--assume-empty-target` | off | Skip reconciliation reads (empty instance). |
| `--concurrency N` | `1` | Parallel transaction POSTs (API target). |

## Export options (with `--export`)

| Flag | Default | Meaning |
|---|---|---|
| `--format {qif,sqlite}` | inferred from extension | Override the export format. |
| `--template PATH` | `$SKROOGE_TEMPLATE`, else `$SKROOGE_FILE` | Skrooge `.sqlite` template for `--format sqlite`. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | success (import + verification matched, export verified, or dry-run OK) |
| `1` | records failed, verification mismatch, or a dry-run issue |
| `2` | configuration, input-file/template, or Firefly API error |

## Examples

```bash
skrooge-firefly --import Skrooge.sqlite --dry-run     # pre-flight, no writes
skrooge-firefly --import Skrooge.sqlite               # import + auto-verify
skrooge-firefly --import Skrooge.sqlite --update      # re-sync existing records
skrooge-firefly --import Skrooge.sqlite --verify      # read-only parity check
skrooge-firefly --import Skrooge.sqlite --target csv --csv-out ./out
skrooge-firefly --export backup.qif                  # export → QIF
skrooge-firefly --export backup.sqlite               # export → native Skrooge
```
