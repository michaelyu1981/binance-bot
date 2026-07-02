"""Read-only Binance Spot account snapshot.

This module is restricted to the USER_DATA account information endpoint. It
must not place orders, cancel orders, transfer funds, or enable trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BINANCE_API_BASE_URL = "https://api.binance.com"
BINANCE_API_KEY_ENV = "BINANCE_API_KEY"
BINANCE_API_SECRET_ENV = "BINANCE_API_SECRET"
ACCOUNT_INFORMATION_PATH = "/api/v3/account"
DEFAULT_RECV_WINDOW_MS = 5000


@dataclass(frozen=True)
class BinanceAccountConfig:
    api_key: str
    api_secret: str


@dataclass(frozen=True)
class AccountBalance:
    asset: str
    free: Decimal
    locked: Decimal

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


@dataclass(frozen=True)
class AccountSnapshot:
    account_type: str
    can_trade: bool
    can_withdraw: bool
    can_deposit: bool
    fetched_at_ms: int
    update_time_ms: int | None
    permissions: tuple[str, ...]
    balances: tuple[AccountBalance, ...]


class BinanceAccountError(RuntimeError):
    """Raised when the read-only account snapshot cannot be fetched."""


def load_binance_account_config_from_env() -> BinanceAccountConfig | None:
    """Load Binance account API credentials without printing secrets."""

    api_key = os.environ.get(BINANCE_API_KEY_ENV, "").strip()
    api_secret = os.environ.get(BINANCE_API_SECRET_ENV, "").strip()
    if not api_key or not api_secret:
        return None
    return BinanceAccountConfig(api_key=api_key, api_secret=api_secret)


def fetch_account_snapshot(
    *,
    config: BinanceAccountConfig,
    omit_zero_balances: bool = True,
    timeout_seconds: float = 10.0,
) -> AccountSnapshot:
    """Fetch Spot account balances from Binance's read-only account endpoint."""

    params = {
        "omitZeroBalances": "true" if omit_zero_balances else "false",
        "recvWindow": str(DEFAULT_RECV_WINDOW_MS),
        "timestamp": str(int(time.time() * 1000)),
    }
    query_string = urlencode(params)
    signature = hmac.new(
        config.api_secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    url = f"{BINANCE_API_BASE_URL}{ACCOUNT_INFORMATION_PATH}?{query_string}&signature={signature}"
    request = Request(
        url,
        headers={"X-MBX-APIKEY": config.api_key},
        method="GET",
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = _http_error_detail(exc)
        raise BinanceAccountError(
            f"Binance account request failed with HTTP {exc.code}.{detail}"
        ) from exc
    except URLError as exc:
        raise BinanceAccountError(f"Could not reach Binance API: {exc.reason}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BinanceAccountError("Binance account endpoint returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise BinanceAccountError("Binance account endpoint returned unexpected data.")

    return _parse_account_snapshot(data)


def format_account_snapshot(snapshot: AccountSnapshot) -> str:
    """Format a read-only account snapshot for CLI output."""

    lines = [
        "Binance read-only account snapshot",
        f"Account type: {snapshot.account_type}",
        f"Can trade: {snapshot.can_trade}",
        f"Can withdraw: {snapshot.can_withdraw}",
        f"Can deposit: {snapshot.can_deposit}",
        f"Permissions: {', '.join(snapshot.permissions) if snapshot.permissions else 'None'}",
        "Balances:",
    ]
    if not snapshot.balances:
        lines.append("- No non-zero balances returned.")
    else:
        for balance in snapshot.balances:
            lines.append(
                f"- {balance.asset}: free {balance.free} | locked {balance.locked} | total {balance.total}"
            )
    lines.append("Safety: read-only account data only; no orders; no trading endpoints.")
    return "\n".join(lines)


def _parse_account_snapshot(data: dict[str, object]) -> AccountSnapshot:
    balances = []
    raw_balances = data.get("balances", [])
    if isinstance(raw_balances, list):
        for item in raw_balances:
            if not isinstance(item, dict):
                continue
            asset = item.get("asset")
            free = _decimal_from_value(item.get("free"))
            locked = _decimal_from_value(item.get("locked"))
            if not isinstance(asset, str) or free is None or locked is None:
                continue
            if free == 0 and locked == 0:
                continue
            balances.append(AccountBalance(asset=asset, free=free, locked=locked))

    permissions = data.get("permissions", [])
    if not isinstance(permissions, list):
        permissions = []

    return AccountSnapshot(
        account_type=_optional_str(data.get("accountType")) or "Unknown",
        can_trade=bool(data.get("canTrade", False)),
        can_withdraw=bool(data.get("canWithdraw", False)),
        can_deposit=bool(data.get("canDeposit", False)),
        fetched_at_ms=int(time.time() * 1000),
        update_time_ms=_optional_int(data.get("updateTime")),
        permissions=tuple(item for item in permissions if isinstance(item, str)),
        balances=tuple(sorted(balances, key=lambda balance: balance.asset)),
    )


def _decimal_from_value(value: object) -> Decimal | None:
    if not isinstance(value, str):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


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
