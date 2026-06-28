"""Command-line entrypoint for the read-only Binance public market monitor."""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from app.binance_reader import BinancePublicMarketError, MarketPrice, fetch_public_prices
from app.config import PUBLIC_MARKET_WATCHLIST
from app.logger import append_market_price_log, current_timestamp, format_market_price_lines


DEFAULT_WATCH_INTERVAL_SECONDS = 60
DEFAULT_ALERT_THRESHOLD_PERCENT = Decimal("1.0")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments without fetching market data."""

    parser = argparse.ArgumentParser(
        description=(
            "Read-only Binance public market monitor. Uses public ticker data only; "
            "no API key, no account access, and no orders."
        )
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Run continuously until Ctrl+C.",
    )
    parser.add_argument(
        "--interval",
        type=_positive_int,
        default=DEFAULT_WATCH_INTERVAL_SECONDS,
        metavar="N",
        help="Watch interval in seconds. Default: 60.",
    )
    parser.add_argument(
        "--alert-threshold",
        type=_non_negative_decimal,
        default=DEFAULT_ALERT_THRESHOLD_PERCENT,
        metavar="N",
        help="Price-change alert threshold percentage for watch mode. Default: 1.0.",
    )
    return parser.parse_args(argv)


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

            previous_prices = {price.symbol: price.price for price in prices}
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("Stopping read-only Binance public market monitor.")
        return 0


def build_alert_lines(
    prices: Sequence[MarketPrice],
    previous_prices: dict[str, Decimal],
    *,
    alert_threshold_percent: Decimal,
    timestamp: str,
) -> list[str]:
    """Build local alert lines for prices crossing the configured threshold."""

    alert_lines: list[str] = []

    for price in prices:
        previous_price = previous_prices.get(price.symbol)
        if previous_price is None or previous_price == 0:
            continue

        change_percent = ((price.price - previous_price) / previous_price) * Decimal("100")
        if change_percent != 0 and abs(change_percent) >= alert_threshold_percent:
            alert_lines.append(
                f"{timestamp} ALERT {price.symbol}: "
                f"{_format_signed_percent(change_percent)} "
                f"from {previous_price} to {price.price}"
            )

    return alert_lines


def main(argv: Sequence[str] | None = None) -> int:
    """Run the read-only public market monitor."""

    args = parse_args(argv)
    if args.watch:
        return run_watch(args.interval, args.alert_threshold)
    return run_once()


def _positive_int(value: str) -> int:
    parsed_value = int(value)
    if parsed_value <= 0:
        raise argparse.ArgumentTypeError("interval must be a positive integer")
    return parsed_value


def _non_negative_decimal(value: str) -> Decimal:
    try:
        parsed_value = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("value must be a number") from exc

    if parsed_value < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return parsed_value


def _format_signed_percent(value: Decimal) -> str:
    return f"{value:+.2f}%"


if __name__ == "__main__":
    raise SystemExit(main())
