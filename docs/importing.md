# Importing Skrooge → Firefly III

```bash
uv run skrooge-firefly --import Skrooge.sqlite            # full import + auto-verify
```

Your Skrooge file is opened **read-only** and never modified. The import is
resumable, safe to re-run, and ends by verifying its own work.

## The happy path

```bash
# 1. Pre-flight — connect, map, and validate everything. Writes NOTHING.
uv run skrooge-firefly --import Skrooge.sqlite --dry-run

# 2. If the pre-flight is OK, run the real import.
uv run skrooge-firefly --import Skrooge.sqlite
```

The import prints a summary, then automatically verifies that Firefly matches
Skrooge and exits non-zero if anything is off.

## What it does, step by step

1. **Read** the Skrooge SQLite file (read-only).
2. **Map** Skrooge's model onto Firefly's. Pure and offline — no network, no
   writes. See [data mapping](data-mapping.md).
3. **Connect** to Firefly and validate the token.
4. **Reconcile** against the live instance: existing accounts/budgets are reused
   by name, already-imported transactions are recognised by their `external_id`,
   existing subscriptions by name.
5. **Write** currencies, accounts, transactions, budgets and subscriptions,
   recording each created record in a local ledger (`.import-state.json`).
6. **Verify** (unless `--skip-verify`) and set the exit code. See
   [verification](verification.md).

## Targets

### `--target api` (default)

Writes directly to Firefly's REST API. Full fidelity — includes budgets and
subscriptions, reconciles against existing data, resumes after interruption, and
auto-verifies.

### `--target csv`

```bash
uv run skrooge-firefly --import Skrooge.sqlite --target csv --csv-out ./out
```

Writes CSV + config files for the
[Firefly Data Importer](https://docs.firefly-iii.org/how-to/data-importer/).
Budgets and subscriptions are **not** representable in this format, and there is
no reconciliation, resume, or auto-verify.

## Pre-flight (`--dry-run`)

`--dry-run` runs a complete pre-flight and changes nothing on Firefly:

- connects and authenticates (catches a wrong URL or token immediately);
- runs the full mapping and reconciliation, reporting how many records **would
  be created** vs are **already present**;
- validates every transaction it would send and **fails (exit 1) on anything
  Firefly would reject** — notably an amount that rounds to `0.00`;
- writes nothing and touches no ledger.

```
$ uv run skrooge-firefly --import Skrooge.sqlite --dry-run
Pre-flight — connected to Firefly 6.6.6
Pre-flight: 19290 record(s) would be created, 0 already present, 0 issue(s). Nothing was written.
Pre-flight OK — re-run without --dry-run to import.
```

Run it before every real import.

## Correcting an existing import (`--update`)

```bash
uv run skrooge-firefly --import Skrooge.sqlite --update
```

Re-syncs already-imported data instead of skipping it (useful after upgrading
the tool). It PUTs the freshly-mapped version over each existing transaction
(matched by `external_id`), creates any that are missing, and PATCHes accounts
so their open/closed state matches Skrooge. An existing budget is reused as-is;
its monthly limits are not modified.

## Resuming, and importing into a non-empty instance

The API target records every created record in `.import-state.json` and
reconciles against the live instance before writing. Therefore:

- **An interrupted run resumes** — just run the same command again.
- **It is safe against an instance that already has data** (yours or a prior
  run): accounts/budgets reused by name, transactions skipped by `external_id`,
  subscriptions skipped by name. "Already exists" counts as *skipped*, never
  *failed*.

Use `--assume-empty-target` to skip the reconciliation reads when the instance
is known to be empty (slightly faster).

## Scoping a run

```bash
uv run skrooge-firefly --import Skrooge.sqlite --only accounts,transactions
uv run skrooge-firefly --import Skrooge.sqlite --since 2024-01-01 --until 2024-12-31
```

- `--only` — comma-separated subset of `accounts,transactions,budgets,subscriptions`.
- `--since` / `--until` — restrict transactions to a date range (inclusive, `YYYY-MM-DD`).

Note: partial runs (`--only`, `--since`, `--until`) do **not** auto-verify,
since a subset can never match the whole Skrooge file.

## Resilience and tuning

Transient failures (HTTP 429/5xx, Cloudflare 52x origin errors, connection
drops, and empty-body responses from an overloaded origin) are retried
automatically with exponential backoff — up to 8 retries per request, honouring
`Retry-After`. Before re-sending a transaction whose first attempt failed
mid-flight, the client checks by `external_id` whether it actually landed, so
retries never create duplicates. If 15 transactions fail in a row the run aborts
(the server is presumed down) rather than hammering it — just re-run later.

- `--concurrency N` — parallel transaction POSTs for the API target (**default
  1**). Raise it on a healthy server to go faster; lower it (or leave at 1) for
  a small or overloaded instance.
- `--timeout SECONDS` — per-request HTTP timeout (default 30). Raise it for slow
  instances.
- `--strict` — abort on the first row error instead of collecting failures.
- `-v` / `--verbose` — DEBUG logging (shows per-row warnings and retries).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | success — imported (or dry-run OK) and verification matched |
| `1` | some records failed, verification found a mismatch, or a dry-run found an issue |
| `2` | configuration, input-file, or Firefly API error |

See also the full [CLI reference](cli-reference.md) and
[troubleshooting](troubleshooting.md).
