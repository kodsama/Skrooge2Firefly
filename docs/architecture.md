# Architecture (for contributors)

A single installed command, `skrooge-firefly`, dispatches to an import or export
pipeline. The design separates **reading**, **pure mapping**, and **writing**, so
the mapping layer can be exercised offline (this is what `--dry-run` relies on).

## Module map

```
src/skrooge2firefly/
├── app.py            Unified `skrooge-firefly` front end (--import / --export dispatch)
├── cli.py            Import orchestration, flags, and verification driver
├── export_cli.py     Export orchestration and flags
├── config.py         Settings resolution (CLI > env > .env > default)
├── verify.py         Post-import verification (two independent checks)
│
├── skrooge/
│   └── reader.py     Read-only Skrooge SQLite reader
│
├── model/            Pure, offline mapping — no network, no writes
│   ├── entities.py   Internal domain types (accounts, transactions, ...)
│   ├── currency.py   Currency / decimal-place handling
│   └── mapper.py     Skrooge model → Firefly model
│
├── writers/          Write targets
│   ├── base.py       WriteReport and the writer interface
│   ├── client.py     HTTP client: auth, retries/backoff, idempotency
│   ├── firefly_api.py  API writer (accounts, transactions, budgets, bills)
│   └── csv_importer.py CSV writer for the Firefly Data Importer
│
└── export/           Firefly → Skrooge
    ├── puller.py     Pull accounts/transactions/budgets from Firefly
    ├── qif.py        Render QIF
    ├── skg.py        Fill a native Skrooge sqlite from a template
    └── verify.py     Export self-verification (re-read the produced file)
```

## Data flow

**Import:** `reader` → `model.mapper` (pure) → `writers` (api or csv) →
`verify`. The mapper produces the full plan before any network call, which is
why `--dry-run` can validate everything without writing.

**Export:** `export.puller` (Firefly) → `export.qif` / `export.skg` →
`export.verify` (re-reads the output file independently of the code that wrote
it).

## Key design points

- **Read-only source.** The Skrooge SQLite is opened read-only; the tool never
  writes to it.
- **Idempotency.** Every created record carries an `external_id`; the API writer
  reconciles against the live instance and records a local ledger
  (`.import-state.json`), so runs are resumable and safe to repeat.
- **Independent verification.** The latest-balance check reads Skrooge balances
  directly, not through the mapper — so it can catch a bug in the mapper itself.
- **Lazy network deps.** The API client is imported lazily, so a CSV-only run
  needs nothing network-related configured.

## Development

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy            # type-check (strict)
```

Lint config lives in `pyproject.toml` (ruff, line length 100, `E/F/I/UP/B/D`;
mypy strict). Tests use `pytest` with `responses` for HTTP mocking.
