"""Read-only public market monitor runtime."""

from __future__ import annotations

import time
from decimal import Decimal

from app.alerts import build_alert_lines
from app.binance_reader import BinancePublicMarketError, MarketPrice, fetch_public_prices
from app.config import PUBLIC_MARKET_WATCHLIST
from app.logger import append_market_price_log, current_timestamp, format_market_price_lines
from app.telegram_notifier import (
    TelegramSendError,
    is_telegram_enabled,
    send_alert_lines_to_telegram,
)


def run_once() -> int:
    """Fetch, print, and log configured public spot market prices once."""

    return 0 if run_price_cycle() is not None else 1


def run_price_cycle() -> list[MarketPrice] | None:
    """Fetch, print, and log configured public spot market prices."""

    try:
        prices = fetch_public_prices(PUBLIC_MARKET_WATCHLIST)
    except BinancePublicMarketError as exc:
        print(f"Error: {exc}")
        return None

    timestamp = current_timestamp()
    price_lines = format_market_price_lines(prices, timestamp=timestamp)
    append_market_price_log(price_lines)

    print("Read-only Binance public market prices")
    print("No API key. No account access. No orders.")
    for line in price_lines:
        print(line)

    return prices


def run_watch(interval_seconds: int, alert_threshold_percent: Decimal) -> int:
    """Fetch, print, and log public prices until interrupted."""

    previous_prices: dict[str, Decimal] = {}
    print_startup_settings(
        interval_seconds=interval_seconds,
        alert_threshold_percent=alert_threshold_percent,
    )

    try:
        while True:
            prices = run_price_cycle()
            if prices is None:
                return 1

            alert_lines = build_alert_lines(
                prices,
                previous_prices,
                alert_threshold_percent=alert_threshold_percent,
                timestamp=current_timestamp(),
            )
            if alert_lines:
                append_market_price_log(alert_lines)
                for line in alert_lines:
                    print(line)
                try:
                    send_alert_lines_to_telegram(alert_lines)
                except TelegramSendError as exc:
                    print(f"Telegram alert send failed: {exc}")

            previous_prices = {price.symbol: price.price for price in prices}
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("Stopping read-only Binance public market monitor.")
        return 0


def print_startup_settings(
    *,
    interval_seconds: int,
    alert_threshold_percent: Decimal,
) -> None:
    """Print watch-mode startup settings without secrets."""

    print("Starting read-only Binance public market monitor")
    print(f"Watchlist: {', '.join(PUBLIC_MARKET_WATCHLIST)}")
    print(f"Interval seconds: {interval_seconds}")
    print(f"Alert threshold: {alert_threshold_percent}%")
    print(f"Telegram: {'enabled' if is_telegram_enabled() else 'disabled'}")
    print("Safety: public market data only; no API key, no account access, no orders.")
