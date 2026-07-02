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


@dataclass(frozen=True)
class Candle:
    """A public spot market candlestick."""

    symbol: str
    interval: str
    open_time_ms: int
    close_time_ms: int
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal


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


def fetch_public_candles(
    symbol: str,
    interval: str,
    *,
    limit: int = 100,
    timeout_seconds: float = 10.0,
) -> list[Candle]:
    """Fetch public spot candles from Binance's unauthenticated klines endpoint."""

    normalized_symbol = _normalize_symbol(symbol)
    normalized_interval = interval.strip()
    if not normalized_interval:
        raise ValueError("Interval cannot be empty.")
    if limit <= 0 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000.")

    query = urlencode(
        {
            "symbol": normalized_symbol,
            "interval": normalized_interval,
            "limit": limit,
        }
    )
    url = f"{PUBLIC_SPOT_API_BASE_URL}/api/v3/klines?{query}"

    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raise BinancePublicMarketError(
            f"Binance public candle request failed with HTTP {exc.code}."
        ) from exc
    except URLError as exc:
        raise BinancePublicMarketError(
            f"Could not reach Binance public candle endpoint: {exc.reason}"
        ) from exc

    try:
        raw_candles = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BinancePublicMarketError("Binance returned invalid candle JSON.") from exc

    if not isinstance(raw_candles, list):
        raise BinancePublicMarketError("Binance candle response was not a list.")

    return [
        _parse_candle(
            item,
            symbol=normalized_symbol,
            interval=normalized_interval,
        )
        for item in raw_candles
    ]


def fetch_public_candles_window(
    symbol: str,
    interval: str,
    *,
    start_time_ms: int,
    end_time_ms: int,
    limit: int = 1000,
    timeout_seconds: float = 10.0,
) -> list[Candle]:
    """Fetch public spot candles from Binance's klines endpoint for a time window."""

    normalized_symbol = _normalize_symbol(symbol)
    normalized_interval = interval.strip()
    if not normalized_interval:
        raise ValueError("Interval cannot be empty.")
    if limit <= 0 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000.")
    if start_time_ms <= 0 or end_time_ms <= start_time_ms:
        raise ValueError("Invalid candle time window.")

    query = urlencode(
        {
            "symbol": normalized_symbol,
            "interval": normalized_interval,
            "startTime": str(start_time_ms),
            "endTime": str(end_time_ms),
            "limit": str(limit),
        }
    )
    url = f"{PUBLIC_SPOT_API_BASE_URL}/api/v3/klines?{query}"

    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raise BinancePublicMarketError(
            f"Binance public candle request failed with HTTP {exc.code}."
        ) from exc
    except URLError as exc:
        raise BinancePublicMarketError(
            f"Could not reach Binance public candle endpoint: {exc.reason}"
        ) from exc

    try:
        raw_candles = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BinancePublicMarketError("Binance returned invalid candle JSON.") from exc

    if not isinstance(raw_candles, list):
        raise BinancePublicMarketError("Binance candle response was not a list.")

    return [
        _parse_candle(
            item,
            symbol=normalized_symbol,
            interval=normalized_interval,
        )
        for item in raw_candles
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


def _parse_candle(item: object, *, symbol: str, interval: str) -> Candle:
    if not isinstance(item, list) or len(item) < 11:
        raise BinancePublicMarketError("Candle item was not a valid kline array.")

    try:
        open_time_ms = int(item[0])
        close_time_ms = int(item[6])
        trade_count = int(item[8])
        open_price = Decimal(str(item[1]))
        high_price = Decimal(str(item[2]))
        low_price = Decimal(str(item[3]))
        close_price = Decimal(str(item[4]))
        volume = Decimal(str(item[5]))
        quote_volume = Decimal(str(item[7]))
        taker_buy_base_volume = Decimal(str(item[9]))
        taker_buy_quote_volume = Decimal(str(item[10]))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise BinancePublicMarketError("Candle item contained invalid values.") from exc

    return Candle(
        symbol=symbol,
        interval=interval,
        open_time_ms=open_time_ms,
        close_time_ms=close_time_ms,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
        quote_volume=quote_volume,
        trade_count=trade_count,
        taker_buy_base_volume=taker_buy_base_volume,
        taker_buy_quote_volume=taker_buy_quote_volume,
    )
