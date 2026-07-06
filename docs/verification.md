# Verification

Every full API import ends with an automatic verification pass. You can also run
it on its own at any time — it is read-only and writes nothing:

```bash
uv run skrooge-firefly --import Skrooge.sqlite --verify                 # 0 if all match, 1 if any gap
uv run skrooge-firefly --import Skrooge.sqlite --verify --no-drill-down # faster; totals only
```

## The two independent checks

### 1. Reconstructed check

Rebuilds each account's expected balance and per-month deltas from the mapped
data and compares them to Firefly. On a mismatch it drills into the diverging
months (disable the drill-down with `--no-drill-down` for speed).

### 2. Independent latest-balance check

Reads each account's balance **straight from the Skrooge SQLite** — bypassing
the importer's own mapping logic entirely — and compares it to Firefly's current
balance, plus the journal count. Because it shares no code with the importer,
this is the check that catches a bug *in the importer itself*.

## Informational (non-failing) mismatches

Some accounts genuinely cannot match, and are reported for information rather
than counted as failures:

- **Mixed-currency accounts.** Firefly revalues foreign-currency holdings at its
  own exchange rates, so a live balance can differ from Skrooge's by exchange-
  rate movement. Every *same-currency* account matches exactly.
- **Accounts where Skrooge's own transfer legs disagree** — a pre-existing
  inconsistency in the source data, surfaced but not blamed on the import.

## Reading the output

```
Verify: 69/69 accounts match | journals expected=21193 actual=21193 | 69 Skrooge ops excluded
Latest-balance check (Skrooge vs Firefly): 69/69 accounts match (0 informational, 0 failing)
```

- `journals expected/actual` — Firefly counts **journals**, not transaction
  groups; a split transaction contributes several journals. Opening-balance
  journals are excluded from the total.
- A `MISMATCH` line shows the account, currency, and expected vs actual balance,
  followed by the diverging months when drill-down is on.
- A `BALANCE MISMATCH` line comes from the independent check — this is the one
  to take seriously.

Exit code is `0` only when both checks pass (informational differences do not
fail the run).
