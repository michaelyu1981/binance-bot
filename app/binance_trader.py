"""Real Binance Spot market order placement -- LIVE TRADING, uses real funds.

This module is intentionally narrow: it can place a market buy (by quote
amount) or a market sell (by base quantity), and look up a symbol's lot-size
step for rounding a sell quantity to something Binance will accept. That is
the entire surface area. It must never gain a withdraw, transfer, or
sub-account function -- per docs/binance-api-key-policy.md, withdrawal and
internal-transfer permissions are forbidden forever, and this module is the
only place in the codebase allowed to place orders at all.

Uses a credential pair dedicated to live trading (BINANCE_LIVE_API_KEY /
BINANCE_LIVE_API_SECRET), separate from the read-only key in
app/binance_account.py. If those environment variables are not set, callers
get None from `load_live_trading_config_from_env()` and must not place any
order -- there is no fallback to the read-only key.

Only reached from app/live_real_trader.py, which only runs a bot when its
dashboard config has mode == "live" AND enabled == True, which in turn is
only reachable after Michael's exact phrase "enable live spot trading." was
given, per docs/binance-api-key-policy.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
import hashlib
import hmac
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BINANCE_API_BASE_URL = "https://api.binance.com"
BINANCE_LIVE_API_KEY_ENV = "BINANCE_LIVE_API_KEY"
BINANCE_LIVE_API_SECRET_ENV = "BINANCE_LIVE_API_SECRET"
ORDER_PATH = "/api/v3/order"
EXCHANGE_INFO_PATH = "/api/v3/exchangeInfo"
DEFAULT_RECV_WINDOW_MS = 5000


@dataclass(frozen=True)
class LiveTradingConfig:
    api_key: str
    api_secret: str


class BinanceTraderError(RuntimeError):
    """Raised when a real order request fails, is rejected, or can't be parsed."""


def load_live_trading_config_from_env() -> LiveTradingConfig | None:
    """Load the dedicated live-trading credential pair, without printing secrets."""

    api_key = os.environ.get(BINANCE_LIVE_API_KEY_ENV, "").strip()
    api_secret = os.environ.get(BINANCE_LIVE_API_SECRET_ENV, "").strip()
    if not api_key or not api_secret:
        return None
    return LiveTradingConfig(api_key=api_key, api_secret=api_secret)


def place_market_buy_by_quote(
    *,
    config: LiveTradingConfig,
    symbol: str,
    quote_amount: Decimal,
    timeout_seconds: float = 10.0,
) -> dict[str, object]:
    """Place a real MARKET BUY, sized by quote (USDT) amount rather than base quantity.

    Binance computes the base quantity itself from `quoteOrderQty`, so no
    lot-size rounding is needed on the buy side.
    """

    return _place_order(
        config=config,
        symbol=symbol,
        side="BUY",
        params={"quoteOrderQty": str(quote_amount)},
        timeout_seconds=timeout_seconds,
    )


def place_market_sell_by_quantity(
    *,
    config: LiveTradingConfig,
    symbol: str,
    quantity: Decimal,
    timeout_seconds: float = 10.0,
) -> dict[str, object]:
    """Place a real MARKET SELL for an exact base quantity.

    Callers must round `quantity` with `round_quantity_down()` first --
    Binance rejects sell quantities that don't align to the symbol's
    LOT_SIZE step.
    """

    return _place_order(
        config=config,
        symbol=symbol,
        side="SELL",
        params={"quantity": str(quantity)},
        timeout_seconds=timeout_seconds,
    )


def _place_order(
    *,
    config: LiveTradingConfig,
    symbol: str,
    side: str,
    params: dict[str, str],
    timeout_seconds: float,
) -> dict[str, object]:
    full_params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        **params,
        "recvWindow": str(DEFAULT_RECV_WINDOW_MS),
        "timestamp": str(int(time.time() * 1000)),
    }
    query_string = urlencode(full_params)
    signature = hmac.new(
        config.api_secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    url = f"{BINANCE_API_BASE_URL}{ORDER_PATH}?{query_string}&signature={signature}"
    request = Request(url, headers={"X-MBX-APIKEY": config.api_key}, method="POST")

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = _http_error_detail(exc)
        raise BinanceTraderError(f"Order {side} {symbol} failed with HTTP {exc.code}.{detail}") from exc
    except URLError as exc:
        raise BinanceTraderError(f"Could not reach Binance API: {exc.reason}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BinanceTraderError(f"Order {side} {symbol} returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise BinanceTraderError(f"Order {side} {symbol} returned unexpected data.")
    return data


_LOT_SIZE_STEP_CACHE: dict[str, Decimal] = {}


def fetch_lot_step_size(symbol: str, *, timeout_seconds: float = 10.0) -> Decimal | None:
    """Look up (and cache) a symbol's LOT_SIZE step, a public/unsigned call."""

    if symbol in _LOT_SIZE_STEP_CACHE:
        return _LOT_SIZE_STEP_CACHE[symbol]

    url = f"{BINANCE_API_BASE_URL}{EXCHANGE_INFO_PATH}?symbol={symbol}"
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError):
        return None

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    symbols = data.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        return None
    filters = symbols[0].get("filters", []) if isinstance(symbols[0], dict) else []
    if not isinstance(filters, list):
        return None

    for item in filters:
        if isinstance(item, dict) and item.get("filterType") == "LOT_SIZE":
            try:
                step = Decimal(str(item.get("stepSize", "0")))
            except Exception:  # noqa: BLE001 - malformed filter, treat as unavailable
                return None
            if step > 0:
                _LOT_SIZE_STEP_CACHE[symbol] = step
                return step
    return None


def round_quantity_down(symbol: str, quantity: Decimal) -> Decimal:
    """Round a sell quantity down to the symbol's LOT_SIZE step.

    Falls back to the unrounded quantity if the step size can't be fetched
    (Binance will reject the order in that case rather than silently accept
    a misaligned quantity).
    """

    if quantity <= 0:
        return Decimal("0")
    step = fetch_lot_step_size(symbol)
    if step is None or step == 0:
        return quantity
    return (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step


def _http_error_detail(exc: HTTPError) -> str:
    try:
        payload = exc.read().decode("utf-8")
    except OSError:
        return ""
    if not payload:
        return ""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    message = data.get("msg") if isinstance(data, dict) else None
    if isinstance(message, str) and message:
        return f" Message: {message}"
    return ""
