from decimal import Decimal

from skrooge2firefly.model.currency import iso_code


def test_known_symbols_map_to_iso():
    assert iso_code("€", "Euro") == "EUR"
    assert iso_code("$", "US Dollars") == "USD"
    assert iso_code("£", "British pound") == "GBP"


def test_symbols_already_iso_pass_through():
    assert iso_code("SEK", "Swedish kronor") == "SEK"
    assert iso_code("DKK", "Danish kronor") == "DKK"
    assert iso_code("BTC", "Bitcoin") == "BTC"


def test_unknown_symbol_is_uppercased_and_stripped():
    assert iso_code("xpf ", "Some currency") == "XPF"


# ── Historical FX conversion (Skrooge unitvalue rates are in primary currency) ──

from skrooge2firefly.model.currency import convert_amount, rate_in_primary  # noqa: E402

# rates: unit_id -> sorted [(date, price_in_primary_currency)]
_RATES = {
    2: [("2018-01-01", 10.0), ("2018-06-01", 11.0)],  # EUR in SEK
    3: [("2018-06-01", 9.0)],  # USD in SEK
    4: [("2018-05-15", 0.98)],  # NOK in SEK
}
_PRIMARY = 1  # SEK


def test_rate_primary_is_one():
    assert rate_in_primary(_RATES, 1, _PRIMARY, "2020-01-01") == 1.0


def test_rate_uses_nearest_date_at_or_before():
    assert rate_in_primary(_RATES, 2, _PRIMARY, "2018-03-01") == 10.0  # before the June bump
    assert rate_in_primary(_RATES, 2, _PRIMARY, "2018-09-01") == 11.0  # after
    # date before the first known rate falls back to the earliest
    assert rate_in_primary(_RATES, 2, _PRIMARY, "2010-01-01") == 10.0


def test_rate_missing_unit_returns_none():
    assert rate_in_primary(_RATES, 999, _PRIMARY, "2020-01-01") is None


def test_convert_foreign_to_primary():
    # 100 USD on 2018-06-05 -> 100 * 9.0 / 1 = 900 SEK
    assert convert_amount(Decimal("100"), 3, 1, _PRIMARY, _RATES, "2018-06-05") == Decimal("900")


def test_convert_between_two_foreign():
    # 100 USD -> EUR on 2018-06-05: 100 * 9.0 / 11.0
    got = convert_amount(Decimal("100"), 3, 2, _PRIMARY, _RATES, "2018-06-05")
    assert got is not None
    assert abs(got - (Decimal("100") * Decimal("9.0") / Decimal("11.0"))) < Decimal("1e-9")


def test_convert_missing_rate_returns_none():
    assert convert_amount(Decimal("100"), 999, 1, _PRIMARY, _RATES, "2020-01-01") is None


def test_convert_is_exact_decimal_no_float_noise():
    # 10 * 0.37654 in binary float is 3.7653999999999996; Decimal arithmetic
    # must produce the exact result instead.
    rates = {5: [("2018-01-01", 0.37654)]}
    got = convert_amount(Decimal("10"), 5, _PRIMARY, _PRIMARY, rates, "2018-06-05")
    assert got == Decimal("3.7654")
