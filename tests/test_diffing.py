from decimal import Decimal

from skrooge2firefly.writers.diffing import norm_amount, norm_date, norm_str


def test_norm_date_strips_time_and_tz():
    assert norm_date("2020-05-01T00:00:00+02:00") == "2020-05-01"
    assert norm_date("2020-05-01") == "2020-05-01"


def test_norm_amount_compares_by_magnitude():
    assert norm_amount("12.000000") == norm_amount("12.00") == Decimal("12")


def test_norm_str_treats_none_as_empty():
    assert norm_str(None) == "" and norm_str("x") == "x"
