"""Write pulled Firefly data as a native Skrooge sqlite file (template copy)."""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from skrooge2firefly.export.puller import PulledData
from skrooge2firefly.model.currency import iso_code
from skrooge2firefly.model.entities import Account, Split, Transaction

logger = logging.getLogger(__name__)

# Children before parents; parameters/node/unit/unitvalue are intentionally kept.
# Tables absent from a minimal template are skipped (vm_budget_tmp/rule are
# Skrooge-managed caches/rules present in real files).
_CLEAR_TABLES = (
    "vm_budget_tmp",
    "rule",
    "budgetsuboperation",
    "budget",
    "budgetrule",
    "recurrentoperation",
    "interest",
    "operationbalance",
    "suboperation",
    "operation",
    "refund",
    "payee",
    "category",
    "account",
    "bank",
    "doctransactionitem",
    "doctransactionmsg",
    "doctransaction",
)

_ACCOUNT_TYPE = {"ccAsset": "D", "cashWalletAsset": "W", "savingAsset": "S"}


@dataclass
class SkgReport:
    """What the skg writer produced and what it had to skip."""

    accounts: int = 0
    operations: int = 0
    budgets: int = 0
    warnings: list[str] = field(default_factory=list)


def _clear(conn: sqlite3.Connection) -> None:
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in _CLEAR_TABLES:
        if table in existing:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table list


def _unit_map(conn: sqlite3.Connection, currencies: set[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for uid, name, symbol in conn.execute("SELECT id, t_name, t_symbol FROM unit"):
        mapping.setdefault(iso_code(symbol, name), uid)
    for code in sorted(currencies - set(mapping)):
        cur = conn.execute(
            "INSERT INTO unit (t_name, t_symbol, t_type) VALUES (?, ?, '2')", (code, code)
        )
        mapping[code] = int(cur.lastrowid or 0)
    return mapping


def _skg_account_type(account: Account) -> str:
    if account.kind == "liability":
        return "L"
    return _ACCOUNT_TYPE.get(account.role or "", "C")


def _insert_accounts(conn: sqlite3.Connection, accounts: list[Account]) -> dict[str, int]:
    bank_ids: dict[str, int] = {}
    ids: dict[str, int] = {}
    for acct in accounts:
        bank_id = 0
        if acct.notes.startswith("Bank: "):
            bank = acct.notes[len("Bank: ") :].strip()
            if bank and bank not in bank_ids:
                cur = conn.execute("INSERT INTO bank (t_name) VALUES (?)", (bank,))
                bank_ids[bank] = int(cur.lastrowid or 0)
            bank_id = bank_ids.get(bank, 0)
        cur = conn.execute(
            "INSERT INTO account (t_name, t_type, t_close, t_comment, rd_bank_id, "
            "f_importbalance, d_importdate) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                acct.name,
                _skg_account_type(acct),
                "N" if acct.active else "Y",
                acct.notes,
                bank_id,
                float(acct.opening_balance) if acct.opening_balance is not None else None,
                acct.opening_balance_date,
            ),
        )
        ids[acct.name] = int(cur.lastrowid or 0)
    return ids


def _insert_categories(conn: sqlite3.Connection, transactions: list[Transaction]) -> dict[str, int]:
    fullnames = sorted({s.category_name for t in transactions for s in t.splits if s.category_name})
    ids: dict[str, int] = {}
    for fullname in fullnames:
        parent_id = 0
        path = ""
        for part in fullname.split(" > "):
            path = f"{path} > {part}" if path else part
            if path not in ids:
                cur = conn.execute(
                    "INSERT INTO category (t_name, rd_category_id) VALUES (?, ?)",
                    (part, parent_id),
                )
                ids[path] = int(cur.lastrowid or 0)
            parent_id = ids[path]
    return ids


def _insert_payees(
    conn: sqlite3.Connection, accounts: list[Account], transactions: list[Transaction]
) -> dict[str, int]:
    own = {a.name for a in accounts}
    names = set()
    for t in transactions:
        for s in t.splits:
            counterparty = s.destination_name if t.kind == "withdrawal" else s.source_name
            if counterparty and counterparty not in own:
                names.add(counterparty)
    ids = {}
    for name in sorted(names):
        cur = conn.execute("INSERT INTO payee (t_name) VALUES (?)", (name,))
        ids[name] = int(cur.lastrowid or 0)
    return ids


def _insert_refunds(conn: sqlite3.Connection, transactions: list[Transaction]) -> dict[str, int]:
    tags = sorted({tag for t in transactions for s in t.splits for tag in s.tags})
    ids = {}
    for tag in tags:
        cur = conn.execute("INSERT INTO refund (t_name) VALUES (?)", (tag,))
        ids[tag] = int(cur.lastrowid or 0)
    return ids


def _booked(split: Split, account: Account) -> Decimal:
    """Return the split amount in ``account``'s own currency."""
    if split.currency_code == account.currency_code:
        return split.amount
    if split.foreign_currency_code == account.currency_code and split.foreign_amount is not None:
        return split.foreign_amount
    return split.amount


def _insert_one_operation(
    conn: sqlite3.Connection,
    txn: Transaction,
    account: Account,
    sign: int,
    payee_id: int,
    group_id: int,
    unit_map: dict[str, int],
    account_ids: dict[str, int],
    category_ids: dict[str, int],
    refund_ids: dict[str, int],
) -> int:
    unit_id = unit_map.get(account.currency_code) or next(iter(unit_map.values()))
    reconciled = all(s.reconciled for s in txn.splits)
    cur = conn.execute(
        "INSERT INTO operation (i_group_id, d_date, rd_account_id, r_payee_id, rc_unit_id, "
        "t_comment, t_status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            group_id,
            txn.date,
            account_ids[account.name],
            payee_id,
            unit_id,
            txn.group_title or txn.splits[0].notes or "",
            "Y" if reconciled else "N",
        ),
    )
    op_id = int(cur.lastrowid or 0)
    for order, split in enumerate(txn.splits):
        conn.execute(
            "INSERT INTO suboperation (rd_operation_id, r_category_id, f_value, t_comment, "
            "r_refund_id, d_date, i_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                op_id,
                category_ids.get(split.category_name or "", 0),
                float(_booked(split, account)) * sign,
                split.notes,
                refund_ids.get(split.tags[0], 0) if split.tags else 0,
                txn.date,
                order,
            ),
        )
    return op_id


def _insert_operations(
    conn: sqlite3.Connection,
    data: PulledData,
    unit_map: dict[str, int],
    account_ids: dict[str, int],
    category_ids: dict[str, int],
    payee_ids: dict[str, int],
    refund_ids: dict[str, int],
) -> int:
    by_name = {a.name: a for a in data.accounts}
    count = 0

    def _pair(out_name: str, in_name: str) -> int:
        # Both sides are our accounts (loan payment/drawdown or transfer):
        # write two operations sharing i_group_id so Skrooge pairs them.
        out_id = _insert_one_operation(
            conn,
            txn,
            by_name[out_name],
            -1,
            0,
            0,
            unit_map,
            account_ids,
            category_ids,
            refund_ids,
        )
        conn.execute("UPDATE operation SET i_group_id=? WHERE id=?", (out_id, out_id))
        _insert_one_operation(
            conn,
            txn,
            by_name[in_name],
            1,
            0,
            out_id,
            unit_map,
            account_ids,
            category_ids,
            refund_ids,
        )
        return 2

    for txn in data.transactions:
        first = txn.splits[0]
        if txn.kind == "withdrawal" and first.source_name in by_name:
            if first.destination_name in by_name:
                count += _pair(first.source_name, first.destination_name)
                continue
            _insert_one_operation(
                conn,
                txn,
                by_name[first.source_name],
                -1,
                payee_ids.get(first.destination_name, 0),
                0,
                unit_map,
                account_ids,
                category_ids,
                refund_ids,
            )
            count += 1
        elif txn.kind == "deposit" and first.destination_name in by_name:
            if first.source_name in by_name:
                count += _pair(first.source_name, first.destination_name)
                continue
            _insert_one_operation(
                conn,
                txn,
                by_name[first.destination_name],
                1,
                payee_ids.get(first.source_name, 0),
                0,
                unit_map,
                account_ids,
                category_ids,
                refund_ids,
            )
            count += 1
        elif txn.kind == "transfer":
            out_id = 0
            if first.source_name in by_name:
                out_id = _insert_one_operation(
                    conn,
                    txn,
                    by_name[first.source_name],
                    -1,
                    0,
                    0,
                    unit_map,
                    account_ids,
                    category_ids,
                    refund_ids,
                )
                conn.execute("UPDATE operation SET i_group_id=? WHERE id=?", (out_id, out_id))
                count += 1
            if first.destination_name in by_name:
                _insert_one_operation(
                    conn,
                    txn,
                    by_name[first.destination_name],
                    1,
                    0,
                    out_id,
                    unit_map,
                    account_ids,
                    category_ids,
                    refund_ids,
                )
                count += 1
    return count


def _insert_budgets(
    conn: sqlite3.Connection,
    data: PulledData,
    category_ids: dict[str, int],
    warnings: list[str],
) -> int:
    count = 0
    for name, limits in data.budgets:
        cat_id = category_ids.get(name)
        if cat_id is None:
            warnings.append(f"budget '{name}' has no matching category; skipped")
            continue
        for year, month, amount in limits:
            conn.execute(
                "INSERT INTO budget (rc_category_id, f_budgeted, i_year, i_month) "
                "VALUES (?, ?, ?, ?)",
                (cat_id, float(amount), year, month),
            )
            count += 1
    return count


def _write_balances(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT o.id, o.rd_account_id, o.t_status, COALESCE(SUM(s.f_value), 0) "
        "FROM operation o LEFT JOIN suboperation s ON s.rd_operation_id = o.id "
        "GROUP BY o.id ORDER BY o.rd_account_id, o.d_date, o.id"
    ).fetchall()
    balance: dict[int, float] = {}
    entered: dict[int, float] = {}
    for op_id, acct_id, status, total in rows:
        balance[acct_id] = balance.get(acct_id, 0.0) + total
        if status == "Y":
            entered[acct_id] = entered.get(acct_id, 0.0) + total
        conn.execute(
            "INSERT INTO operationbalance (r_operation_id, f_balance, f_balance_entered) "
            "VALUES (?, ?, ?)",
            (op_id, balance[acct_id], entered.get(acct_id, 0.0)),
        )


def write_skg(template: Path, output: Path, data: PulledData) -> SkgReport:
    """Write ``data`` into a copy of ``template`` at ``output``."""
    if not template.exists():
        raise FileNotFoundError(f"Skrooge template not found: {template}")
    shutil.copyfile(template, output)
    report = SkgReport()
    conn = sqlite3.connect(str(output))
    try:
        _clear(conn)
        currencies = {a.currency_code for a in data.accounts if a.currency_code}
        currencies |= {s.currency_code for t in data.transactions for s in t.splits}
        unit_map = _unit_map(conn, currencies)
        account_ids = _insert_accounts(conn, data.accounts)
        category_ids = _insert_categories(conn, data.transactions)
        payee_ids = _insert_payees(conn, data.accounts, data.transactions)
        refund_ids = _insert_refunds(conn, data.transactions)
        report.accounts = len(account_ids)
        report.operations = _insert_operations(
            conn, data, unit_map, account_ids, category_ids, payee_ids, refund_ids
        )
        report.budgets = _insert_budgets(conn, data, category_ids, report.warnings)
        _write_balances(conn)
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    return report
