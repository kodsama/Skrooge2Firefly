"""Pure normalization helpers for change detection (no network, no I/O)."""

from __future__ import annotations

from decimal import Decimal


def norm_date(value: str) -> str:
    """Firefly returns TZ datetimes; compare only the YYYY-MM-DD prefix."""
    return value[:10]


def norm_amount(value: str | Decimal) -> Decimal:
    """Compare money by magnitude, not string form (Firefly re-rounds to 6dp)."""
    return Decimal(str(value))


def norm_str(value: str | None) -> str:
    """Treat None and missing as empty string for comparison."""
    return value or ""
