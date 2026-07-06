from decimal import Decimal
from pathlib import Path

from skrooge2firefly.model.mapper import Mapper
from skrooge2firefly.skrooge.reader import SkroogeReader


def _setup(populate, db: Path) -> None:
    populate(
        db,
        "unit",
        [
            {"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"},
            {"id": 2, "t_name": "Euro", "t_symbol": "€", "t_type": "2"},
        ],
    )
    populate(
        db,
        "account",
        [
            {
                "id": 1,
                "t_name": "Checking",
                "t_type": "C",
                "f_importbalance": 0.0,
                "d_importdate": "2010-01-01",
            },
            {
                "id": 2,
                "t_name": "Savings",
                "t_type": "S",
                "f_importbalance": 0.0,
                "d_importdate": "2010-01-01",
            },
        ],
    )


def _map(db: Path) -> Mapper:
    with SkroogeReader(db) as reader:
        return Mapper.build(reader)


def test_two_op_pair_collapses_to_one_transfer(skrooge_db: Path, populate):
    _setup(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "i_group_id": 100,
                "d_date": "2020-05-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
            },
            {
                "id": 2,
                "i_group_id": 100,
                "d_date": "2020-05-01",
                "rd_account_id": 2,
                "rc_unit_id": 1,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -500.0},
            {"id": 2, "rd_operation_id": 2, "f_value": 500.0},
        ],
    )
    m = _map(skrooge_db)
    transfers = [t for t in m.transactions if t.kind == "transfer"]
    assert len(transfers) == 1
    s = transfers[0].splits[0]
    assert s.source_name == "Checking" and s.destination_name == "Savings"
    assert s.amount == Decimal("500.0")
    assert transfers[0].external_id == "skrooge:grp:100"


def test_cross_currency_transfer_sets_foreign_amount(skrooge_db: Path, populate):
    _setup(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "i_group_id": 101,
                "d_date": "2020-05-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
            },
            {
                "id": 2,
                "i_group_id": 101,
                "d_date": "2020-05-01",
                "rd_account_id": 2,
                "rc_unit_id": 2,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -1000.0},
            {"id": 2, "rd_operation_id": 2, "f_value": 90.0},
        ],
    )
    m = _map(skrooge_db)
    s = next(t for t in m.transactions if t.kind == "transfer").splits[0]
    assert s.amount == Decimal("1000.0") and s.currency_code == "SEK"
    assert s.foreign_amount == Decimal("90.0") and s.foreign_currency_code == "EUR"


def test_singleton_group_falls_back_to_normal_transaction(skrooge_db: Path, populate):
    _setup(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "i_group_id": 102,
                "d_date": "2020-05-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
            },
        ],
    )
    populate(skrooge_db, "suboperation", [{"id": 1, "rd_operation_id": 1, "f_value": -25.0}])
    m = _map(skrooge_db)
    assert any(t.external_id == "skrooge:op:1" and t.kind == "withdrawal" for t in m.transactions)
    assert any("group 102" in w for w in m.warnings)


def test_two_op_same_sign_pair_falls_back(skrooge_db: Path, populate) -> None:
    """Two ops in different accounts but BOTH negative → not opposite-signed → fallback."""
    _setup(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 10,
                "i_group_id": 200,
                "d_date": "2020-07-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
            },
            {
                "id": 11,
                "i_group_id": 200,
                "d_date": "2020-07-01",
                "rd_account_id": 2,
                "rc_unit_id": 1,
            },
        ],
    )
    # Both negative — not an opposite-signed pair
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 10, "rd_operation_id": 10, "f_value": -100.0},
            {"id": 11, "rd_operation_id": 11, "f_value": -50.0},
        ],
    )
    m = _map(skrooge_db)

    assert not any(t.kind == "transfer" for t in m.transactions)
    # Each op should emit as a normal withdrawal
    assert any(t.external_id == "skrooge:op:10" for t in m.transactions)
    assert any(t.external_id == "skrooge:op:11" for t in m.transactions)
    assert any("not an opposite-signed pair" in w for w in m.warnings)


def test_transfer_with_share_unit_becomes_security_expense(skrooge_db: Path, populate) -> None:
    """Opposite-signed pair where one op uses a share unit → the cash leg is
    recorded as a tagged 'security' expense/income (not skipped), so the cash
    account balance stays correct."""
    # Add a share unit in addition to the two currency units from _setup
    populate(
        skrooge_db,
        "unit",
        [
            {"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"},
            {"id": 2, "t_name": "Euro", "t_symbol": "€", "t_type": "2"},
            {"id": 3, "t_name": "AAPL", "t_symbol": "AAPL", "t_type": "S"},
        ],
    )
    populate(
        skrooge_db,
        "account",
        [
            {
                "id": 1,
                "t_name": "Checking",
                "t_type": "C",
                "f_importbalance": 0.0,
                "d_importdate": "2010-01-01",
            },
            {
                "id": 2,
                "t_name": "Portfolio",
                "t_type": "C",
                "f_importbalance": 0.0,
                "d_importdate": "2010-01-01",
            },
        ],
    )
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 20,
                "i_group_id": 300,
                "d_date": "2020-08-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,  # currency
            },
            {
                "id": 21,
                "i_group_id": 300,
                "d_date": "2020-08-01",
                "rd_account_id": 2,
                "rc_unit_id": 3,  # share unit
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 20, "rd_operation_id": 20, "f_value": -500.0},
            {"id": 21, "rd_operation_id": 21, "f_value": 500.0},
        ],
    )
    m = _map(skrooge_db)

    assert not any(t.kind == "transfer" for t in m.transactions)
    [txn] = [t for t in m.transactions if t.external_id == "skrooge:grp:300"]
    assert txn.kind == "withdrawal"
    [s] = txn.splits
    assert s.source_name == "Checking" and s.destination_name == "Securities"
    assert s.amount == Decimal("500.0") and "security" in s.tags


def test_same_currency_mismatched_legs_emitted_separately(skrooge_db: Path, populate) -> None:
    """Opposite-signed pair, same currency, materially different magnitudes →
    Skrooge's own data is inconsistent, so a single transfer can't carry both
    amounts. Emit each leg separately so BOTH accounts match their own amount."""
    _setup(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 30,
                "i_group_id": 400,
                "d_date": "2020-09-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
            },
            {
                "id": 31,
                "i_group_id": 400,
                "d_date": "2020-09-01",
                "rd_account_id": 2,
                "rc_unit_id": 1,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 30, "rd_operation_id": 30, "f_value": -100.0},
            {"id": 31, "rd_operation_id": 31, "f_value": 95.0},
        ],
    )
    m = _map(skrooge_db)

    assert not any(t.kind == "transfer" for t in m.transactions)
    # Checking loses its own 100, Savings gains its own 95 — each matches Skrooge.
    wd = next(t for t in m.transactions if t.external_id == "skrooge:op:30")
    dep = next(t for t in m.transactions if t.external_id == "skrooge:op:31")
    assert wd.kind == "withdrawal" and wd.splits[0].amount == Decimal("100.0")
    assert dep.kind == "deposit" and dep.splits[0].amount == Decimal("95.0")
    assert any("legs differ materially" in w for w in m.warnings)


def test_odd_group_falls_back_and_warns(skrooge_db: Path, populate):
    _setup(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "i_group_id": 103,
                "d_date": "2020-05-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
            },
            {
                "id": 2,
                "i_group_id": 103,
                "d_date": "2020-05-01",
                "rd_account_id": 2,
                "rc_unit_id": 1,
            },
            {
                "id": 3,
                "i_group_id": 103,
                "d_date": "2020-05-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -10.0},
            {"id": 2, "rd_operation_id": 2, "f_value": 10.0},
            {"id": 3, "rd_operation_id": 3, "f_value": -5.0},
        ],
    )
    m = _map(skrooge_db)
    assert sum(1 for t in m.transactions if t.external_id.startswith("skrooge:op:")) == 3
    assert not any(t.kind == "transfer" for t in m.transactions)
    assert any("group 103" in w for w in m.warnings)


def test_transfer_leg_currency_mismatching_account_falls_back(skrooge_db: Path, populate):
    """A USD-denominated leg into a majority-EUR account cannot be a Firefly
    transfer (foreign_amount must match the destination account currency), so
    the pair falls back to two normal operations."""
    populate(
        skrooge_db,
        "unit",
        [
            {"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"},
            {"id": 2, "t_name": "Bitcoin", "t_symbol": "BTC", "t_type": "2"},
            {"id": 3, "t_name": "Dollar", "t_symbol": "$", "t_type": "2"},
            {"id": 4, "t_name": "Euro", "t_symbol": "€", "t_type": "2"},
        ],
    )
    populate(
        skrooge_db,
        "account",
        [
            {"id": 1, "t_name": "BTC acct", "t_type": "C"},
            {"id": 2, "t_name": "Kraken", "t_type": "C"},
        ],
    )
    # Kraken's majority currency is EUR (two EUR ops), but the transfer leg is USD.
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 1, "i_group_id": 9, "d_date": "2021-11-16", "rd_account_id": 1, "rc_unit_id": 2},
            {"id": 2, "i_group_id": 9, "d_date": "2021-11-16", "rd_account_id": 2, "rc_unit_id": 3},
            {"id": 3, "d_date": "2021-01-01", "rd_account_id": 2, "rc_unit_id": 4},
            {"id": 4, "d_date": "2021-02-01", "rd_account_id": 2, "rc_unit_id": 4},
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -0.03},
            {"id": 2, "rd_operation_id": 2, "f_value": 155.1},
            {"id": 3, "rd_operation_id": 3, "f_value": -5.0},
            {"id": 4, "rd_operation_id": 4, "f_value": -6.0},
        ],
    )
    m = _map(skrooge_db)
    assert not any(t.external_id == "skrooge:grp:9" for t in m.transactions)
    assert any(t.external_id == "skrooge:op:1" for t in m.transactions)
    assert any(t.external_id == "skrooge:op:2" for t in m.transactions)
    assert any("group 9" in w and "currency" in w for w in m.warnings)


def _setup_with_loan(populate, db: Path) -> None:
    populate(db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(
        db,
        "account",
        [
            {"id": 1, "t_name": "Checking", "t_type": "C"},
            {"id": 2, "t_name": "Mortgage", "t_type": "L"},
        ],
    )


def test_transfer_into_liability_becomes_withdrawal(skrooge_db: Path, populate):
    """Firefly only transfers asset<->asset; paying a loan is a withdrawal."""
    _setup_with_loan(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "i_group_id": 50,
                "d_date": "2015-01-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
            },
            {
                "id": 2,
                "i_group_id": 50,
                "d_date": "2015-01-01",
                "rd_account_id": 2,
                "rc_unit_id": 1,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -500.0},
            {"id": 2, "rd_operation_id": 2, "f_value": 500.0},
        ],
    )
    m = _map(skrooge_db)
    [txn] = [t for t in m.transactions if t.external_id == "skrooge:grp:50"]
    assert txn.kind == "withdrawal"
    [s] = txn.splits
    assert s.source_name == "Checking" and s.destination_name == "Mortgage"
    assert s.amount == Decimal("500.0")


def test_transfer_out_of_liability_becomes_deposit(skrooge_db: Path, populate):
    _setup_with_loan(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "i_group_id": 60,
                "d_date": "2015-02-01",
                "rd_account_id": 2,
                "rc_unit_id": 1,
            },
            {
                "id": 2,
                "i_group_id": 60,
                "d_date": "2015-02-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -20000.0},
            {"id": 2, "rd_operation_id": 2, "f_value": 20000.0},
        ],
    )
    m = _map(skrooge_db)
    [txn] = [t for t in m.transactions if t.external_id == "skrooge:grp:60"]
    assert txn.kind == "deposit"
    [s] = txn.splits
    assert s.source_name == "Mortgage" and s.destination_name == "Checking"


def _share_setup(populate, db: Path) -> None:
    populate(
        db,
        "unit",
        [
            {"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"},
            {"id": 5, "t_name": "SEB Världenfund", "t_symbol": "SEB-V", "t_type": "S"},
        ],
    )
    populate(
        db,
        "account",
        [
            {"id": 1, "t_name": "ISK", "t_type": "C"},
            {"id": 2, "t_name": "Fonds", "t_type": "I"},
        ],
    )
    populate(db, "payee", [{"id": 5, "t_name": "Broker"}])


def test_fund_buy_becomes_tagged_security_withdrawal(skrooge_db: Path, populate):
    """A share purchase (cash out of ISK, shares into Fonds) must record the
    cash leg as a withdrawal so the ISK balance is correct, tagged 'security'
    for later reclassification when Firefly supports securities."""
    _share_setup(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "i_group_id": 500,
                "d_date": "2020-01-01",
                "rd_account_id": 1,
                "r_payee_id": 5,
                "rc_unit_id": 1,
                "t_comment": "Fund buy: SEB Världenfund",
            },
            {
                "id": 2,
                "i_group_id": 500,
                "d_date": "2020-01-01",
                "rd_account_id": 2,
                "r_payee_id": 5,
                "rc_unit_id": 5,
                "t_comment": "Fund buy: SEB Världenfund",
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -3000.0},
            {"id": 2, "rd_operation_id": 2, "f_value": 252.2958},
        ],
    )
    m = _map(skrooge_db)
    [txn] = [t for t in m.transactions if t.external_id == "skrooge:grp:500"]
    assert txn.kind == "withdrawal"
    [s] = txn.splits
    assert s.source_name == "ISK"
    assert s.destination_name == "Securities"
    assert s.amount == Decimal("3000.0")
    assert "security" in s.tags
    assert s.category_name == "Securities"
    assert "SEB-V" in s.notes and "252.2958" in s.notes  # reconstructable


def test_fund_sell_becomes_tagged_security_deposit(skrooge_db: Path, populate):
    _share_setup(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "i_group_id": 501,
                "d_date": "2021-06-01",
                "rd_account_id": 2,
                "r_payee_id": 5,
                "rc_unit_id": 5,
                "t_comment": "Fund sell",
            },
            {
                "id": 2,
                "i_group_id": 501,
                "d_date": "2021-06-01",
                "rd_account_id": 1,
                "r_payee_id": 5,
                "rc_unit_id": 1,
                "t_comment": "Fund sell",
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -100.0},
            {"id": 2, "rd_operation_id": 2, "f_value": 5000.0},
        ],
    )
    m = _map(skrooge_db)
    [txn] = [t for t in m.transactions if t.external_id == "skrooge:grp:501"]
    assert txn.kind == "deposit"
    [s] = txn.splits
    assert s.source_name == "Securities"
    assert s.destination_name == "ISK"
    assert s.amount == Decimal("5000.0")
    assert "security" in s.tags


def test_fictitious_zero_cash_share_group_emits_nothing(skrooge_db: Path, populate):
    """Skrooge's 0-value 'Opération fictive' revaluation legs must not produce
    a zero-amount transaction (Firefly rejects amount 0)."""
    _share_setup(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "i_group_id": 502,
                "d_date": "2020-01-01",
                "rd_account_id": 2,
                "rc_unit_id": 1,
            },
            {
                "id": 2,
                "i_group_id": 502,
                "d_date": "2020-01-01",
                "rd_account_id": 2,
                "rc_unit_id": 5,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": 0.0},
            {"id": 2, "rd_operation_id": 2, "f_value": -252.2958},
        ],
    )
    m = _map(skrooge_db)
    assert not any(t.external_id == "skrooge:grp:502" for t in m.transactions)


def test_foreign_currency_security_leg_is_fx_converted(skrooge_db: Path, populate) -> None:
    """A NOK-denominated stock buy in a SEK-dominant account is converted to
    SEK at Skrooge's historical rate, preserving the NOK original as
    foreign_amount — so the account balance is right and no data is lost."""
    populate(
        skrooge_db,
        "unit",
        [
            {"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"},
            {"id": 4, "t_name": "Norwegian krone", "t_symbol": "NOK", "t_type": "C"},
            {"id": 3, "t_name": "Nordea stock", "t_symbol": "NAS", "t_type": "S"},
        ],
    )
    populate(
        skrooge_db,
        "account",
        [{"id": 1, "t_name": "ISK", "t_type": "C"}, {"id": 2, "t_name": "Fonds", "t_type": "I"}],
    )
    # NOK price in SEK = 0.98 as of 2018-05-15 (so 1085 NOK -> 1063.30 SEK).
    populate(
        skrooge_db, "unitvalue", [{"rd_unit_id": 4, "d_date": "2018-05-15", "f_quantity": 0.98}]
    )
    populate(
        skrooge_db,
        "operation",
        [
            # Two SEK ops make SEK the account's dominant currency…
            {"id": 1, "d_date": "2018-01-01", "rd_account_id": 1, "rc_unit_id": 1},
            {"id": 2, "d_date": "2018-02-01", "rd_account_id": 1, "rc_unit_id": 1},
            # …then a NOK stock-buy transfer (cash NOK out of ISK, shares into Fonds).
            {
                "id": 3,
                "i_group_id": 90,
                "d_date": "2018-06-25",
                "rd_account_id": 1,
                "rc_unit_id": 4,
            },
            {
                "id": 4,
                "i_group_id": 90,
                "d_date": "2018-06-25",
                "rd_account_id": 2,
                "rc_unit_id": 3,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": 500.0},
            {"id": 2, "rd_operation_id": 2, "f_value": 500.0},
            {"id": 3, "rd_operation_id": 3, "f_value": -1085.0},
            {"id": 4, "rd_operation_id": 4, "f_value": 7.0},
        ],
    )
    m = _map(skrooge_db)
    sec = next(t for t in m.transactions if t.external_id == "skrooge:grp:90")
    assert sec.kind == "withdrawal"
    s = sec.splits[0]
    assert s.currency_code == "SEK"
    assert s.amount == Decimal("1063.30")  # 1085 * 0.98
    assert s.foreign_amount == Decimal("1085.0") and s.foreign_currency_code == "NOK"
    assert "security" in s.tags
