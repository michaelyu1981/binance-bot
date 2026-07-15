"""Read-only Binance account/wallet snapshots (Spot, Funding, Simple Earn).

This module is restricted to read-only USER_DATA endpoints (account
information, funding wallet balance, Simple Earn positions). It must not
place orders, cancel orders, transfer funds, subscribe/redeem Earn
products, or enable trading -- every function here only ever performs a
signed GET or a balance-query POST, never a TRADE-permission call.
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
FUNDING_WALLET_PATH = "/sapi/v1/asset/get-funding-asset"
SIMPLE_EARN_FLEXIBLE_POSITION_PATH = "/sapi/v1/simple-earn/flexible/position"
SIMPLE_EARN_LOCKED_POSITION_PATH = "/sapi/v1/simple-earn/locked/position"
DEFAULT_RECV_WINDOW_MS = 5000
SIMPLE_EARN_PAGE_SIZE = 100


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

    data = _signed_request(
        config=config,
        method="GET",
        path=ACCOUNT_INFORMATION_PATH,
        params={"omitZeroBalances": "true" if omit_zero_balances else "false"},
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(data, dict):
        raise BinanceAccountError("Binance account endpoint returned unexpected data.")

    return _parse_account_snapshot(data)


def fetch_funding_wallet_balances(
    *,
    config: BinanceAccountConfig,
    timeout_seconds: float = 10.0,
) -> tuple[AccountBalance, ...]:
    """Fetch Funding wallet balances (read-only USER_DATA, separate from Spot)."""

    data = _signed_request(
        config=config,
        method="POST",
        path=FUNDING_WALLET_PATH,
        params={},
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(data, list):
        raise BinanceAccountError("Binance funding wallet endpoint returned unexpected data.")

    balances: list[AccountBalance] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        asset = item.get("asset")
        free = _decimal_from_value(item.get("free"))
        locked = _decimal_from_value(item.get("locked")) or Decimal("0")
        freeze = _decimal_from_value(item.get("freeze")) or Decimal("0")
        withdrawing = _decimal_from_value(item.get("withdrawing")) or Decimal("0")
        if not isinstance(asset, str) or free is None:
            continue
        total_locked = locked + freeze + withdrawing
        if free == 0 and total_locked == 0:
            continue
        balances.append(AccountBalance(asset=asset, free=free, locked=total_locked))
    return tuple(sorted(balances, key=lambda balance: balance.asset))


def fetch_simple_earn_flexible_positions(
    *,
    config: BinanceAccountConfig,
    timeout_seconds: float = 10.0,
) -> tuple[AccountBalance, ...]:
    """Fetch Simple Earn Flexible positions (read-only USER_DATA)."""

    rows = _fetch_simple_earn_rows(
        config=config,
        path=SIMPLE_EARN_FLEXIBLE_POSITION_PATH,
        timeout_seconds=timeout_seconds,
    )
    balances: list[AccountBalance] = []
    for item in rows:
        asset = item.get("asset")
        amount = _decimal_from_value(item.get("totalAmount"))
        if not isinstance(asset, str) or amount is None or amount == 0:
            continue
        balances.append(AccountBalance(asset=asset, free=amount, locked=Decimal("0")))
    return tuple(sorted(balances, key=lambda balance: balance.asset))


def fetch_simple_earn_locked_positions(
    *,
    config: BinanceAccountConfig,
    timeout_seconds: float = 10.0,
) -> tuple[AccountBalance, ...]:
    """Fetch Simple Earn Locked positions (read-only USER_DATA)."""

    rows = _fetch_simple_earn_rows(
        config=config,
        path=SIMPLE_EARN_LOCKED_POSITION_PATH,
        timeout_seconds=timeout_seconds,
    )
    balances: list[AccountBalance] = []
    for item in rows:
        asset = item.get("asset")
        amount = _decimal_from_value(item.get("amount"))
        if not isinstance(asset, str) or amount is None or amount == 0:
            continue
        balances.append(AccountBalance(asset=asset, free=amount, locked=Decimal("0")))
    return tuple(sorted(balances, key=lambda balance: balance.asset))


def _fetch_simple_earn_rows(
    *,
    config: BinanceAccountConfig,
    path: str,
    timeout_seconds: float,
) -> list[dict[str, object]]:
    all_rows: list[dict[str, object]] = []
    current = 1
    while True:
        data = _signed_request(
            config=config,
            method="GET",
            path=path,
            params={"current": str(current), "size": str(SIMPLE_EARN_PAGE_SIZE)},
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(data, dict):
            raise BinanceAccountError(f"Binance endpoint {path} returned unexpected data.")
        rows = data.get("rows", [])
        if not isinstance(rows, list):
            break
        all_rows.extend(item for item in rows if isinstance(item, dict))
        total = data.get("total", 0)
        if not isinstance(total, int) or len(all_rows) >= total or not rows:
            break
        current += 1
    return all_rows


def _signed_request(
    *,
    config: BinanceAccountConfig,
    method: str,
    path: str,
    params: dict[str, str],
    timeout_seconds: float,
) -> object:
    full_params = {
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
    url = f"{BINANCE_API_BASE_URL}{path}?{query_string}&signature={signature}"
    request = Request(url, headers={"X-MBX-APIKEY": config.api_key}, method=method)

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = _http_error_detail(exc)
        raise BinanceAccountError(f"Binance request to {path} failed with HTTP {exc.code}.{detail}") from exc
    except URLError as exc:
        raise BinanceAccountError(f"Could not reach Binance API: {exc.reason}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BinanceAccountError(f"Binance endpoint {path} returned invalid JSON.") from exc


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
