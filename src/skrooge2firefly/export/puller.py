"""Pull a Firefly-III instance's data into the importer's IR entities."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from skrooge2firefly.model.entities import Account, Split, Transaction

_SKIPPED_GROUP_TYPES = {"opening balance", "reconciliation"}


@dataclass
class PulledData:
    """Everything pulled from Firefly that the export writers consume."""

    accounts: list[Account]
    transactions: list[Transaction]
    budgets: list[tuple[str, list[tuple[int, int, Decimal]]]]


def _account(item: dict[str, Any]) -> Account:
    a = item["attributes"]
    is_asset = a["type"] == "asset"
    opening = Decimal(str(a["opening_balance"])) if a.get("opening_balance") else None
    if opening == 0:
        opening = None
    return Account(
        external_id=f"firefly:acct:{item['id']}",
        name=a["name"],
        kind="asset" if is_asset else "liability",
        role=a.get("account_role") if is_asset else None,
        currency_code=a.get("currency_code") or "",
        opening_balance=opening,
        opening_balance_date=a.get("opening_balance_date") or None,
        liability_type=None if is_asset else a["type"],
        notes=a.get("notes") or "",
        active=bool(a.get("active", True)),
    )


def _split(s: dict[str, Any]) -> Split:
    return Split(
        amount=Decimal(s["amount"]),
        currency_code=s["currency_code"],
        source_name=s["source_name"],
        destination_name=s["destination_name"],
        category_name=s.get("category_name") or None,
        foreign_amount=Decimal(s["foreign_amount"]) if s.get("foreign_amount") else None,
        foreign_currency_code=s.get("foreign_currency_code") or None,
        tags=tuple(s.get("tags") or ()),
        notes=s.get("notes") or "",
        reconciled=bool(s.get("reconciled", False)),
    )


def _transaction(item: dict[str, Any]) -> Transaction | None:
    splits = item["attributes"]["transactions"]
    if not splits or splits[0]["type"] in _SKIPPED_GROUP_TYPES:
        return None
    first = splits[0]
    return Transaction(
        external_id=first.get("external_id") or f"firefly:tx:{item['id']}",
        kind=first["type"],
        date=first["date"][:10],
        splits=[_split(s) for s in splits],
        group_title=item["attributes"].get("group_title") or None,
    )


def _budget_limits(limits: list[dict[str, Any]]) -> list[tuple[int, int, Decimal]]:
    result = []
    for lim in limits:
        a = lim["attributes"]
        year, month = int(a["start"][:4]), int(a["start"][5:7])
        result.append((year, month, Decimal(str(a["amount"]))))
    return result


def pull(client: Any) -> PulledData:
    """Pull accounts, transactions and budgets from a Firefly client."""
    accounts = [_account(i) for i in client.list_accounts("asset")]
    accounts += [_account(i) for i in client.list_accounts("liabilities")]
    transactions = [
        t for i in client.list_transaction_groups() if (t := _transaction(i)) is not None
    ]
    budgets = [(name, _budget_limits(lims)) for name, lims in client.list_budgets_with_limits()]
    return PulledData(accounts=accounts, transactions=transactions, budgets=budgets)
