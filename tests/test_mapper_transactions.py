from decimal import Decimal
from pathlib import Path

from skrooge2firefly.model.mapper import Mapper
from skrooge2firefly.skrooge.reader import SkroogeReader


def _base(populate, db: Path) -> None:
    populate(
        db,
        "unit",
        [
            {"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"},
            {"id": 9, "t_name": "Avanza Global", "t_symbol": "Av-Glob", "t_type": "S"},
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
                "t_name": "Brokerage",
                "t_type": "I",
                "f_importbalance": 0.0,
                "d_importdate": "2010-01-01",
            },
        ],
    )
    populate(db, "payee", [{"id": 5, "t_name": "ICA"}, {"id": 6, "t_name": "Employer"}])
    populate(
        db,
        "category",
        [
            {"id": 7, "t_name": "Groceries", "t_fullname": "Food > Groceries"},
            {"id": 8, "t_name": "Salary", "t_fullname": "Income > Salary"},
        ],
    )
    populate(db, "refund", [{"id": 3, "t_name": "Trip 2020"}])


def _map(db: Path) -> Mapper:
    with SkroogeReader(db) as reader:
        return Mapper.build(reader)


def test_simple_withdrawal(skrooge_db: Path, populate):
    _base(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "d_date": "2020-05-01",
                "rd_account_id": 1,
                "r_payee_id": 5,
                "rc_unit_id": 1,
                "t_status": "Y",
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "r_category_id": 7, "f_value": -12.5},
        ],
    )
    m = _map(skrooge_db)
    txn = next(t for t in m.transactions if t.external_id == "skrooge:op:1")
    assert txn.kind == "withdrawal"
    s = txn.splits[0]
    assert s.amount == Decimal("12.5")
    assert s.source_name == "Checking" and s.destination_name == "ICA"
    assert s.category_name == "Food > Groceries"
    assert s.reconciled is True


def test_simple_deposit(skrooge_db: Path, populate):
    _base(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 2, "d_date": "2020-05-02", "rd_account_id": 1, "r_payee_id": 6, "rc_unit_id": 1},
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 2, "rd_operation_id": 2, "r_category_id": 8, "f_value": 1000.0},
        ],
    )
    m = _map(skrooge_db)
    txn = next(t for t in m.transactions if t.external_id == "skrooge:op:2")
    assert txn.kind == "deposit"
    assert txn.splits[0].source_name == "Employer"
    assert txn.splits[0].destination_name == "Checking"


def test_same_sign_split_is_one_group(skrooge_db: Path, populate):
    _base(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 3, "d_date": "2020-05-03", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 1},
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 3, "rd_operation_id": 3, "r_category_id": 7, "f_value": -10.0, "i_order": 0},
            {"id": 4, "rd_operation_id": 3, "r_category_id": 8, "f_value": -5.0, "i_order": 1},
        ],
    )
    m = _map(skrooge_db)
    txns = [t for t in m.transactions if t.external_id.startswith("skrooge:op:3")]
    assert len(txns) == 1
    assert len(txns[0].splits) == 2


def test_mixed_sign_split_becomes_two_groups(skrooge_db: Path, populate):
    _base(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 4, "d_date": "2020-05-04", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 1},
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 5, "rd_operation_id": 4, "r_category_id": 7, "f_value": -30.0, "i_order": 0},
            {"id": 6, "rd_operation_id": 4, "r_category_id": 8, "f_value": 10.0, "i_order": 1},
        ],
    )
    m = _map(skrooge_db)
    kinds = sorted(t.kind for t in m.transactions if t.external_id.startswith("skrooge:op:4"))
    assert kinds == ["deposit", "withdrawal"]


def test_refund_becomes_tag(skrooge_db: Path, populate):
    _base(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 5, "d_date": "2020-05-05", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 1},
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 7, "rd_operation_id": 5, "r_category_id": 7, "f_value": -8.0, "r_refund_id": 3},
        ],
    )
    m = _map(skrooge_db)
    txn = next(t for t in m.transactions if t.external_id == "skrooge:op:5")
    assert "Trip 2020" in txn.splits[0].tags


def test_share_denominated_operation_skipped(skrooge_db: Path, populate):
    _base(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 6, "d_date": "2020-05-06", "rd_account_id": 2, "r_payee_id": 0, "rc_unit_id": 9},
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 8, "rd_operation_id": 6, "r_category_id": 0, "f_value": 3.0},
        ],
    )
    m = _map(skrooge_db)
    assert not any(t.external_id.startswith("skrooge:op:6") for t in m.transactions)
    assert any("share-denominated" in w for w in m.warnings)


def test_template_operation_not_imported(skrooge_db: Path, populate):
    _base(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 10,
                "d_date": "2020-05-10",
                "rd_account_id": 1,
                "r_payee_id": 5,
                "rc_unit_id": 1,
                "t_template": "Y",
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 11, "rd_operation_id": 10, "r_category_id": 7, "f_value": -8.0},
        ],
    )
    m = _map(skrooge_db)
    assert not any(t.external_id.startswith("skrooge:op:10") for t in m.transactions)


def test_undated_operation_skipped(skrooge_db: Path, populate):
    """A non-transfer, invalid-dated operation never becomes a transaction —
    when the account already has an explicit f_importbalance (as _base's
    account 1 does), that value wins and the op's own value is unused."""
    _base(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 11,
                "d_date": "0000-00-00",
                "rd_account_id": 1,
                "r_payee_id": 5,
                "rc_unit_id": 1,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 12, "rd_operation_id": 11, "r_category_id": 7, "f_value": -8.0},
        ],
    )
    m = _map(skrooge_db)
    assert not any(t.external_id.startswith("skrooge:op:11") for t in m.transactions)


def test_zero_value_operation_skipped(skrooge_db: Path, populate):
    _base(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 12,
                "d_date": "2020-05-12",
                "rd_account_id": 1,
                "r_payee_id": 5,
                "rc_unit_id": 1,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 13, "rd_operation_id": 12, "r_category_id": 7, "f_value": 0.0},
        ],
    )
    m = _map(skrooge_db)
    assert not any(t.external_id.startswith("skrooge:op:12") for t in m.transactions)


def test_subcent_only_operation_is_excluded(skrooge_db, populate):
    """Skrooge 'fake' float-artifact operations (1e-07 SEK) round to 0.00; skip them."""
    _base(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [{"id": 30, "d_date": "2021-08-17", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 1}],
    )
    populate(
        skrooge_db,
        "suboperation",
        [{"id": 31, "rd_operation_id": 30, "f_value": 1.284061e-07, "t_comment": "Fake operation"}],
    )
    m = _map(skrooge_db)
    assert not any(t.external_id.startswith("skrooge:op:30") for t in m.transactions)
    assert any("sub-cent" in w for w in m.warnings)


def test_subcent_split_dropped_but_real_splits_kept(skrooge_db, populate):
    """A real multi-split operation must survive one embedded float-artifact split."""
    _base(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [{"id": 40, "d_date": "2022-02-28", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 1}],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 41, "rd_operation_id": 40, "f_value": -42.25, "t_comment": "Spotify"},
            {"id": 42, "rd_operation_id": 40, "f_value": -1.544794941e-07, "t_comment": "Fake"},
            {"id": 43, "rd_operation_id": 40, "f_value": -210.0, "t_comment": "Bathroom"},
        ],
    )
    m = _map(skrooge_db)
    [txn] = [t for t in m.transactions if t.external_id.startswith("skrooge:op:40")]
    assert [str(s.amount) for s in txn.splits] == ["42.25", "210.0"]
