"""Command-line entrypoint for the read-only Binance public market monitor."""

from __future__ import annotations

from app.binance_reader import BinancePublicMarketError, fetch_public_prices
from app.config import PUBLIC_MARKET_WATCHLIST
from app.logger import append_market_price_log, current_timestamp, format_market_price_lines


def main() -> int:
    """Fetch and print configured public spot market prices."""

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


if __name__ == "__main__":
    raise SystemExit(main())
