"""Timestamped logging for public market monitor output."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from app.binance_reader import MarketPrice


MARKET_PRICE_LOG_PATH = Path("logs/market_prices.log")
MARKET_PRICE_LOG_DIR = Path("logs")
MARKET_PRICE_LOG_PREFIX = "market_prices"
PHILIPPINE_TIME = timezone(timedelta(hours=8), name="PHT")


def current_timestamp() -> str:
    """Return the current Philippine time timestamp with UTC+8 offset."""

    return datetime.now(PHILIPPINE_TIME).isoformat(timespec="seconds")


def format_price_usd(value: Decimal) -> str:
    """Format a price for reports: $ sign, thousands separators, 3 decimal places.

    E.g. Decimal("12345678.901234") -> "$12,345,678.901". Raw Binance prices
    carry up to 8 decimal places, which clutters Telegram messages -- this is
    the single formatting point every report/alert should use instead of
    interpolating a Decimal directly.
    """

    return f"${value:,.3f}"


def format_market_price_lines(
    prices: Iterable[MarketPrice],
    *,
    timestamp: str,
) -> list[str]:
    """Format public market prices for terminal output and log files."""

    return [f"{timestamp} {price.symbol}: {price.price}" for price in prices]


def append_market_price_log(
    lines: Iterable[str],
    *,
    log_path: Path | None = None,
) -> None:
    """Append timestamped public market prices to the runtime log file."""

    if log_path is None:
        log_path = daily_market_price_log_path()

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        for line in lines:
            log_file.write(f"{line}\n")


def daily_market_price_log_path(
    *,
    current_date: date | None = None,
    log_dir: Path = MARKET_PRICE_LOG_DIR,
) -> Path:
    """Return the Philippine-date log path for public market prices."""

    if current_date is None:
        current_date = datetime.now(PHILIPPINE_TIME).date()
    return log_dir / f"{MARKET_PRICE_LOG_PREFIX}-{current_date.isoformat()}.log"
