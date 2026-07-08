import calendar
from decimal import Decimal
from pathlib import Path

from skrooge2firefly.model.mapper import Mapper
from skrooge2firefly.skrooge.reader import SkroogeReader


def _map(db: Path) -> Mapper:
    with SkroogeReader(db) as reader:
        return Mapper.build(reader)


def test_budgets_grouped_by_category_with_month_limits(skrooge_db: Path, populate):
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(skrooge_db, "category", [{"id": 7, "t_name": "Food", "t_fullname": "Food"}])
    populate(
        skrooge_db,
        "budget",
        [
            {"rc_category_id": 7, "i_year": 2020, "i_month": 1, "f_budgeted": -500.0},
            {"rc_category_id": 7, "i_year": 2020, "i_month": 2, "f_budgeted": -450.0},
        ],
    )
    m = _map(skrooge_db)
    food = next(b for b in m.budgets if b.name == "Food")
    assert len(food.limits) == 2
    jan = next(limit for limit in food.limits if limit.start == "2020-01-01")
    last = calendar.monthrange(2020, 1)[1]
    assert jan.end == f"2020-01-{last:02d}"
    assert jan.amount == Decimal("500.0")  # absolute value


def test_recurrence_mapped_from_template(skrooge_db: Path, populate):
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
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
        ],
    )
    populate(skrooge_db, "payee", [{"id": 5, "t_name": "Landlord"}])
    populate(skrooge_db, "category", [{"id": 7, "t_name": "Rent", "t_fullname": "Housing > Rent"}])
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 1, "d_date": "2020-05-01", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 1},
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "r_category_id": 7, "f_value": -800.0},
        ],
    )
    populate(
        skrooge_db,
        "recurrentoperation",
        [
            {
                "id": 1,
                "rd_operation_id": 1,
                "d_date": "2020-06-01",
                "i_period_increment": 1,
                "t_period_unit": "M",
            },
        ],
    )
    m = _map(skrooge_db)
    rec = m.recurrences[0]
    assert rec.kind == "withdrawal"
    assert rec.amount == Decimal("800.0")
    assert rec.repetition_type == "monthly"
    assert rec.source_name == "Checking" and rec.destination_name == "Landlord"
    assert rec.external_id == "skrooge:rec:1"


def test_budget_skips_yearly_aggregate_and_orphan_category(skrooge_db: Path, populate):
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(skrooge_db, "category", [{"id": 7, "t_name": "Food", "t_fullname": "Food"}])
    populate(
        skrooge_db,
        "budget",
        [
            {"rc_category_id": 7, "i_year": 2020, "i_month": 0, "f_budgeted": -6000.0},
            {"rc_category_id": 999, "i_year": 2020, "i_month": 3, "f_budgeted": -100.0},
            {"rc_category_id": 7, "i_year": 2020, "i_month": 3, "f_budgeted": -500.0},
        ],
    )
    m = _map(skrooge_db)
    food = next(b for b in m.budgets if b.name == "Food")
    assert len(food.limits) == 1
    assert food.limits[0].start == "2020-03-01"
    assert all(b.name != "999" for b in m.budgets)
    assert len(m.budgets) == 1


def test_recurrence_zero_amount_skipped(skrooge_db: Path, populate):
    """A recurrence whose template suboperations net to zero is dropped (Fix A1)."""
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
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
        ],
    )
    populate(skrooge_db, "payee", [{"id": 5, "t_name": "Shop"}])
    populate(skrooge_db, "category", [{"id": 7, "t_name": "Food", "t_fullname": "Food"}])
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 1, "d_date": "2020-05-01", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 1},
        ],
    )
    # Two suboperations that cancel each other out → net zero
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "r_category_id": 7, "f_value": 100.0},
            {"id": 2, "rd_operation_id": 1, "r_category_id": 7, "f_value": -100.0},
        ],
    )
    populate(
        skrooge_db,
        "recurrentoperation",
        [
            {
                "id": 1,
                "rd_operation_id": 1,
                "d_date": "2020-06-01",
                "i_period_increment": 1,
                "t_period_unit": "M",
            },
        ],
    )
    m = _map(skrooge_db)
    assert len(m.recurrences) == 0, "Zero-amount recurrence must be skipped"


def test_recurrence_with_missing_account_is_skipped_with_warning(skrooge_db: Path, populate):
    """A recurrence's underlying operation can reference an account id that no
    longer exists (no FK enforcement in Skrooge's schema); the import must not
    crash — the recurrence is skipped and warned about instead (Fix A)."""
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(skrooge_db, "payee", [{"id": 5, "t_name": "Landlord"}])
    populate(skrooge_db, "category", [{"id": 7, "t_name": "Rent", "t_fullname": "Housing > Rent"}])
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "d_date": "2020-05-01",
                "rd_account_id": 999,  # no such account
                "r_payee_id": 5,
                "rc_unit_id": 1,
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [{"id": 1, "rd_operation_id": 1, "r_category_id": 7, "f_value": -800.0}],
    )
    populate(
        skrooge_db,
        "recurrentoperation",
        [
            {
                "id": 1,
                "rd_operation_id": 1,
                "d_date": "2020-06-01",
                "i_period_increment": 1,
                "t_period_unit": "M",
            },
        ],
    )
    m = _map(skrooge_db)  # must not raise
    assert len(m.recurrences) == 0
    assert any("Recurrence 1" in w and "unknown account 999" in w for w in m.warnings)


def test_recurrence_unresolved_unit_uses_primary_currency(skrooge_db: Path, populate):
    """An operation with rc_unit_id=0 (unresolved) uses the primary currency (Fix A2)."""
    populate(
        skrooge_db,
        "unit",
        [{"id": 1, "t_name": "Swedish Krona", "t_symbol": "SEK", "t_type": "1"}],
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
        ],
    )
    populate(skrooge_db, "payee", [{"id": 5, "t_name": "Shop"}])
    populate(skrooge_db, "category", [{"id": 7, "t_name": "Food", "t_fullname": "Food"}])
    # rc_unit_id = 0 → unit not found → should fall back to primary (SEK)
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 1, "d_date": "2020-05-01", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 0},
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "r_category_id": 7, "f_value": -200.0},
        ],
    )
    populate(
        skrooge_db,
        "recurrentoperation",
        [
            {
                "id": 1,
                "rd_operation_id": 1,
                "d_date": "2020-06-01",
                "i_period_increment": 1,
                "t_period_unit": "M",
            },
        ],
    )
    m = _map(skrooge_db)
    assert len(m.recurrences) == 1
    assert m.recurrences[0].currency_code == "SEK", (
        "Unresolved unit should fall back to primary currency SEK, not EUR"
    )


def test_recurrence_weekly_period_and_skip_arithmetic(skrooge_db: Path, populate):
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
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
        ],
    )
    populate(skrooge_db, "payee", [{"id": 5, "t_name": "Gym"}])
    populate(skrooge_db, "category", [{"id": 7, "t_name": "Health", "t_fullname": "Health"}])
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 1, "d_date": "2020-05-01", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 1},
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "r_category_id": 7, "f_value": -50.0},
        ],
    )
    populate(
        skrooge_db,
        "recurrentoperation",
        [
            {
                "id": 1,
                "rd_operation_id": 1,
                "d_date": "2020-05-08",
                "i_period_increment": 2,
                "t_period_unit": "W",
            },
        ],
    )
    m = _map(skrooge_db)
    rec = m.recurrences[0]
    assert rec.repetition_type == "weekly"
    assert rec.skip == 1


def test_recurrences_with_colliding_titles_are_disambiguated(skrooge_db, populate):
    """Firefly bills are matched by NAME (no external_id), so distinct Skrooge
    recurrences that share a title (e.g. several 'Insurance' entries with
    different amounts) must get unique bill names, or all but the first are
    silently dropped as 'already exists'."""
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(skrooge_db, "account", [{"id": 1, "t_name": "Checking", "t_type": "C"}])
    populate(skrooge_db, "payee", [{"id": 5, "t_name": "Insurer"}])
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "d_date": "2020-01-01",
                "rd_account_id": 1,
                "r_payee_id": 5,
                "rc_unit_id": 1,
                "t_comment": "Insurance",
            },
            {
                "id": 2,
                "d_date": "2020-01-01",
                "rd_account_id": 1,
                "r_payee_id": 5,
                "rc_unit_id": 1,
                "t_comment": "Insurance",
            },
            {
                "id": 3,
                "d_date": "2020-01-01",
                "rd_account_id": 1,
                "r_payee_id": 5,
                "rc_unit_id": 1,
                "t_comment": "Insurance",
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -276.0},
            {"id": 2, "rd_operation_id": 2, "f_value": -491.0},
            {"id": 3, "rd_operation_id": 3, "f_value": -193.0},
        ],
    )
    populate(
        skrooge_db,
        "recurrentoperation",
        [
            {
                "id": 1,
                "rd_operation_id": 1,
                "d_date": "2020-02-01",
                "i_period_increment": 1,
                "t_period_unit": "M",
            },
            {
                "id": 2,
                "rd_operation_id": 2,
                "d_date": "2020-02-01",
                "i_period_increment": 1,
                "t_period_unit": "M",
            },
            {
                "id": 3,
                "rd_operation_id": 3,
                "d_date": "2020-02-01",
                "i_period_increment": 1,
                "t_period_unit": "M",
            },
        ],
    )
    m = _map(skrooge_db)
    titles = [r.title for r in m.recurrences]
    assert len(m.recurrences) == 3
    assert len(set(titles)) == 3, f"bill titles must be unique, got {titles}"
    # the disambiguator keeps the base title and adds the distinguishing amount
    assert all(t.startswith("Insurance") for t in titles)


def test_recurrences_same_title_and_amount_get_index_suffix(skrooge_db, populate):
    """Even identical title AND amount must resolve to unique names."""
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(skrooge_db, "account", [{"id": 1, "t_name": "Checking", "t_type": "C"}])
    populate(
        skrooge_db,
        "operation",
        [
            {
                "id": 1,
                "d_date": "2020-01-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
                "t_comment": "Fee",
            },
            {
                "id": 2,
                "d_date": "2020-01-01",
                "rd_account_id": 1,
                "rc_unit_id": 1,
                "t_comment": "Fee",
            },
        ],
    )
    populate(
        skrooge_db,
        "suboperation",
        [
            {"id": 1, "rd_operation_id": 1, "f_value": -100.0},
            {"id": 2, "rd_operation_id": 2, "f_value": -100.0},
        ],
    )
    populate(
        skrooge_db,
        "recurrentoperation",
        [
            {
                "id": 1,
                "rd_operation_id": 1,
                "d_date": "2020-02-01",
                "i_period_increment": 1,
                "t_period_unit": "M",
            },
            {
                "id": 2,
                "rd_operation_id": 2,
                "d_date": "2020-02-01",
                "i_period_increment": 1,
                "t_period_unit": "M",
            },
        ],
    )
    m = _map(skrooge_db)
    titles = [r.title for r in m.recurrences]
    assert len(set(titles)) == 2, titles
