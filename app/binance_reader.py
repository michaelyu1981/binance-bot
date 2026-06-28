"""Read-only public Binance market data helpers.

This module intentionally uses only public spot market endpoints. It does not
accept API keys, read account data, or place orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


PUBLIC_SPOT_API_BASE_URL = "https://api.binance.com"


@dataclass(frozen=True)
class MarketPrice:
    """A public spot market ticker price."""

    symbol: str
    price: Decimal


class BinancePublicMarketError(RuntimeError):
    """Raised when public Binance market data cannot be fetched or parsed."""


def fetch_public_prices(
    symbols: Iterable[str],
    *,
    timeout_seconds: float = 10.0,
) -> list[MarketPrice]:
    """Fetch public spot prices for the requested symbols.

    Uses Binance's unauthenticated `/api/v3/ticker/price` endpoint.
    """

    normalized_symbols = tuple(_normalize_symbol(symbol) for symbol in symbols)
    if not normalized_symbols:
        raise ValueError("At least one symbol is required.")

    return [
        _fetch_public_price(symbol, timeout_seconds=timeout_seconds)
        for symbol in normalized_symbols
    ]


def _fetch_public_price(symbol: str, *, timeout_seconds: float) -> MarketPrice:
    query = urlencode({"symbol": symbol})
    url = f"{PUBLIC_SPOT_API_BASE_URL}/api/v3/ticker/price?{query}"

    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raise BinancePublicMarketError(
            f"Binance public market request failed with HTTP {exc.code}."
        ) from exc
    except URLError as exc:
        raise BinancePublicMarketError(
            f"Could not reach Binance public market endpoint: {exc.reason}"
        ) from exc

    try:
        raw_prices = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BinancePublicMarketError("Binance returned invalid JSON.") from exc

    return _parse_market_price(raw_prices)


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("Symbols cannot be empty.")
    return normalized


def _parse_market_price(item: object) -> MarketPrice:
    if not isinstance(item, dict):
        raise BinancePublicMarketError("Ticker item was not an object.")

    symbol = item.get("symbol")
    price = item.get("price")
    if not isinstance(symbol, str) or not isinstance(price, str):
        raise BinancePublicMarketError("Ticker item was missing symbol or price.")

    try:
        parsed_price = Decimal(price)
    except InvalidOperation as exc:
        raise BinancePublicMarketError(f"Ticker price for {symbol} was invalid.") from exc

    return MarketPrice(symbol=symbol, price=parsed_price)
