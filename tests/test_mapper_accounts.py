from decimal import Decimal
from pathlib import Path

from skrooge2firefly.model.mapper import Mapper
from skrooge2firefly.skrooge.reader import SkroogeReader


def _map(db: Path) -> Mapper:
    with SkroogeReader(db) as reader:
        return Mapper.build(reader)


def test_currencies_include_used_currency_units_with_base(skrooge_db: Path, populate):
    populate(
        skrooge_db,
        "unit",
        [
            {"id": 1, "t_name": "Swedish kronor", "t_symbol": "SEK", "t_type": "1"},
            {"id": 2, "t_name": "Euro", "t_symbol": "€", "t_type": "2"},
            {"id": 3, "t_name": "Avanza Global", "t_symbol": "Av-Glob", "t_type": "S"},
        ],
    )
    m = _map(skrooge_db)
    codes = {c.code: c for c in m.currencies}
    assert "SEK" in codes and codes["SEK"].primary is True
    assert "EUR" in codes
    assert "AV-GLOB" not in codes  # shares are not currencies


def test_account_types_and_roles(skrooge_db: Path, populate):
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(
        skrooge_db,
        "account",
        [
            {
                "id": 1,
                "t_name": "Checking",
                "t_type": "C",
                "f_importbalance": 100.0,
                "d_importdate": "2010-01-01",
            },
            {
                "id": 2,
                "t_name": "Savings",
                "t_type": "S",
                "f_importbalance": 0.0,
                "d_importdate": "2010-01-01",
            },
            {
                "id": 3,
                "t_name": "Wallet",
                "t_type": "W",
                "f_importbalance": 5.0,
                "d_importdate": "2010-01-01",
            },
            {
                "id": 4,
                "t_name": "Mortgage",
                "t_type": "L",
                "f_importbalance": -200.0,
                "d_importdate": "2010-01-01",
                "rd_bank_id": 0,
            },
        ],
    )
    m = _map(skrooge_db)
    accts = {a.name: a for a in m.accounts}
    assert accts["Checking"].kind == "asset" and accts["Checking"].role == "defaultAsset"
    assert accts["Savings"].role == "savingAsset"
    assert accts["Wallet"].role == "cashWalletAsset"
    assert accts["Mortgage"].kind == "liability" and accts["Mortgage"].liability_type == "loan"
    assert accts["Checking"].opening_balance == Decimal("100.0")
    assert accts["Checking"].external_id == "skrooge:acct:1"


def test_closed_skrooge_account_maps_to_inactive(skrooge_db, populate):
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(
        skrooge_db,
        "account",
        [
            {
                "id": 1,
                "t_name": "Open",
                "t_type": "C",
                "t_close": "N",
                "f_importbalance": "",
                "d_importdate": "",
            },
            {
                "id": 2,
                "t_name": "Closed",
                "t_type": "C",
                "t_close": "Y",
                "f_importbalance": "",
                "d_importdate": "",
            },
        ],
    )
    m = _map(skrooge_db)
    accts = {a.name: a for a in m.accounts}
    assert accts["Open"].active is True
    assert accts["Closed"].active is False


def test_account_currency_inferred_from_operations(skrooge_db, populate):
    populate(
        skrooge_db,
        "unit",
        [
            {"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"},
            {"id": 2, "t_name": "Danish kronor", "t_symbol": "DKK", "t_type": "C"},
        ],
    )
    populate(
        skrooge_db,
        "account",
        [
            {"id": 1, "t_name": "DkkAcc", "t_type": "C", "f_importbalance": "", "d_importdate": ""},
            {"id": 2, "t_name": "NoOps", "t_type": "C", "f_importbalance": "", "d_importdate": ""},
        ],
    )
    # DkkAcc has 3 DKK ops and 1 SEK op -> dominant DKK; NoOps has none -> primary SEK
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 1, "d_date": "2020-01-01", "rd_account_id": 1, "rc_unit_id": 2},
            {"id": 2, "d_date": "2020-01-02", "rd_account_id": 1, "rc_unit_id": 2},
            {"id": 3, "d_date": "2020-01-03", "rd_account_id": 1, "rc_unit_id": 2},
            {"id": 4, "d_date": "2020-01-04", "rd_account_id": 1, "rc_unit_id": 1},
        ],
    )
    m = _map(skrooge_db)
    accts = {a.name: a for a in m.accounts}
    assert accts["DkkAcc"].currency_code == "DKK"
    assert accts["NoOps"].currency_code == "SEK"


def test_credit_card_type_maps_to_cc_asset(skrooge_db, populate):
    """Skrooge type 'D' is a credit card, not a savings account."""
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(
        skrooge_db,
        "account",
        [{"id": 1, "t_name": "Norwegian CC", "t_type": "D"}],
    )
    m = _map(skrooge_db)
    [acct] = m.accounts
    assert acct.kind == "asset" and acct.role == "ccAsset"


def test_opening_balance_derived_from_zero_date_operation(skrooge_db, populate):
    """Skrooge encodes an account's opening balance as a same-currency
    operation dated 0000-00-00 (when f_importbalance is empty); its value
    must become Account.opening_balance, dated at the earliest real
    transaction (Firefly needs a real date for the opening-balance entry)."""
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(skrooge_db, "account", [{"id": 1, "t_name": "Loan", "t_type": "L"}])
    populate(skrooge_db, "payee", [{"id": 5, "t_name": "Bank"}])
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 1, "d_date": "0000-00-00", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 1},
            {"id": 2, "d_date": "2020-06-01", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 1},
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -1355000.0},
            {"id": 2, "rd_operation_id": 2, "f_value": 5000.0},
        ],
    )
    m = _map(skrooge_db)
    [acct] = m.accounts
    assert acct.opening_balance == Decimal("-1355000.0")
    assert acct.opening_balance_date == "2020-06-01"
    # the placeholder op itself must not become a transaction
    assert len(m.transactions) == 1
    assert m.transactions[0].date == "2020-06-01"


def test_opening_balance_zero_date_op_no_other_history_uses_fallback_date(skrooge_db, populate):
    """An account whose ONLY operation is the 0000-00-00 placeholder (no real
    transactions at all) still needs a valid date for Firefly."""
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(skrooge_db, "account", [{"id": 1, "t_name": "Loan 2", "t_type": "L"}])
    populate(
        skrooge_db,
        "operation",
        [{"id": 1, "d_date": "0000-00-00", "rd_account_id": 1, "rc_unit_id": 1}],
    )
    populate(skrooge_db, "suboperation", [{"id": 1, "rd_operation_id": 1, "f_value": -1355000.0}])
    m = _map(skrooge_db)
    [acct] = m.accounts
    assert acct.opening_balance == Decimal("-1355000.0")
    assert acct.opening_balance_date is not None
    assert len(m.transactions) == 0


def test_explicit_importbalance_takes_priority_over_zero_date_op(skrooge_db, populate):
    """f_importbalance, when set, is the authoritative opening balance."""
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(
        skrooge_db,
        "account",
        [
            {
                "id": 1,
                "t_name": "Checking",
                "t_type": "C",
                "f_importbalance": 42.0,
                "d_importdate": "2015-01-01",
            }
        ],
    )
    populate(
        skrooge_db,
        "operation",
        [{"id": 1, "d_date": "0000-00-00", "rd_account_id": 1, "rc_unit_id": 1}],
    )
    populate(skrooge_db, "suboperation", [{"id": 1, "rd_operation_id": 1, "f_value": -999.0}])
    m = _map(skrooge_db)
    [acct] = m.accounts
    assert acct.opening_balance == Decimal("42.0")
    assert acct.opening_balance_date == "2015-01-01"


def test_zero_value_placeholder_op_leaves_opening_balance_unset(skrooge_db, populate):
    """The ~68 harmless zero-value placeholders shouldn't force a spurious
    opening_balance=0 payload onto every account."""
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(skrooge_db, "account", [{"id": 1, "t_name": "Checking", "t_type": "C"}])
    populate(
        skrooge_db,
        "operation",
        [{"id": 1, "d_date": "0000-00-00", "rd_account_id": 1, "rc_unit_id": 1}],
    )
    populate(skrooge_db, "suboperation", [{"id": 1, "rd_operation_id": 1, "f_value": 0.0}])
    m = _map(skrooge_db)
    [acct] = m.accounts
    assert acct.opening_balance is None
