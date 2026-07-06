"""Target-agnostic intermediate representation shared by all writers."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

AccountKind = Literal["asset", "liability"]
TransactionKind = Literal["withdrawal", "deposit", "transfer"]


@dataclass(frozen=True)
class Currency:
    """A currency to ensure exists/enabled in Firefly."""

    code: str
    name: str
    symbol: str
    decimal_places: int
    primary: bool


@dataclass(frozen=True)
class Account:
    """An asset or liability account."""

    external_id: str
    name: str
    kind: AccountKind
    role: str | None
    currency_code: str
    opening_balance: Decimal | None
    opening_balance_date: str | None
    liability_type: str | None
    notes: str = ""
    active: bool = True


@dataclass(frozen=True)
class Split:
    """One leg of a transaction (a Firefly transaction journal)."""

    amount: Decimal
    currency_code: str
    source_name: str
    destination_name: str
    category_name: str | None = None
    foreign_amount: Decimal | None = None
    foreign_currency_code: str | None = None
    tags: tuple[str, ...] = ()
    notes: str = ""
    reconciled: bool = False


@dataclass(frozen=True)
class Transaction:
    """A Firefly transaction group: one or more same-typed splits."""

    external_id: str
    kind: TransactionKind
    date: str
    splits: list[Split]
    group_title: str | None = None


@dataclass(frozen=True)
class BudgetLimit:
    """A spending limit for a budget over a period."""

    start: str
    end: str
    amount: Decimal


@dataclass(frozen=True)
class IRBudget:
    """A Firefly budget plus its per-period limits."""

    name: str
    limits: list[BudgetLimit] = field(default_factory=list)


@dataclass(frozen=True)
class Recurrence:
    """A recurring transaction template."""

    external_id: str
    title: str
    kind: TransactionKind
    first_date: str
    repetition_type: str  # daily | weekly | monthly | yearly
    skip: int
    amount: Decimal
    description: str
    source_name: str
    destination_name: str
    currency_code: str
    category_name: str | None = None
