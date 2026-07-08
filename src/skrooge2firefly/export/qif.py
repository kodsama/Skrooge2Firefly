"""Render pulled Firefly data as a QIF document Skrooge can import."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from skrooge2firefly.model.entities import Account, Split, Transaction


@dataclass(frozen=True)
class QifEntry:
    """One ledger line in one account's QIF block."""

    date: str
    amount: Decimal
    payee: str
    category: str | None
    memo: str
    reconciled: bool
    transfer_to: str | None = None
    splits: tuple[tuple[str | None, Decimal, str], ...] = field(default_factory=tuple)


def _qif_category(name: str | None) -> str | None:
    return name.replace(" > ", ":") if name else None


def _booked_amount(split: Split, account: Account) -> Decimal:
    """Return the split amount in ``account``'s own currency."""
    if split.currency_code == account.currency_code:
        return split.amount
    if split.foreign_currency_code == account.currency_code and split.foreign_amount is not None:
        return split.foreign_amount
    return split.amount


def _entry(
    txn: Transaction, account: Account, sign: int, payee: str, transfer_to: str | None
) -> QifEntry:
    total = sum((_booked_amount(s, account) for s in txn.splits), Decimal(0)) * sign
    splits: tuple[tuple[str | None, Decimal, str], ...] = ()
    category = _qif_category(txn.splits[0].category_name)
    if len(txn.splits) > 1:
        splits = tuple(
            (_qif_category(s.category_name), _booked_amount(s, account) * sign, s.notes)
            for s in txn.splits
        )
        category = None
    memo = txn.group_title or txn.splits[0].notes
    return QifEntry(
        date=txn.date,
        amount=total,
        payee=payee,
        category=category,
        memo=memo or "",
        reconciled=all(s.reconciled for s in txn.splits),
        transfer_to=transfer_to,
        splits=splits,
    )


def ledger_entries(
    accounts: list[Account], transactions: list[Transaction]
) -> dict[str, list[QifEntry]]:
    """Assign each transaction to the account block(s) it appears in."""
    by_name = {a.name: a for a in accounts}
    entries: dict[str, list[QifEntry]] = {a.name: [] for a in accounts}
    for txn in transactions:
        first = txn.splits[0]
        if txn.kind == "withdrawal" and first.source_name in by_name:
            acc = by_name[first.source_name]
            # A withdrawal into one of our own accounts (a loan payment) moves
            # both balances: render it transfer-style on each side.
            counterparty_is_ours = first.destination_name in by_name
            entries[acc.name].append(
                _entry(
                    txn,
                    acc,
                    -1,
                    first.destination_name,
                    first.destination_name if counterparty_is_ours else None,
                )
            )
            if counterparty_is_ours:
                other = by_name[first.destination_name]
                entries[other.name].append(
                    _entry(txn, other, 1, first.source_name, first.source_name)
                )
        elif txn.kind == "deposit" and first.destination_name in by_name:
            acc = by_name[first.destination_name]
            counterparty_is_ours = first.source_name in by_name
            entries[acc.name].append(
                _entry(
                    txn,
                    acc,
                    1,
                    first.source_name,
                    first.source_name if counterparty_is_ours else None,
                )
            )
            if counterparty_is_ours:
                other = by_name[first.source_name]
                entries[other.name].append(
                    _entry(txn, other, -1, first.destination_name, first.destination_name)
                )
        elif txn.kind == "transfer":
            if first.source_name in by_name:
                acc = by_name[first.source_name]
                entries[acc.name].append(
                    _entry(txn, acc, -1, first.destination_name, first.destination_name)
                )
            if first.destination_name in by_name:
                acc = by_name[first.destination_name]
                entries[acc.name].append(_entry(txn, acc, 1, first.source_name, first.source_name))
    for name in entries:
        entries[name].sort(key=lambda e: e.date)
    return entries


def _qif_account_type(account: Account) -> str:
    if account.kind == "liability":
        return "Oth L"
    return {"ccAsset": "CCard", "cashWalletAsset": "Cash"}.get(account.role or "", "Bank")


def _amount(value: Decimal, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def _render_entry(entry: QifEntry, decimals: int) -> list[str]:
    lines = [f"D{entry.date}", f"T{_amount(entry.amount, decimals)}", f"P{entry.payee}"]
    if entry.transfer_to is not None:
        lines.append(f"L[{entry.transfer_to}]")
    elif entry.category:
        lines.append(f"L{entry.category}")
    if entry.memo:
        lines.append(f"M{entry.memo}")
    if entry.reconciled:
        lines.append("CR")
    for category, amount, memo in entry.splits:
        lines.append(f"S{category or ''}")
        if memo:
            lines.append(f"E{memo}")
        lines.append(f"${_amount(amount, decimals)}")
    lines.append("^")
    return lines


def render_qif(
    accounts: list[Account],
    entries: dict[str, list[QifEntry]],
    decimals: dict[str, int] | None = None,
) -> str:
    """Render per-account QIF blocks for every account.

    ``decimals`` maps currency code -> decimal places (from
    ``FireflyClient.currency_decimals()``); an account's own currency decides
    how its amounts are formatted, defaulting to 2 when unknown.
    """
    dp = decimals or {}
    lines: list[str] = []
    for account in accounts:
        qtype = _qif_account_type(account)
        account_dp = dp.get(account.currency_code, 2)
        lines += ["!Account", f"N{account.name}", f"T{qtype}", "^", f"!Type:{qtype}"]
        if account.opening_balance is not None:
            lines += _render_entry(
                QifEntry(
                    date=account.opening_balance_date or "1970-01-01",
                    amount=account.opening_balance,
                    payee="Opening Balance",
                    category=None,
                    memo="",
                    reconciled=False,
                    transfer_to=account.name,
                ),
                account_dp,
            )
        for entry in entries.get(account.name, []):
            lines += _render_entry(entry, account_dp)
    return "\n".join(lines) + "\n"
