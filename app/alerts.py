"""Local alert calculation for public market prices."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.binance_reader import MarketPrice


def build_alert_lines(
    prices: Sequence[MarketPrice],
    previous_prices: dict[str, Decimal],
    *,
    alert_threshold_percent: Decimal,
    timestamp: str,
) -> tuple[list[str], set[str]]:
    """Build local alert lines for prices crossing the configured threshold.

    Also returns the set of symbols that triggered at least one alert line,
    so callers can enrich just those symbols without string-parsing.
    """

    alert_lines: list[str] = []
    triggered_symbols: set[str] = set()

    for price in prices:
        previous_price = previous_prices.get(price.symbol)
        if previous_price is None or previous_price == 0:
            continue

        change_percent = ((price.price - previous_price) / previous_price) * Decimal("100")
        if change_percent != 0 and abs(change_percent) >= alert_threshold_percent:
            alert_lines.append(
                f"{timestamp} ALERT {price.symbol}: "
                f"{format_signed_percent(change_percent)} "
                f"from {previous_price} to {price.price}"
            )
            triggered_symbols.add(price.symbol)

    return alert_lines, triggered_symbols


def format_signed_percent(value: Decimal) -> str:
    """Format a percent value with a leading sign."""

    return f"{value:+.2f}%"
