"""Command-line entrypoint for the read-only Binance public market monitor."""

from __future__ import annotations

from app.binance_reader import DEFAULT_SYMBOLS, BinancePublicMarketError, fetch_public_prices


def main() -> int:
    """Fetch and print public BTC/USDT, ETH/USDT, and BNB/USDT prices."""

    try:
        prices = fetch_public_prices(DEFAULT_SYMBOLS)
    except BinancePublicMarketError as exc:
        print(f"Error: {exc}")
        return 1

    print("Read-only Binance public market prices")
    print("No API key. No account access. No orders.")
    for market_price in prices:
        print(f"{market_price.symbol}: {market_price.price}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
