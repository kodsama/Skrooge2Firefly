"""Self-verification: re-read a produced export and compare with pulled data."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from skrooge2firefly.model.entities import Account, Split, Transaction


@dataclass(frozen=True)
class AccountParity:
    """Expected vs actual entry count and sum for one account."""

    name: str
    expected_count: int
    actual_count: int
    expected_sum: Decimal
    actual_sum: Decimal

    @property
    def ok(self) -> bool:
        """Return True when count and sum both match."""
        return self.expected_count == self.actual_count and self.expected_sum == self.actual_sum


def _two(value: Decimal, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}"


def parse_qif(text: str) -> dict[str, list[tuple[str, Decimal]]]:
    """Parse QIF text into per-account (date, amount) entries."""
    result: dict[str, list[tuple[str, Decimal]]] = {}
    current: str | None = None
    in_account_decl = False
    date, amount = "", Decimal(0)
    for line in text.splitlines():
        if line == "!Account":
            in_account_decl = True
        elif line.startswith("!Type:"):
            in_account_decl = False
        elif line.startswith("N") and in_account_decl:
            current = line[1:]
            result.setdefault(current, [])
        elif line.startswith("D") and not in_account_decl:
            date = line[1:]
        elif line.startswith("T") and not in_account_decl:
            amount = Decimal(line[1:])
        elif line == "^" and in_account_decl:
            in_account_decl = False
        elif line == "^" and current is not None and date:
            result[current].append((date, amount))
            date, amount = "", Decimal(0)
    return result


def _booked(split: Split, account: Account) -> Decimal:
    if split.currency_code == account.currency_code:
        return split.amount
    if split.foreign_currency_code == account.currency_code and split.foreign_amount is not None:
        return split.foreign_amount
    return split.amount


def _transaction_sides(txn: Transaction, ours: set[str]) -> list[tuple[str, int]]:
    """Return the (account, sign) sides a transaction touches.

    Withdrawals into / deposits from one of OUR accounts (loan payments and
    drawdowns) move both balances, exactly like transfers.
    """
    first = txn.splits[0]
    if txn.kind == "withdrawal":
        sides = [(first.source_name, -1)]
        if first.destination_name in ours:
            sides.append((first.destination_name, 1))
        return sides
    if txn.kind == "deposit":
        sides = [(first.destination_name, 1)]
        if first.source_name in ours:
            sides.append((first.source_name, -1))
        return sides
    if txn.kind == "transfer":
        return [(first.source_name, -1), (first.destination_name, 1)]
    return []


def expected_parity(
    accounts: list[Account],
    transactions: list[Transaction],
    *,
    include_opening: bool,
    decimals: dict[str, int] | None = None,
) -> dict[str, tuple[int, Decimal]]:
    """Compute the (count, sum) each account must show in the export.

    ``decimals`` maps currency code -> decimal places; each account's own
    currency decides the precision its running total is rounded to, defaulting
    to 2 when the currency is unknown.
    """
    dp = decimals or {}
    expected: dict[str, tuple[int, Decimal]] = {}
    by_name = {a.name: a for a in accounts}
    for a in accounts:
        if include_opening and a.opening_balance is not None:
            expected[a.name] = (1, Decimal(_two(a.opening_balance, dp.get(a.currency_code, 2))))
        else:
            expected[a.name] = (0, Decimal(0))
    ours = set(expected)
    for txn in transactions:
        for name, sign in _transaction_sides(txn, ours):
            if name not in expected:
                continue
            acc = by_name[name]
            count, total = expected[name]
            delta = sum((_booked(s, acc) for s in txn.splits), Decimal(0)) * sign
            expected[name] = (count + 1, total + Decimal(_two(delta, dp.get(acc.currency_code, 2))))
    return expected


def verify_qif(
    text: str,
    accounts: list[Account],
    transactions: list[Transaction],
    decimals: dict[str, int] | None = None,
) -> list[AccountParity]:
    """Compare a rendered QIF document against the pulled data."""
    dp = decimals or {}
    by_name = {a.name: a for a in accounts}
    parsed = parse_qif(text)
    expected = expected_parity(accounts, transactions, include_opening=True, decimals=dp)
    parities = []
    for name, (exp_count, exp_sum) in sorted(expected.items()):
        rows = parsed.get(name, [])
        actual_sum = sum((amt for _, amt in rows), Decimal(0))
        account_dp = dp.get(by_name[name].currency_code, 2)
        actual_sum = Decimal(_two(actual_sum, account_dp))
        parities.append(AccountParity(name, exp_count, len(rows), exp_sum, actual_sum))
    return parities


def verify_skg(
    path: Path,
    accounts: list[Account],
    transactions: list[Transaction],
    decimals: dict[str, int] | None = None,
) -> list[AccountParity]:
    """Round-trip check: read the produced file back with the import pipeline."""
    from skrooge2firefly.model.mapper import Mapper
    from skrooge2firefly.skrooge.reader import SkroogeReader

    with SkroogeReader(path) as reader:
        mapper = Mapper.build(reader)
    # The read-back side goes through the exact same parity computation as the
    # expected side (foreign-amount aware), just over the re-mapped file.
    actual = expected_parity(
        accounts, mapper.transactions, include_opening=False, decimals=decimals
    )
    # Opening balances live in account.f_importbalance, not as operations.
    expected = expected_parity(accounts, transactions, include_opening=False, decimals=decimals)
    parities = []
    for name in sorted(expected):
        exp_count, exp_sum = expected[name]
        act_count, act_sum = actual.get(name, (0, Decimal(0)))
        parities.append(AccountParity(name, exp_count, act_count, exp_sum, act_sum))
    return parities
