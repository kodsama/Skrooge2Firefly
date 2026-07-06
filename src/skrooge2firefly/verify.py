"""Read-only comparison of the Skrooge mapping (expected) against Firefly (actual)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from skrooge2firefly.model.mapper import Mapper

TOLERANCE = Decimal("0.01")


@dataclass
class AccountExpectation:
    """Expected state of one account, reconstructed from the IR."""

    name: str
    currency: str
    balance: Decimal = Decimal(0)
    count: int = 0
    by_month: dict[str, Decimal] = field(default_factory=dict)
    by_year: dict[str, Decimal] = field(default_factory=dict)
    mixed_currency: bool = False  # holds ops in a currency other than its own


def expected_accounts(
    mapper: Mapper, currency_decimals: dict[str, int] | None = None
) -> dict[str, AccountExpectation]:
    """Reconstruct each of our accounts' expected balance/count/period-deltas from the IR.

    Each delta is quantized at its currency's Firefly decimal places
    (``currency_decimals``), exactly mirroring how the writer formats amounts,
    so float tails cannot accumulate into false mismatches.
    """
    ours = {a.name: a.currency_code for a in mapper.accounts}
    decimals = currency_decimals or {}
    result: dict[str, AccountExpectation] = {}
    # Seed every account up front (not lazily on first touch): an account with
    # zero transactions — e.g. a loan whose only Skrooge operation IS its
    # opening balance — must still be compared, not silently dropped.
    for a in mapper.accounts:
        balance = Decimal(0)
        if a.opening_balance is not None:
            balance = Decimal(f"{a.opening_balance:.{decimals.get(a.currency_code, 2)}f}")
        result[a.name] = AccountExpectation(name=a.name, currency=a.currency_code, balance=balance)

    def touch(name: str, delta: Decimal, currency: str, date: str) -> None:
        if name not in ours:
            return
        exp = result.get(name)
        if exp is None:
            exp = AccountExpectation(name=name, currency=ours[name])
            result[name] = exp
        if currency != ours[name]:
            exp.mixed_currency = True
        delta = Decimal(f"{delta:.{decimals.get(currency, 2)}f}")
        exp.balance += delta
        exp.count += 1
        exp.by_month[date[:7]] = exp.by_month.get(date[:7], Decimal(0)) + delta
        exp.by_year[date[:4]] = exp.by_year.get(date[:4], Decimal(0)) + delta

    for txn in mapper.transactions:
        for split in txn.splits:
            # touch() ignores names that are not our accounts, so the
            # counterparty side is safe to attempt unconditionally: it only
            # lands for withdrawals INTO / deposits FROM our own accounts
            # (loan payments and drawdowns, which move both balances).
            dest_amt = split.foreign_amount if split.foreign_amount is not None else split.amount
            dest_cur = split.foreign_currency_code or split.currency_code
            if txn.kind == "withdrawal":
                touch(split.source_name, -split.amount, split.currency_code, txn.date)
                touch(split.destination_name, dest_amt, dest_cur, txn.date)
            elif txn.kind == "deposit":
                touch(split.destination_name, split.amount, split.currency_code, txn.date)
                touch(split.source_name, -dest_amt, dest_cur, txn.date)
            else:  # transfer
                touch(split.source_name, -split.amount, split.currency_code, txn.date)
                touch(split.destination_name, dest_amt, dest_cur, txn.date)

    return result


@dataclass
class AccountComparison:
    """Per-account expected-vs-actual result."""

    name: str
    currency: str
    expected_balance: Decimal
    actual_balance: Decimal | None
    balance_ok: bool
    present: bool
    expected_count: int
    month_diffs: list[tuple[str, Decimal, Decimal]] = field(default_factory=list)
    mixed_currency: bool = False  # informational only: Firefly converts foreign ops by FX


# Coarse tolerance for the independent Skrooge-vs-Firefly balance check: it is
# the gross safety net (dropped data, sign flips, missing opening balances —
# always hundreds+), so 1 currency unit swallows accumulated per-transaction
# 2dp rounding noise while catching every real error. The per-transaction IR
# comparison above still uses the tight 0.01 TOLERANCE.
BALANCE_TOLERANCE = Decimal("1.00")


@dataclass
class BalanceParity:
    """Independent latest-balance comparison for one account.

    Skrooge's own raw balance (source of truth) vs Firefly's current balance.
    """

    name: str
    skrooge_balance: Decimal
    firefly_balance: Decimal | None
    ok: bool
    informational: bool
    reason: str = ""


@dataclass
class VerifyReport:
    """Full comparison result."""

    comparisons: list[AccountComparison]
    # Journal (split) counts: Firefly's transaction pagination total counts
    # journals, not groups, so that is the comparable unit.
    expected_group_count: int
    actual_group_count: int
    excluded_ops: int
    balance_parities: list[BalanceParity] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        """True if every account matched and group counts agree.

        Mixed-currency accounts are informational: Firefly values their
        foreign-currency operations by its own exchange rates, which no
        IR-side sum can reproduce. The independent latest-balance check
        (``balance_parities``) must also pass for every non-informational
        account.
        """
        return (
            all(c.balance_ok or c.mixed_currency for c in self.comparisons)
            and all(b.ok or b.informational for b in self.balance_parities)
            and self.expected_group_count == self.actual_group_count
        )


def latest_balance_parities(
    mapper: Mapper, firefly_balances: dict[str, Decimal]
) -> list[BalanceParity]:
    """Compare Skrooge's own per-account balance to Firefly's, independently.

    This does NOT go through the import mapping: ``mapper.account_audits`` is
    read straight from the Skrooge SQLite rows. That independence is the whole
    point — a mapper bug (e.g. dropping opening balances) can't hide here,
    because both sides would otherwise agree with each other while both being
    wrong. Only accounts whose Firefly balance legitimately cannot equal
    Skrooge's — mixed-currency (Firefly applies its own FX) or internally
    inconsistent transfer legs — are reported informationally and never fail
    the run. Share/fund cash IS now preserved (as tagged ``Securities``
    expenses/income), so a share-trading account must still match its cash
    balance and is NOT excused here.
    """
    parities: list[BalanceParity] = []
    for name, audit in sorted(mapper.account_audits.items()):
        firefly = firefly_balances.get(name)
        skrooge = Decimal(f"{audit.cash_balance:.2f}")
        reasons = []
        if audit.mixed_currency:
            reasons.append("holds operations in more than one currency (Firefly applies FX)")
        if audit.legs_differ:
            reasons.append("Skrooge transfer legs disagree (source-data inconsistency)")
        informational = bool(reasons)
        ok = firefly is not None and abs(skrooge - firefly) <= BALANCE_TOLERANCE
        parities.append(
            BalanceParity(
                name=name,
                skrooge_balance=skrooge,
                firefly_balance=firefly,
                ok=ok,
                informational=informational,
                reason="; ".join(reasons),
            )
        )
    return parities


def _actual_month_deltas(rows: list[dict[str, Any]], account_name: str) -> dict[str, Decimal]:
    """Net change per YYYY-MM for ``account_name`` from its Firefly register rows."""
    months: dict[str, Decimal] = {}
    for row in rows:
        for split in row.get("attributes", {}).get("transactions", []):
            amount = Decimal(str(split.get("amount") or "0"))
            if split.get("source_name") == account_name:
                delta = -amount
            elif split.get("destination_name") == account_name:
                delta = amount
            else:
                continue
            month = str(split.get("date", ""))[:7]
            months[month] = months.get(month, Decimal(0)) + delta
    return months


def verify(mapper: Mapper, client: Any, *, drill_down: bool) -> VerifyReport:
    """Compare the IR-derived expectations against Firefly actuals (read-only)."""
    try:
        decimals = client.currency_decimals()
    except Exception:  # noqa: BLE001 - older stubs/servers: fall back to 2dp
        decimals = {}
    expected = expected_accounts(mapper, decimals)
    actual: dict[str, tuple[str, Decimal, str]] = {}
    actual.update(client.account_balances("asset"))
    actual.update(client.account_balances("liability"))

    comparisons: list[AccountComparison] = []
    for name in sorted(expected):
        exp = expected[name]
        act = actual.get(name)
        if act is None:
            comparisons.append(
                AccountComparison(name, exp.currency, exp.balance, None, False, False, exp.count)
            )
            continue
        account_id, actual_balance, _currency = act
        balance_ok = abs(exp.balance - actual_balance) <= TOLERANCE
        month_diffs: list[tuple[str, Decimal, Decimal]] = []
        if not balance_ok and drill_down and not exp.mixed_currency:
            actual_months = _actual_month_deltas(
                client.account_transactions(account_id),
                name,
            )
            for period in sorted(set(exp.by_month) | set(actual_months)):
                ev = exp.by_month.get(period, Decimal(0))
                av = actual_months.get(period, Decimal(0))
                if abs(ev - av) > TOLERANCE:
                    month_diffs.append((period, ev, av))
        comparisons.append(
            AccountComparison(
                name,
                exp.currency,
                exp.balance,
                actual_balance,
                balance_ok,
                True,
                exp.count,
                month_diffs,
                mixed_currency=exp.mixed_currency,
            )
        )

    firefly_balances = {name: bal for name, (_id, bal, _cur) in actual.items()}
    return VerifyReport(
        comparisons=comparisons,
        # Firefly does create an "Opening balance" journal per account that
        # has opening_balance set (visible in that account's own register),
        # but its general /transactions listing excludes opening-balance and
        # reconciliation journal types — confirmed empirically, and matching
        # export/puller.py's existing _SKIPPED_GROUP_TYPES exclusion — so the
        # journal-count total should NOT include them.
        expected_group_count=sum(len(t.splits) for t in mapper.transactions),
        actual_group_count=client.transaction_group_count(),
        excluded_ops=len(mapper.warnings),
        balance_parities=latest_balance_parities(mapper, firefly_balances),
    )
