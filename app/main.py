"""Command-line entrypoint for the read-only Binance public market monitor."""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence

from app.binance_reader import BinancePublicMarketError, fetch_public_prices
from app.config import PUBLIC_MARKET_WATCHLIST
from app.logger import append_market_price_log, current_timestamp, format_market_price_lines


DEFAULT_WATCH_INTERVAL_SECONDS = 60


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
    return parser.parse_args(argv)


def run_once() -> int:
    """Fetch, print, and log configured public spot market prices once."""

    try:
        prices = fetch_public_prices(PUBLIC_MARKET_WATCHLIST)
    except BinancePublicMarketError as exc:
        print(f"Error: {exc}")
        return 1

    timestamp = current_timestamp()
    price_lines = format_market_price_lines(prices, timestamp=timestamp)
    append_market_price_log(price_lines)

    print("Read-only Binance public market prices")
    print("No API key. No account access. No orders.")
    for line in price_lines:
        print(line)

    return 0


def run_watch(interval_seconds: int) -> int:
    """Fetch, print, and log public prices until interrupted."""

    try:
        while True:
            exit_code = run_once()
            if exit_code != 0:
                return exit_code
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("Stopping read-only Binance public market monitor.")
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the read-only public market monitor."""

    args = parse_args(argv)
    if args.watch:
        return run_watch(args.interval)
    return run_once()


def _positive_int(value: str) -> int:
    parsed_value = int(value)
    if parsed_value <= 0:
        raise argparse.ArgumentTypeError("interval must be a positive integer")
    return parsed_value


if __name__ == "__main__":
    raise SystemExit(main())
