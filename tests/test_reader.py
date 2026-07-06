from pathlib import Path

from skrooge2firefly.skrooge.reader import SkroogeReader


def test_reader_opens_readonly_and_reads_units(skrooge_db: Path, populate):
    populate(
        skrooge_db,
        "unit",
        [
            {
                "id": 1,
                "t_name": "Swedish kronor",
                "t_symbol": "SEK",
                "t_type": "1",
                "i_nbdecimal": 2,
            },
            {"id": 2, "t_name": "Euro", "t_symbol": "€", "t_type": "2", "i_nbdecimal": 2},
        ],
    )
    with SkroogeReader(skrooge_db) as reader:
        units = {u.id: u for u in reader.units()}
    assert units[1].symbol == "SEK"
    assert units[1].is_primary is True
    assert units[2].is_currency is True


def test_reader_coerces_empty_import_balance_to_none(skrooge_db: Path, populate):
    # Real Skrooge exports store f_importbalance/d_importdate as TEXT and often
    # leave them empty; the reader must not choke on that.
    populate(
        skrooge_db,
        "account",
        [
            {"id": 1, "t_name": "Empty", "t_type": "C", "f_importbalance": "", "d_importdate": ""},
            {
                "id": 2,
                "t_name": "Numeric",
                "t_type": "C",
                "f_importbalance": "123.45",
                "d_importdate": "2010-01-01",
            },
        ],
    )
    with SkroogeReader(skrooge_db) as reader:
        accts = {a.id: a for a in reader.accounts()}
    assert accts[1].import_balance is None
    assert accts[1].import_date is None
    assert accts[2].import_balance == 123.45
    assert accts[2].import_date == "2010-01-01"


def test_reader_categories_and_suboperations(skrooge_db: Path, populate):
    populate(
        skrooge_db,
        "category",
        [
            {"id": 7, "t_name": "Groceries", "t_fullname": "Food > Groceries"},
        ],
    )
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 1, "d_date": "2020-05-01", "rd_account_id": 1, "rc_unit_id": 1},
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "r_category_id": 7, "f_value": -12.5},
        ],
    )
    with SkroogeReader(skrooge_db) as reader:
        assert reader.categories()[7] == "Food > Groceries"
        subs = reader.suboperations_by_operation()
        assert subs[1][0].value == -12.5


def test_reader_is_readonly(skrooge_db: Path):
    import sqlite3

    import pytest

    with SkroogeReader(skrooge_db) as reader:
        with pytest.raises(sqlite3.OperationalError):
            reader._conn.execute("INSERT INTO bank (id, t_name) VALUES (99, 'x')")


def _audit_setup(populate, db):
    populate(
        db,
        "unit",
        [
            {"id": 1, "t_name": "Swedish kronor", "t_symbol": "SEK", "t_type": "1"},
            {"id": 2, "t_name": "Euro", "t_symbol": "€", "t_type": "2"},
            {"id": 3, "t_name": "Some Fund", "t_symbol": "FND", "t_type": "S"},
        ],
    )
    populate(
        db,
        "account",
        [
            {"id": 1, "t_name": "Checking", "t_type": "C"},
            {"id": 2, "t_name": "Loan", "t_type": "L"},
            {"id": 3, "t_name": "Invest", "t_type": "I"},
            {"id": 4, "t_name": "MultiCur", "t_type": "C"},
        ],
    )


def test_account_audits_cash_balance_includes_opening_placeholder(skrooge_db, populate):
    """Skrooge's raw cash balance sums ALL non-template cash suboperations,
    including the 0000-00-00 opening-balance placeholder — this is the
    independent source-of-truth number verification compares to Firefly."""
    _audit_setup(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 1, "d_date": "0000-00-00", "rd_account_id": 2, "rc_unit_id": 1},
            {"id": 2, "d_date": "2020-01-01", "rd_account_id": 2, "rc_unit_id": 1},
            {"id": 3, "d_date": "2020-02-01", "rd_account_id": 1, "rc_unit_id": 1},
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -1000.0},
            {"id": 2, "rd_operation_id": 2, "f_value": 200.0},
            {"id": 3, "rd_operation_id": 3, "f_value": 50.0},
        ],
    )
    with SkroogeReader(skrooge_db) as reader:
        audits = reader.account_audits()
    assert audits["Loan"].cash_balance == -800.0  # -1000 opening + 200
    assert audits["Loan"].has_shares is False
    assert audits["Checking"].cash_balance == 50.0


def test_account_audits_flags_shares_via_transfer_group(skrooge_db, populate):
    """An account whose share leg lives in a separate account (share purchase
    modeled as a transfer group) must still be flagged share-involved."""
    _audit_setup(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            # transfer group 100: cash leaves Checking, shares arrive in Invest
            {
                "id": 1,
                "i_group_id": 100,
                "d_date": "2020-01-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
            },
            {
                "id": 2,
                "i_group_id": 100,
                "d_date": "2020-01-01",
                "rd_account_id": 3,
                "rc_unit_id": 3,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -500.0},
            {"id": 2, "rd_operation_id": 2, "f_value": 5.0},
        ],
    )
    with SkroogeReader(skrooge_db) as reader:
        audits = reader.account_audits()
    assert audits["Checking"].has_shares is True  # cash leg of a share transfer
    assert audits["Invest"].has_shares is True  # holds the share op directly


def test_account_audits_flags_mixed_currency_and_legs_differ(skrooge_db, populate):
    _audit_setup(populate, skrooge_db)
    populate(
        skrooge_db,
        "operation",
        [
            # MultiCur holds both SEK and EUR ops -> mixed
            {"id": 1, "d_date": "2020-01-01", "rd_account_id": 4, "rc_unit_id": 1},
            {"id": 2, "d_date": "2020-01-02", "rd_account_id": 4, "rc_unit_id": 2},
            # transfer group 200 between Checking & Loan with unequal same-currency legs
            {
                "id": 3,
                "i_group_id": 200,
                "d_date": "2020-03-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
            },
            {
                "id": 4,
                "i_group_id": 200,
                "d_date": "2020-03-01",
                "rd_account_id": 2,
                "rc_unit_id": 1,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": 100.0},
            {"id": 2, "rd_operation_id": 2, "f_value": 50.0},
            {"id": 3, "rd_operation_id": 3, "f_value": -445268.0},
            {"id": 4, "rd_operation_id": 4, "f_value": 445000.0},
        ],
    )
    with SkroogeReader(skrooge_db) as reader:
        audits = reader.account_audits()
    assert audits["MultiCur"].mixed_currency is True
    assert audits["Checking"].legs_differ is True
    assert audits["Loan"].legs_differ is True


def test_reader_strips_whitespace_from_payee_and_account_names(skrooge_db, populate):
    """Skrooge sometimes stores stray leading/trailing spaces in names; Firefly
    trims them on account creation, so we normalise at read time to keep the
    two exactly in step (and avoid near-duplicate accounts)."""
    populate(
        skrooge_db,
        "payee",
        [{"id": 1, "t_name": "Sostrene Grene "}, {"id": 2, "t_name": " Planta"}],
    )
    populate(skrooge_db, "account", [{"id": 1, "t_name": "Industriv A ", "t_type": "C"}])
    with SkroogeReader(skrooge_db) as reader:
        assert reader.payees() == {1: "Sostrene Grene", 2: "Planta"}
        assert reader.accounts()[0].name == "Industriv A"


def test_payees_differing_only_by_accent_collapse_to_accented_spelling(skrooge_db, populate):
    """Firefly account matching is accent-insensitive, so 'Ahlens' and 'Åhlens'
    become one account. We canonicalise such pairs to the richer (accented)
    spelling so both Skrooge payees map to the same, nicer Firefly name."""
    populate(
        skrooge_db, "payee", [{"id": 334, "t_name": "Ahlens"}, {"id": 691, "t_name": "Åhlens"}]
    )
    with SkroogeReader(skrooge_db) as reader:
        p = reader.payees()
    assert p[334] == "Åhlens" and p[691] == "Åhlens"


def test_distinct_cjk_payees_are_not_merged(skrooge_db, populate):
    """Accent folding must strip only combining marks, never whole scripts —
    otherwise distinct CJK names (no ASCII form) would all collapse to one."""
    populate(
        skrooge_db,
        "payee",
        [
            {"id": 1, "t_name": "全聯"},
            {"id": 2, "t_name": "森氏咖啡所"},
            {"id": 3, "t_name": "三分春色"},
        ],
    )
    with SkroogeReader(skrooge_db) as reader:
        p = reader.payees()
    assert p[1] == "全聯" and p[2] == "森氏咖啡所" and p[3] == "三分春色"
    assert len({p[1], p[2], p[3]}) == 3
