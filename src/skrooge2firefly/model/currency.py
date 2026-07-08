"""Map Skrooge currency symbols to ISO 4217 (or custom) currency codes."""

from decimal import Decimal

# Skrooge stores display symbols, not ISO codes; t_internet_code holds rate-pair
# strings (e.g. "EURSEK") and is unusable. Map the non-ISO symbols explicitly.
_SYMBOL_TO_ISO: dict[str, str] = {
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
    "¥": "JPY",
    "₣": "CHF",
}


def iso_code(symbol: str, name: str) -> str:
    """Return the currency code for a Skrooge unit.

    Args:
        symbol: The Skrooge unit symbol (e.g. ``"€"`` or ``"SEK"``).
        name: The Skrooge unit name, used only for diagnostics.

    Returns:
        An ISO 4217 code where known, otherwise the symbol uppercased and
        stripped (covers ISO-already symbols like ``SEK`` and crypto like
        ``BTC``).

    """
    symbol = symbol.strip()
    if symbol in _SYMBOL_TO_ISO:
        return _SYMBOL_TO_ISO[symbol]
    return symbol.upper()


def rate_in_primary(
    rates: dict[int, list[tuple[str, float]]],
    unit_id: int,
    primary_id: int,
    date: str,
) -> float | None:
    """Return the value of one ``unit_id`` in the primary currency on ``date``.

    Skrooge stores exchange rates in ``unitvalue`` as the price of one unit in
    the primary currency (e.g. 1 USD = 8.83 SEK). ``rates`` maps unit id to its
    ``(date, price)`` series sorted ascending. The primary currency is 1.0 by
    definition. For other units the most recent rate on or before ``date`` is
    used, falling back to the earliest known rate for dates before the series
    begins. Returns None when no rate is known for the unit.
    """
    if unit_id == primary_id:
        return 1.0
    series = rates.get(unit_id)
    if not series:
        return None
    chosen = series[0][1]
    for d, price in series:
        if d <= date:
            chosen = price
        else:
            break
    return chosen


def convert_amount(
    amount: Decimal,
    from_id: int,
    to_id: int,
    primary_id: int,
    rates: dict[int, list[tuple[str, float]]],
    date: str,
) -> Decimal | None:
    """Convert ``amount`` from unit ``from_id`` to unit ``to_id`` on ``date``.

    Uses each unit's price in the primary currency (see :func:`rate_in_primary`):
    ``amount * price(from) / price(to)``. The rates are looked up as ``float``
    (Skrooge stores them that way) but converted via their string
    representation before any arithmetic, so the whole computation happens in
    Decimal and never reintroduces binary-float rounding noise. Returns None
    when either rate is unknown, so the caller can fall back to the face value.
    """
    if from_id == to_id:
        return amount
    rate_from = rate_in_primary(rates, from_id, primary_id, date)
    rate_to = rate_in_primary(rates, to_id, primary_id, date)
    if rate_from is None or rate_to is None or rate_to == 0:
        return None
    return amount * Decimal(str(rate_from)) / Decimal(str(rate_to))
