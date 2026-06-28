"""Timestamped logging for public market monitor output."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from app.binance_reader import MarketPrice


MARKET_PRICE_LOG_PATH = Path("logs/market_prices.log")
PHILIPPINE_TIME = timezone(timedelta(hours=8), name="PHT")


def current_timestamp() -> str:
    """Return the current Philippine time timestamp with UTC+8 offset."""

    return datetime.now(PHILIPPINE_TIME).isoformat(timespec="seconds")


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
    log_path: Path = MARKET_PRICE_LOG_PATH,
) -> None:
    """Append timestamped public market prices to the runtime log file."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        for line in lines:
            log_file.write(f"{line}\n")
