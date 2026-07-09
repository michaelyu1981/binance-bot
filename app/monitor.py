"""Read-only public market monitor runtime."""

from __future__ import annotations

from decimal import Decimal
import sqlite3
import time

from app.alerts import build_alert_lines
from app.binance_reader import BinancePublicMarketError, MarketPrice, fetch_public_prices
from app.candle_store import DEFAULT_CANDLE_DB_PATH
from app.config import PUBLIC_MARKET_WATCHLIST
from app.health import (
    PRICE_MONITOR_HEALTH_PATH,
    write_error_heartbeat,
    write_success_heartbeat,
)
from app.indicators import build_indicator_snapshot
from app.logger import append_market_price_log, current_timestamp, format_market_price_lines
from app.qualifying_readings import evaluate_qualifying_readings, format_qualifying_readings
from app.signals import build_technical_signal_guide
from app.telegram_notifier import (
    TelegramSendError,
    is_telegram_enabled,
    send_alert_lines_to_telegram,
)


PRICE_ENRICHMENT_INTERVAL = "5m"
PRICE_ENRICHMENT_CANDLE_LIMIT = 120
PRICE_ENRICHMENT_MIN_CANDLES = 20


def run_once() -> int:
    """Fetch, print, and log configured public spot market prices once."""

    return 0 if run_price_cycle(interval_seconds=0) is not None else 1


def run_price_cycle(*, interval_seconds: int) -> list[MarketPrice] | None:
    """Fetch, print, and log configured public spot market prices."""

    try:
        prices = fetch_public_prices(PUBLIC_MARKET_WATCHLIST)
    except BinancePublicMarketError as exc:
        print(f"Error: {exc}")
        write_error_heartbeat(
            path=PRICE_MONITOR_HEALTH_PATH,
            service="price_monitor",
            interval_seconds=interval_seconds,
            error_message=str(exc),
        )
        return None

    timestamp = current_timestamp()
    price_lines = format_market_price_lines(prices, timestamp=timestamp)
    append_market_price_log(price_lines)

    print("Read-only Binance public market prices")
    print("No API key. No account access. No orders.")
    for line in price_lines:
        print(line)

    write_success_heartbeat(
        path=PRICE_MONITOR_HEALTH_PATH,
        service="price_monitor",
        interval_seconds=interval_seconds,
        details={
            "symbols": list(PUBLIC_MARKET_WATCHLIST),
            "price_count": len(prices),
        },
    )
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
            prices = run_price_cycle(interval_seconds=interval_seconds)
            if prices is None:
                return 1

            alert_lines, triggered_symbols = build_alert_lines(
                prices,
                previous_prices,
                alert_threshold_percent=alert_threshold_percent,
                timestamp=current_timestamp(),
            )
            if alert_lines:
                enrichment_lines = _build_price_alert_enrichment(triggered_symbols)
                all_lines = alert_lines + list(enrichment_lines)
                append_market_price_log(all_lines)
                for line in all_lines:
                    print(line)
                try:
                    send_alert_lines_to_telegram(all_lines)
                except TelegramSendError as exc:
                    print(f"Telegram alert send failed: {exc}")

            previous_prices = {price.symbol: price.price for price in prices}
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("Stopping read-only Binance public market monitor.")
        return 0


def _load_price_enrichment_candles(symbol: str) -> tuple[dict[str, Decimal], ...]:
    if not DEFAULT_CANDLE_DB_PATH.exists():
        return ()
    try:
        connection = sqlite3.connect(DEFAULT_CANDLE_DB_PATH)
        rows = connection.execute(
            """
            SELECT high, low, close, volume
            FROM candles
            WHERE symbol = ? AND interval = ?
            ORDER BY open_time_ms DESC
            LIMIT ?
            """,
            (symbol, PRICE_ENRICHMENT_INTERVAL, PRICE_ENRICHMENT_CANDLE_LIMIT),
        ).fetchall()
    except sqlite3.Error:
        return ()
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass

    return tuple(
        {"high": Decimal(row[0]), "low": Decimal(row[1]), "close": Decimal(row[2]), "volume": Decimal(row[3])}
        for row in reversed(rows)
    )


def _build_price_alert_enrichment(symbols: set[str]) -> tuple[str, ...]:
    """Append qualifying 5m readings only for symbols that already alerted."""

    lines: list[str] = []
    for symbol in sorted(symbols):
        candles = _load_price_enrichment_candles(symbol)
        if len(candles) < PRICE_ENRICHMENT_MIN_CANDLES:
            continue
        highs = tuple(candle["high"] for candle in candles)
        lows = tuple(candle["low"] for candle in candles)
        closes = tuple(candle["close"] for candle in candles)
        volumes = tuple(candle["volume"] for candle in candles)
        snapshot = build_indicator_snapshot(highs=highs, lows=lows, closes=closes, volumes=volumes)
        guide = build_technical_signal_guide(
            symbol=symbol,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            snapshot=snapshot,
        )
        readings = evaluate_qualifying_readings(symbol=symbol, interval=PRICE_ENRICHMENT_INTERVAL, guide=guide)
        lines.extend(format_qualifying_readings(readings))
    return tuple(lines)


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
