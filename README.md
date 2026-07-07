# Skrooge2Firefly

[![CI](https://github.com/kodsama/Skrooge2Firefly/actions/workflows/ci.yml/badge.svg)](https://github.com/kodsama/Skrooge2Firefly/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/kodsama/Skrooge2Firefly/python-coverage-comment-action-data/endpoint.json)](https://github.com/kodsama/Skrooge2Firefly/tree/python-coverage-comment-action-data)
[![Latest tag](https://img.shields.io/github/v/tag/kodsama/Skrooge2Firefly?label=release&sort=semver)](https://github.com/kodsama/Skrooge2Firefly/tags)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

Migrate a [Skrooge](https://skrooge.org/) SQLite file into
[Firefly III](https://www.firefly-iii.org/) — and export it back out again.

One command, two directions:

```bash
skrooge-firefly --import Skrooge.sqlite    # import a Skrooge file into Firefly III
skrooge-firefly --export backup.qif        # export Firefly III back to a file
```

- **`--import FILE`** loads a Skrooge `.sqlite` into Firefly III (directly via
  its REST API, or as CSV for the Firefly Data Importer with `--target csv`).
- **`--export FILE`** writes a Firefly III instance back to a QIF file or a
  native Skrooge `.sqlite` (an escape hatch, so you are never locked in) — the
  format is inferred from the extension.

Exactly one direction per run (passing both is rejected as ambiguous). Both are
read-safe by default, resumable, and **verify their own work**: an import ends
by checking that Firefly's balances and counts match Skrooge, and an export
re-reads the file it produced and compares it to the source.

> Status: free/open tooling for a one-person migration. It has been exercised
> against a real ~23,000-operation Skrooge file. Always keep your original
> Skrooge file — treat Firefly as the copy until you've eyeballed the result.

## Documentation

Full guides live in [`docs/`](docs/README.md):

- [Installation](docs/installation.md) · [Configuration](docs/configuration.md) · [Importing](docs/importing.md) · [Exporting](docs/exporting.md)
- [Verification](docs/verification.md) · [Data mapping](docs/data-mapping.md) · [CLI reference](docs/cli-reference.md) · [Troubleshooting](docs/troubleshooting.md) · [Architecture](docs/architecture.md)

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/) (`uv sync` installs everything).
- A Firefly III instance and a **Personal Access Token**
  (Firefly III → *Options → Profile → OAuth → Personal Access Tokens*).

## Setup

```bash
uv sync
cp .env.example .env      # then edit the values below
```

All settings come from `.env` (which is gitignored), the real environment, or
CLI flags. Precedence: **CLI flag > environment variable > `.env` > built-in default**.

| Variable | Meaning | CLI override |
|---|---|---|
| `SKROOGE_FILE` | default Skrooge `.sqlite` for `--import` | `--import` |
| `FIREFLY_URL` | Firefly III base URL (e.g. `https://firefly.example.com`) | `--url` |
| `FIREFLY_TOKEN` | Firefly Personal Access Token | `--token` |
| `SKROOGE_TEMPLATE` | template `.sqlite` for `--export` to a native Skrooge file (defaults to `SKROOGE_FILE`) | `--template` |

Run `uv run skrooge-firefly --help` for the full flag list, the environment
variables, and the exit codes.

## Quick start

```bash
# 1. Pre-flight: connect, map, and validate everything — writes NOTHING.
uv run skrooge-firefly --import Skrooge.sqlite --dry-run

# 2. If the pre-flight says OK, run the real import (resumable, auto-verified).
uv run skrooge-firefly --import Skrooge.sqlite
```

That's the whole happy path. The import prints a summary, then automatically
verifies that Firefly matches Skrooge and exits non-zero if anything is off.

## What the import does, step by step

1. **Read** the Skrooge SQLite file (opened read-only; your file is never modified).
2. **Map** Skrooge's model to Firefly's (see *What gets imported* below). This
   step is pure and offline — no network, no writes.
3. **Connect** to Firefly and verify the token.
4. **Reconcile** against the live instance: existing accounts/budgets are reused
   by name, already-imported transactions are recognised by their `external_id`,
   existing recurring transactions by title. This makes the import safe to re-run and safe
   against a non-empty instance.
5. **Write** currencies, accounts, transactions, budgets and recurring transactions —
   recording each created record in a local ledger (`.import-state.json`) so an
   interrupted run resumes exactly where it left off.
6. **Verify** (unless `--skip-verify`): compare Firefly against Skrooge and set
   the exit code accordingly.

### Targets

- `--target api` (default): writes directly to Firefly. Full fidelity —
  includes budgets and recurring transactions, reconciles, resumes, and verifies.
- `--target csv --csv-out ./out`: writes CSV + config files for the
  [Firefly Data Importer](https://docs.firefly-iii.org/how-to/data-importer/).
  Budgets and recurring transactions are not representable in that format.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | success — imported (or dry-run OK), and verification matched |
| `1` | some records failed, verification found a mismatch, or a dry-run found an issue |
| `2` | configuration, input-file, or Firefly API error |

## `--dry-run` (pre-flight)

`--dry-run` performs a full pre-flight without changing anything on Firefly:

- connects and authenticates (catches a wrong URL or token immediately);
- runs the complete mapping and reconciliation, so it reports how many records
  **would be created** versus are **already present**;
- validates every transaction it would send and **fails (exit 1) on anything
  Firefly would reject** — in particular an amount that rounds to `0.00`;
- writes nothing and touches no ledger.

```
$ uv run skrooge-firefly --import Skrooge.sqlite --dry-run
Pre-flight — connected to Firefly 6.6.6
Pre-flight: 19290 record(s) would be created, 0 already present, 0 issue(s). Nothing was written.
Pre-flight OK — re-run without --dry-run to import.
```

Run it before every real import.

## Verifying an import matches Skrooge

Every full API import ends with an automatic verification pass; you can also run
it on its own at any time (read-only, no writes):

```bash
uv run skrooge-firefly --import Skrooge.sqlite --verify                 # 0 if all match, 1 if any gap
uv run skrooge-firefly --import Skrooge.sqlite --verify --no-drill-down # faster; totals only
```

It runs two independent checks:

1. **Reconstructed check** — rebuilds each account's expected balance and
   per-month deltas from the mapped data and compares to Firefly, drilling into
   the diverging months on a mismatch.
2. **Independent latest-balance check** — reads each account's balance straight
   from the Skrooge SQLite (bypassing the importer's own logic entirely) and
   compares it to Firefly's current balance, plus the journal count. This is the
   check that catches a bug in the importer itself, because it doesn't share any
   code with it.

Accounts that genuinely cannot match are reported informationally, not as
failures: mixed-currency accounts (Firefly revalues foreign holdings at its own
rates) and accounts where Skrooge's own transfer legs disagree.

## Correcting an existing import (`--update`)

Re-sync already-imported data instead of skipping it — useful after upgrading
the tool:

```bash
uv run skrooge-firefly --import Skrooge.sqlite --update
```

`--update` PUTs the freshly-mapped version over each existing transaction
(matched by `external_id`), creates any that are missing, and PATCHes accounts
so their open/closed state matches Skrooge. The summary reports an `updated`
count. (An existing budget is reused as-is; `--update` syncs any newly-added
monthly limits, and rewrites a recurring transaction whose managed fields have
changed.)

## Resuming, and importing into a non-empty instance

The API target records every created record in `.import-state.json`, and
reconciles against the live instance before writing. So:

- an interrupted run resumes on re-run — just run the same command again;
- it is safe against an instance that already has data (yours or a prior run):
  accounts/budgets are reused by name, transactions skipped by `external_id`,
  recurring transactions skipped by title. "Already exists" counts as *skipped*, never
  *failed*.

Pass `--assume-empty-target` to skip the reconciliation reads when the instance
is known to be empty (a little faster). Use `--only accounts,transactions,budgets,recurrences`
to limit the run to specific sections.

## Resilience

Transient failures (HTTP 429/5xx, Cloudflare 52x origin errors, connection
drops, and empty-body responses from an overloaded origin) are retried
automatically with exponential backoff — up to 8 retries per request, honouring
`Retry-After`. Before re-sending a transaction whose first attempt failed
mid-flight, the client checks by `external_id` whether it actually landed, so
retries never create duplicates. If 15 transactions fail in a row the run aborts
(the server is down) rather than hammering it; just re-run later. Raise
`--timeout` for slow instances and raise `--concurrency` (default 1) on a
healthy server to go faster — or keep it low if the server struggles.

## What gets imported

| Skrooge | Firefly III |
|---|---|
| Accounts (current/savings/wallet) | Asset accounts (with the right role) |
| Credit-card accounts | Asset accounts, role *credit card* |
| Loans (negative-balance accounts) | Liability accounts (kept negative) |
| Account opening balances¹ | Firefly opening balance |
| Operations, incl. multi-split | Withdrawals / deposits / transfers, incl. splits |
| Two-sided transfer pairs | A single Firefly transfer |
| Loan payments / draw-downs | Withdrawal / deposit against the liability |
| Categories `Parent > Child` | Nested categories |
| Payees, reconciliation status | Payees, reconciled flag |
| Refund trackers | Tags |
| Foreign-currency operations² | Converted to the account currency (original kept as foreign amount) |
| Share / fund buys & sells³ | Tagged `security` expenses / income |
| Budgets, recurring operations | Budgets, recurring transactions (Firefly *Recurrences*) — API target only |

¹ Skrooge encodes an account's opening balance as an operation dated
`0000-00-00`. It's detected and imported as the Firefly opening balance (for a
liability the sign is preserved so loans stay negative).

² A NOK operation in an otherwise-SEK account, say, is converted to SEK at
Skrooge's own historical exchange rate (`unitvalue`), and the original NOK
amount is kept as Firefly's `foreign_amount`.

³ Firefly has no securities model, so a fund/stock purchase is imported as an
expense (and a sale as income) to a `Securities` account, **tagged `security`**
with the share quantity in the description. This keeps cash balances correct and
lets you find and reclassify them later, once Firefly supports securities. The
share unit itself is not tracked.

Skipped (reported as warnings/counts, never as failures): template
(recurrence-blueprint) operations, and sub-cent amounts that round to `0.00`
(Skrooge float artefacts).

### Known limitations

- **Securities are cash-only** (see ³): the money movement is captured; the
  holding is not.
- **Foreign-currency accounts drift.** Firefly stores each transaction at its
  historical rate; Skrooge revalues holdings at *today's* rate. So a foreign
  account's live balance can differ between the two systems by exchange-rate
  movement — inherent, not a bug. Every same-currency account matches exactly.
- **The CSV target** cannot create budgets or recurring transactions.

## Exporting back to Skrooge

`--export FILE` pulls everything from Firefly and writes a file Skrooge can
open. The format is inferred from the extension:

```bash
uv run skrooge-firefly --export backup.qif       # QIF — the version-proof path
uv run skrooge-firefly --export backup.sqlite    # native Skrooge file (schema copied from a template)
```

Every export self-verifies (per-account entry counts and sums against what
Firefly served) and exits 1 on a mismatch. The `.sqlite` target copies the
schema, views and triggers from a real Skrooge file (`SKROOGE_TEMPLATE`) and
fills it with the exported data — including budgets and tags/trackers, which QIF
cannot carry. Not representable: QIF — budgets, tags, subscriptions; sqlite —
subscriptions. Final acceptance is manual: open the produced file in Skrooge once.

## Development

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy            # type-check
```

## License

Released under the **GNU General Public License v3.0** — see [`LICENSE`](LICENSE).
Free to use, study, modify, and redistribute; derivative works must remain under
the GPL-3.0. This is not affiliated with or endorsed by the Firefly III or
Skrooge projects.
