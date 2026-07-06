import sqlite3
from decimal import Decimal
from pathlib import Path

from skrooge2firefly.export.puller import PulledData
from skrooge2firefly.export.skg import write_skg
from skrooge2firefly.model.entities import Account, Split, Transaction


def test_template_fixture_has_triggers_and_seed_data(skrooge_template: Path):
    conn = sqlite3.connect(str(skrooge_template))
    triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {"cpt_category_fullname1", "cpt_category_fullname2"} <= triggers
    assert conn.execute("SELECT COUNT(*) FROM operation").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM doctransaction").fetchone()[0] > 0
    conn.close()


def _acct(name, **kw):
    d = dict(
        external_id=f"firefly:acct:{name}",
        name=name,
        kind="asset",
        role="defaultAsset",
        currency_code="SEK",
        opening_balance=None,
        opening_balance_date=None,
        liability_type=None,
    )
    d.update(kw)
    return Account(**d)


def _data(accounts=(), transactions=(), budgets=()):
    return PulledData(list(accounts), list(transactions), list(budgets))


def test_write_skg_clears_template_data(skrooge_template, tmp_path):
    out = tmp_path / "out.sqlite"
    write_skg(skrooge_template, out, _data())
    conn = sqlite3.connect(str(out))
    for table in (
        "operation",
        "suboperation",
        "account",
        "payee",
        "doctransaction",
        "operationbalance",
    ):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert conn.execute("SELECT COUNT(*) FROM parameters").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM unit").fetchone()[0] >= 2


def test_write_skg_accounts_banks_and_types(skrooge_template, tmp_path):
    out = tmp_path / "o.sqlite"
    accounts = [
        _acct("Checking", notes="Bank: SEB"),
        _acct("Card", role="ccAsset"),
        _acct("Mortgage", kind="liability", role=None, liability_type="loan", active=False),
    ]
    write_skg(skrooge_template, out, _data(accounts))
    conn = sqlite3.connect(str(out))
    rows = dict(conn.execute("SELECT t_name, t_type FROM account"))
    assert rows == {"Checking": "C", "Card": "D", "Mortgage": "L"}
    assert (
        conn.execute(
            "SELECT b.t_name FROM account a JOIN bank b ON b.id=a.rd_bank_id "
            "WHERE a.t_name='Checking'"
        ).fetchone()[0]
        == "SEB"
    )
    assert conn.execute("SELECT t_close FROM account WHERE t_name='Mortgage'").fetchone()[0] == "Y"


def test_write_skg_categories_hierarchy_via_triggers(skrooge_template, tmp_path):
    out = tmp_path / "o2.sqlite"
    txn = Transaction(
        "x",
        "withdrawal",
        "2020-01-01",
        [Split(Decimal("1"), "SEK", "Checking", "ICA", category_name="Food > Groceries")],
    )
    write_skg(skrooge_template, out, _data([_acct("Checking")], [txn]))
    conn = sqlite3.connect(str(out))
    names = {r[0] for r in conn.execute("SELECT t_fullname FROM category")}
    assert "Food" in names and "Food > Groceries" in names


def test_write_skg_operations_splits_and_reconciled(skrooge_template, tmp_path):
    out = tmp_path / "ops.sqlite"
    txn = Transaction(
        "x",
        "withdrawal",
        "2020-05-01",
        [
            Split(
                Decimal("10"),
                "SEK",
                "Checking",
                "ICA",
                category_name="Food",
                reconciled=True,
                tags=("tracker1",),
            ),
            Split(Decimal("2.50"), "SEK", "Checking", "ICA", reconciled=True),
        ],
    )
    write_skg(skrooge_template, out, _data([_acct("Checking")], [txn]))
    conn = sqlite3.connect(str(out))
    [(op_id, date, status, payee)] = conn.execute(
        "SELECT o.id, o.d_date, o.t_status, p.t_name FROM operation o "
        "JOIN payee p ON p.id=o.r_payee_id"
    ).fetchall()
    assert (date, status, payee) == ("2020-05-01", "Y", "ICA")
    subs = conn.execute(
        "SELECT f_value, d_date, r_refund_id FROM suboperation ORDER BY i_order"
    ).fetchall()
    assert [s[0] for s in subs] == [-10.0, -2.5]
    assert all(s[1] == "2020-05-01" for s in subs)
    assert subs[0][2] > 0  # tracker1 linked


def test_write_skg_transfer_pairs_share_group_id(skrooge_template, tmp_path):
    out = tmp_path / "tr.sqlite"
    txn = Transaction(
        "t", "transfer", "2020-06-01", [Split(Decimal("500"), "SEK", "Checking", "Savings")]
    )
    write_skg(skrooge_template, out, _data([_acct("Checking"), _acct("Savings")], [txn]))
    conn = sqlite3.connect(str(out))
    rows = conn.execute(
        "SELECT a.t_name, s.f_value, o.i_group_id FROM operation o "
        "JOIN account a ON a.id=o.rd_account_id "
        "JOIN suboperation s ON s.rd_operation_id=o.id ORDER BY o.id"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("Checking", -500.0), ("Savings", 500.0)]
    assert rows[0][2] == rows[1][2] > 0


def test_write_skg_running_balances(skrooge_template, tmp_path):
    out = tmp_path / "bal.sqlite"
    txns = [
        Transaction(
            "a",
            "deposit",
            "2020-01-01",
            [Split(Decimal("100"), "SEK", "Employer", "Checking", reconciled=True)],
        ),
        Transaction(
            "b", "withdrawal", "2020-02-01", [Split(Decimal("30"), "SEK", "Checking", "ICA")]
        ),
    ]
    write_skg(skrooge_template, out, _data([_acct("Checking")], txns))
    conn = sqlite3.connect(str(out))
    rows = conn.execute(
        "SELECT f_balance, f_balance_entered FROM operationbalance "
        "JOIN operation ON operation.id=r_operation_id ORDER BY operation.d_date"
    ).fetchall()
    assert rows == [(100.0, 100.0), (70.0, 100.0)]


def test_write_skg_budget_matched_to_category(skrooge_template, tmp_path):
    out = tmp_path / "bud.sqlite"
    txn = Transaction(
        "x",
        "withdrawal",
        "2020-01-01",
        [Split(Decimal("1"), "SEK", "Checking", "ICA", category_name="Food")],
    )
    data = _data([_acct("Checking")], [txn], [("Food", [(2026, 1, Decimal("500"))])])
    report = write_skg(skrooge_template, out, data)
    conn = sqlite3.connect(str(out))
    [(cat_id, y, m, amt)] = conn.execute(
        "SELECT rc_category_id, i_year, i_month, f_budgeted FROM budget"
    ).fetchall()
    assert (y, m, amt) == (2026, 1, 500.0) and cat_id > 0
    assert report.budgets == 1
    out2 = tmp_path / "bud2.sqlite"
    data2 = _data([_acct("Checking")], [txn], [("Nope", [(2026, 1, Decimal("1"))])])
    report2 = write_skg(skrooge_template, out2, data2)
    assert report2.budgets == 0 and any("Nope" in w for w in report2.warnings)


def test_write_skg_clears_budget_tmp_and_tolerates_missing_tables(skrooge_template, tmp_path):
    """The real template has vm_budget_tmp/rule (FK-trigger-protected); minimal ones do not."""
    out = tmp_path / "vm.sqlite"
    write_skg(skrooge_template, out, _data())
    conn = sqlite3.connect(str(out))
    assert conn.execute("SELECT COUNT(*) FROM vm_budget_tmp").fetchone()[0] == 0
    conn.close()

    # a template without the optional tables must still work
    bare = tmp_path / "bare.sqlite"
    conn = sqlite3.connect(str(skrooge_template))
    conn.execute("DROP TABLE vm_budget_tmp")
    conn.commit()
    conn.close()
    write_skg(skrooge_template, bare, _data())


def test_write_skg_withdrawal_into_own_liability_pairs_operations(skrooge_template, tmp_path):
    out = tmp_path / "loan.sqlite"
    loan = _acct("Mortgage", kind="liability", role=None, liability_type="loan")
    txn = Transaction(
        "w", "withdrawal", "2015-01-01", [Split(Decimal("500"), "SEK", "Checking", "Mortgage")]
    )
    write_skg(skrooge_template, out, _data([_acct("Checking"), loan], [txn]))
    conn = sqlite3.connect(str(out))
    rows = conn.execute(
        "SELECT a.t_name, s.f_value, o.i_group_id FROM operation o "
        "JOIN account a ON a.id=o.rd_account_id "
        "JOIN suboperation s ON s.rd_operation_id=o.id ORDER BY o.id"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("Checking", -500.0), ("Mortgage", 500.0)]
    assert rows[0][2] == rows[1][2] > 0  # paired as a Skrooge transfer
