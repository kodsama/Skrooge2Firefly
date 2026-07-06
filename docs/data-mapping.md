# Data mapping: Skrooge → Firefly III

How each Skrooge concept is represented in Firefly. The mapping step is pure and
offline — it runs identically under `--dry-run`.

## Overview

| Skrooge | Firefly III |
|---|---|
| Accounts (current / savings / wallet) | Asset accounts (with the right role) |
| Credit-card accounts (Skrooge type `D`) | Asset accounts, role *credit card* |
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
| Budgets, recurring operations | Budgets, subscriptions (Firefly *Bills*) — API target only |

## Notes

### ¹ Opening balances

Skrooge encodes an account's opening balance as an operation dated `0000-00-00`.
It is detected and imported as the Firefly opening balance. For a liability the
sign is preserved so loans stay negative (Firefly needs
`liability_direction=debit` for these).

### ² Foreign-currency operations

A NOK operation inside an otherwise-SEK account, say, is converted to SEK at
Skrooge's own historical exchange rate (`unitvalue`), and the original NOK amount
is kept as Firefly's `foreign_amount`. Amounts are formatted at the server's
per-currency decimal places (e.g. BTC uses 6), not Skrooge's.

### ³ Securities

Firefly has no securities model, so a fund/stock **purchase is imported as an
expense** (and a **sale as income**) against a `Securities` account, **tagged
`security`**, with the share quantity in the description. This keeps cash
balances correct and lets you find and reclassify them later, once Firefly
supports securities. The share unit itself is not tracked.

## Transfers and liabilities

- A pair of Skrooge operations that mirror each other becomes a **single Firefly
  transfer** (asset ↔ asset only).
- A transfer leg that touches a **liability** is booked as a withdrawal or
  deposit instead (Firefly transfers are asset-to-asset).
- Loan payments and draw-downs move **both** account balances.
- Transfers to closed or mixed-currency accounts are handled explicitly (the
  writer temporarily activates an inactive counterparty, then restores it).

## Skipped inputs

Reported as warnings/counts, never as failures:

- **Template (recurrence-blueprint) operations** — the blueprint rows behind a
  recurrence, not real money movements.
- **Sub-cent amounts that round to `0.00`** — Skrooge float artefacts that
  Firefly rejects.

## Known limitations

- **Securities are cash-only** (see ³): the money movement is captured, the
  holding is not.
- **Foreign-currency accounts drift.** Firefly stores each transaction at its
  historical rate; Skrooge revalues holdings at *today's* rate. A foreign
  account's live balance can therefore differ between the two systems by
  exchange-rate movement — inherent, not a bug. Every same-currency account
  matches exactly.
- **The CSV target** cannot create budgets or subscriptions.
