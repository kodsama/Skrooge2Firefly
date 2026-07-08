"""Read-only access to a Skrooge SQLite export."""

from __future__ import annotations

import logging
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any

from skrooge2firefly.model.currency import convert_amount

logger = logging.getLogger(__name__)


def _opt_float(value: Any) -> float | None:
    """Coerce a SQLite cell to a float, or None.

    Skrooge stores ``f_importbalance`` as TEXT and frequently leaves it empty,
    so this tolerates ``None``, empty strings, and non-numeric values by
    returning ``None`` rather than raising.

    Args:
        value: The raw SQLite cell value.

    Returns:
        The value as a float, or None when absent or unparseable.

    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fold_name(name: str) -> str:
    """Accent- and case-insensitive key for a name.

    Mirrors how Firefly matches account names (accent-insensitive), so payees
    that Firefly would treat as one map to the same key here.
    """
    # Decompose, then drop only combining marks (accents) — NOT whole non-Latin
    # scripts, so distinct CJK names (which have no accents) never collapse.
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_accents.casefold().strip()


def _opt_str(value: Any) -> str | None:
    """Return a non-empty string, or None for ``None``/empty values.

    Args:
        value: The raw SQLite cell value.

    Returns:
        The string when non-empty, otherwise None.

    """
    return value or None


@dataclass(frozen=True)
class Unit:
    """A Skrooge unit (currency or share)."""

    id: int
    name: str
    symbol: str
    type: str
    decimal_places: int

    @property
    def is_primary(self) -> bool:
        """True for the primary currency (type ``1``)."""
        return self.type == "1"

    @property
    def is_currency(self) -> bool:
        """True for currency units (types ``1``, ``2``, ``C``); False for shares (``S``)."""
        return self.type in ("1", "2", "C")


@dataclass(frozen=True)
class Account:
    """A Skrooge account row."""

    id: int
    name: str
    type: str
    comment: str
    closed: bool
    bank_name: str
    import_balance: float | None
    import_date: str | None


@dataclass(frozen=True)
class Operation:
    """A Skrooge operation (transaction header)."""

    id: int
    group_id: int
    date: str
    account_id: int
    payee_id: int
    unit_id: int
    mode: str
    comment: str
    status: str
    number: str
    template: bool


@dataclass(frozen=True)
class Suboperation:
    """A Skrooge suboperation (split line)."""

    id: int
    operation_id: int
    category_id: int
    value: float
    comment: str
    refund_id: int
    order: int


@dataclass(frozen=True)
class Budget:
    """A Skrooge budget row (per category, per month)."""

    category_id: int
    year: int
    month: int
    budgeted: float


@dataclass(frozen=True)
class Recurrence:
    """A Skrooge recurrent operation."""

    id: int
    operation_id: int
    date: str
    period_increment: int
    period_unit: str


@dataclass(frozen=True)
class AccountAudit:
    """Skrooge's own source-of-truth summary of one account.

    Read straight from the SQLite rows — independent of the import mapping.
    Used by verification as a mandatory, independent cross-check that each
    account's latest balance in Firefly equals Skrooge's. ``cash_balance`` is
    the sum of every non-template suboperation booked in the account's
    dominant currency, INCLUDING Skrooge's ``0000-00-00`` opening-balance
    placeholder (so a missing opening balance shows up as a mismatch). The
    flags mark accounts whose Firefly balance legitimately cannot equal this
    number and are therefore reported informationally rather than as failures.
    """

    name: str
    cash_balance: float
    currency: str
    has_shares: bool
    mixed_currency: bool
    legs_differ: bool


class SkroogeReader:
    """Opens a Skrooge SQLite file read-only and exposes typed iterators."""

    def __init__(self, path: Path) -> None:
        """Open ``path`` read-only.

        Args:
            path: Path to the Skrooge ``.sqlite`` file.

        """
        if not path.exists():
            raise FileNotFoundError(f"Skrooge file not found: {path}")
        uri = f"file:{path}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True)
        self._conn.row_factory = sqlite3.Row

    def __enter__(self) -> SkroogeReader:
        """Return self for use as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the connection on context manager exit."""
        self._conn.close()

    def units(self) -> list[Unit]:
        """Return all units (currencies and shares)."""
        rows = self._conn.execute("SELECT id, t_name, t_symbol, t_type, i_nbdecimal FROM unit")
        return [
            Unit(r["id"], r["t_name"], r["t_symbol"], r["t_type"], r["i_nbdecimal"]) for r in rows
        ]

    def accounts(self) -> list[Account]:
        """Return all accounts with their bank name resolved."""
        rows = self._conn.execute(
            "SELECT a.id, a.t_name, a.t_type, a.t_comment, a.t_close, "
            "a.f_importbalance, a.d_importdate, COALESCE(b.t_name, '') AS bank "
            "FROM account a LEFT JOIN bank b ON b.id = a.rd_bank_id"
        )
        return [
            Account(
                id=r["id"],
                name=r["t_name"].strip(),
                type=r["t_type"],
                comment=r["t_comment"],
                closed=r["t_close"] == "Y",
                bank_name=r["bank"],
                import_balance=_opt_float(r["f_importbalance"]),
                import_date=_opt_str(r["d_importdate"]),
            )
            for r in rows
        ]

    def categories(self) -> dict[int, str]:
        """Return a mapping of category id → full hierarchical name."""
        rows = self._conn.execute("SELECT id, t_fullname FROM category")
        return {r["id"]: r["t_fullname"] for r in rows}

    def payees(self) -> dict[int, str]:
        """Return a mapping of payee id → name, normalised to match Firefly.

        Two normalisations keep Skrooge payees in exact step with the accounts
        Firefly actually creates:

        * whitespace is trimmed (Skrooge sometimes stores stray spaces, which
          Firefly strips on account creation);
        * payees that differ only by accent/case are collapsed to one spelling,
          because Firefly's account matching is accent-insensitive and would
          merge them anyway (e.g. ``Ahlens`` and ``Åhlens`` become one
          account). We keep the richest (most-accented) variant so the Firefly
          account gets the nicer name.
        """
        rows = [
            (r["id"], r["t_name"].strip())
            for r in self._conn.execute("SELECT id, t_name FROM payee")
        ]
        variants: dict[str, list[str]] = defaultdict(list)
        for _id, name in rows:
            variants[_fold_name(name)].append(name)
        canonical = {
            key: max(names, key=lambda n: (sum(ord(c) > 127 for c in n), len(n), n))
            for key, names in variants.items()
        }
        return {_id: canonical[_fold_name(name)] for _id, name in rows}

    def refunds(self) -> dict[int, str]:
        """Return a mapping of refund id → name."""
        rows = self._conn.execute("SELECT id, t_name FROM refund")
        return {r["id"]: r["t_name"] for r in rows}

    def operations(self) -> list[Operation]:
        """Return all operations ordered by date then id."""
        rows = self._conn.execute(
            "SELECT id, i_group_id, d_date, rd_account_id, r_payee_id, rc_unit_id, "
            "t_mode, t_comment, t_status, t_number, t_template "
            "FROM operation ORDER BY d_date, id"
        )
        return [
            Operation(
                id=r["id"],
                group_id=r["i_group_id"],
                date=r["d_date"],
                account_id=r["rd_account_id"],
                payee_id=r["r_payee_id"],
                unit_id=r["rc_unit_id"],
                mode=r["t_mode"],
                comment=r["t_comment"],
                status=r["t_status"],
                number=r["t_number"],
                template=r["t_template"] == "Y",
            )
            for r in rows
        ]

    def suboperations_by_operation(self) -> dict[int, list[Suboperation]]:
        """Return a mapping of operation id → its suboperations, ordered."""
        rows = self._conn.execute(
            "SELECT id, rd_operation_id, r_category_id, f_value, t_comment, "
            "r_refund_id, i_order FROM suboperation ORDER BY rd_operation_id, i_order, id"
        )
        result: dict[int, list[Suboperation]] = defaultdict(list)
        for r in rows:
            result[r["rd_operation_id"]].append(
                Suboperation(
                    id=r["id"],
                    operation_id=r["rd_operation_id"],
                    category_id=r["r_category_id"],
                    value=r["f_value"],
                    comment=r["t_comment"],
                    refund_id=r["r_refund_id"],
                    order=r["i_order"],
                )
            )
        return dict(result)

    def budgets(self) -> list[Budget]:
        """Return all budget rows."""
        rows = self._conn.execute("SELECT rc_category_id, f_budgeted, i_year, i_month FROM budget")
        return [
            Budget(r["rc_category_id"], r["i_year"], r["i_month"], r["f_budgeted"]) for r in rows
        ]

    def recurrences(self) -> list[Recurrence]:
        """Return all recurrent operations."""
        rows = self._conn.execute(
            "SELECT id, rd_operation_id, d_date, i_period_increment, t_period_unit "
            "FROM recurrentoperation"
        )
        return [
            Recurrence(
                r["id"],
                r["rd_operation_id"],
                r["d_date"],
                r["i_period_increment"],
                r["t_period_unit"],
            )
            for r in rows
        ]

    def account_audits(self) -> dict[str, AccountAudit]:
        """Return Skrooge's own per-account summary keyed by account name.

        Computed directly from the raw rows (not via the import mapper), so it
        can serve as an independent cross-check of Firefly's balances. See
        :class:`AccountAudit` for the semantics of each field.
        """
        currency_types = {"1", "2", "C"}
        unit_type: dict[int, str] = {}
        unit_symbol: dict[int, str] = {}
        for u in self._conn.execute("SELECT id, t_type, t_symbol FROM unit"):
            unit_type[u["id"]] = u["t_type"]
            unit_symbol[u["id"]] = u["t_symbol"]

        # Per-operation summed value (suboperations), and operation metadata.
        op_value: dict[int, float] = defaultdict(float)
        for s in self._conn.execute("SELECT rd_operation_id, f_value FROM suboperation"):
            op_value[s["rd_operation_id"]] += s["f_value"]

        # Group ids that contain at least one share (non-currency) leg.
        share_groups: set[int] = set()
        for op in self._conn.execute(
            "SELECT i_group_id, rc_unit_id FROM operation "
            "WHERE i_group_id != 0 AND t_template = 'N'"
        ):
            if unit_type.get(op["rc_unit_id"]) not in currency_types:
                share_groups.add(op["i_group_id"])

        # Two-op, same-currency transfer groups whose leg magnitudes disagree.
        group_legs: dict[int, list[tuple[int, int]]] = defaultdict(list)  # gid -> [(op_id, unit)]
        for op in self._conn.execute(
            "SELECT id, i_group_id, rc_unit_id FROM operation "
            "WHERE i_group_id != 0 AND t_template = 'N'"
        ):
            group_legs[op["i_group_id"]].append((op["id"], op["rc_unit_id"]))
        legs_differ_ops: set[int] = set()
        for legs in group_legs.values():
            if len(legs) != 2:
                continue
            (op_a, u_a), (op_b, u_b) = legs
            same_currency = u_a == u_b and unit_type.get(u_a) in currency_types
            if same_currency and abs(abs(op_value[op_a]) - abs(op_value[op_b])) > 0.01:
                legs_differ_ops.update((op_a, op_b))

        # Walk real operations once, accumulating per account. Keep each cash
        # op's (unit, value, date) so foreign-currency ops can be converted to
        # the account's dominant currency below.
        account_name: dict[int, str] = {}
        cur_counts: dict[int, Counter[int]] = defaultdict(Counter)
        cash_ops: dict[int, list[tuple[int, float, str]]] = defaultdict(list)
        has_shares: set[int] = set()
        legs_differ: set[int] = set()
        for a in self._conn.execute("SELECT id, t_name FROM account"):
            account_name[a["id"]] = a["t_name"]
        for op in self._conn.execute(
            "SELECT id, i_group_id, rd_account_id, rc_unit_id, d_date "
            "FROM operation WHERE t_template = 'N'"
        ):
            aid = op["rd_account_id"]
            uid = op["rc_unit_id"]
            if op["id"] in legs_differ_ops:
                legs_differ.add(aid)
            if unit_type.get(uid) not in currency_types or op["i_group_id"] in share_groups:
                has_shares.add(aid)
            if unit_type.get(uid) in currency_types:
                cur_counts[aid][uid] += 1
                cash_ops[aid].append((uid, op_value[op["id"]], op["d_date"]))

        rates = self.exchange_rates()
        primary = self.primary_unit_id()
        result: dict[str, AccountAudit] = {}
        for aid, name in account_name.items():
            counts = cur_counts.get(aid)
            dominant = counts.most_common(1)[0][0] if counts else None
            balance = 0.0
            if dominant is not None and primary is not None:
                for uid, value, date in cash_ops[aid]:
                    converted = convert_amount(
                        Decimal(str(value)), uid, dominant, primary, rates, date
                    )
                    balance += float(converted) if converted is not None else value
            audit = AccountAudit(
                name=name,
                cash_balance=round(balance, 2),
                currency=unit_symbol.get(dominant, "") if dominant is not None else "",
                has_shares=aid in has_shares,
                mixed_currency=counts is not None and len(counts) > 1,
                legs_differ=aid in legs_differ,
            )
            existing = result.get(name)
            if existing is not None:
                # Skrooge has no UNIQUE constraint on account name, and the
                # writer matches Firefly accounts by name too (see
                # writers/firefly_api.py), so two same-named Skrooge accounts
                # end up feeding the same Firefly account. Sum their balances
                # under that shared name instead of silently dropping one —
                # that matches what Firefly will actually hold.
                logger.warning(
                    "Duplicate account name %r (ids collide when matched by name in "
                    "Firefly); merging their balances for the balance-parity check.",
                    name,
                )
                audit = AccountAudit(
                    name=name,
                    cash_balance=round(existing.cash_balance + audit.cash_balance, 2),
                    currency=existing.currency or audit.currency,
                    has_shares=existing.has_shares or audit.has_shares,
                    mixed_currency=existing.mixed_currency or audit.mixed_currency,
                    legs_differ=existing.legs_differ or audit.legs_differ,
                )
            result[name] = audit
        return result

    def exchange_rates(self) -> dict[int, list[tuple[str, float]]]:
        """Return each unit's historical price in the primary currency.

        Maps unit id → ``(date, price)`` pairs sorted ascending by date, read
        from Skrooge's ``unitvalue`` table (``f_quantity`` is the unit's value
        in the primary currency on ``d_date``).
        """
        rates: dict[int, list[tuple[str, float]]] = defaultdict(list)
        try:
            rows = self._conn.execute(
                "SELECT rd_unit_id, d_date, f_quantity FROM unitvalue ORDER BY rd_unit_id, d_date"
            )
        except sqlite3.OperationalError:
            return {}  # no unitvalue table (e.g. a minimal export) → no rates
        for r in rows:
            rates[r["rd_unit_id"]].append((r["d_date"], r["f_quantity"]))
        return dict(rates)

    def primary_unit_id(self) -> int | None:
        """Return the id of the primary currency unit (``t_type = '1'``), if any."""
        row = self._conn.execute("SELECT id FROM unit WHERE t_type = '1' LIMIT 1").fetchone()
        return int(row["id"]) if row else None
