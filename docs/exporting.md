# Exporting Firefly III → Skrooge

`--export FILE` pulls everything from your Firefly instance and writes a file
Skrooge can open. This is the escape hatch: you are never locked in.

```bash
uv run skrooge-firefly --export backup.qif       # QIF — the version-proof path
uv run skrooge-firefly --export backup.sqlite    # native Skrooge file
```

The **format is inferred from the extension** (`.sqlite`/`.skg` → native
Skrooge; anything else → QIF). Override with `--format {qif,sqlite}`.

## The two formats

### QIF (`.qif`)

A plain-text interchange format that Skrooge's importer reads. Version-proof and
human-inspectable. **Cannot represent** budgets, tags, or subscriptions.

### Native Skrooge SQLite (`.sqlite` / `.skg`)

Copies the schema, views, and triggers from a real Skrooge file
(`--template` / `SKROOGE_TEMPLATE`, falling back to `SKROOGE_FILE`) and fills it
with the exported data — including budgets and tags/trackers, which QIF cannot
carry.

```bash
uv run skrooge-firefly --export backup.sqlite --template MySkrooge.sqlite
```

You need an existing Skrooge file to use as the template; its schema is copied,
its data is not.

## Self-verification

Every export re-reads the file it just produced and compares per-account entry
counts and sums against what Firefly served, exiting `1` on a mismatch. This
catches a bug in the exporter itself, because the verification reads the output
file independently of the code that wrote it.

## What each format can carry

| Data | QIF | native SQLite |
|---|:---:|:---:|
| Accounts, transactions, transfers, splits | ✅ | ✅ |
| Categories, payees | ✅ | ✅ |
| Budgets | ❌ | ✅ |
| Tags / trackers | ❌ | ✅ |
| Subscriptions | ❌ | ❌ |

## Final acceptance is manual

Automated verification confirms counts and sums, but the last check is yours:
**open the produced file in the Skrooge desktop app once** to confirm it looks
right. That step cannot be automated here.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | file written and self-verification matched |
| `1` | self-verification found a mismatch |
| `2` | configuration, template, or Firefly API error |
