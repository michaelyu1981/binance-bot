"""Live REAL-MONEY trading loop for bots switched to Live Trading mode.

Only runs a bot when its dashboard config has mode == "live" AND
enabled == True (see `app/live_bot_state.py`, set from the Live Bot Trader
dashboard tab). Drives the exact same `app.live_trading_engine.TradingEngine`
and strategy decision logic as `app/live_paper_trader.py` -- the only
difference is `RealExecutor` below, which places actual Binance market
orders through `app/binance_trader.py` and records whatever Binance actually
filled, instead of simulating a fill.

Requires BINANCE_LIVE_API_KEY / BINANCE_LIVE_API_SECRET in the environment
-- a credential pair dedicated to live trading, separate from the read-only
key used by `app/binance_account.py`. If that pair is missing, every cycle
logs a message and does nothing; it never falls back to paper trading.

Enabled only after Michael's exact phrase "enable live spot trading." per
docs/binance-api-key-policy.md. This module places real orders with real
funds on Binance Spot. It never calls a withdraw or internal-transfer
endpoint -- those capabilities do not exist anywhere in `app/binance_trader.py`
or this codebase. The kill switch is the dashboard's Stop Trading button:
each cycle re-reads the config, and a disabled bot is skipped immediately,
same as the paper loop.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import time
from typing import Any

from app.binance_trader import (
    BinanceTraderError,
    LiveTradingConfig,
    load_live_trading_config_from_env,
    place_market_buy_by_quote,
    place_market_sell_by_quantity,
    round_quantity_down,
)
from app.health import write_error_heartbeat, write_success_heartbeat
from app.live_bot_state import LIVE_BOT_DEFINITIONS, TRADING_MODE_LIVE, write_live_trade_runtime
from app.live_trading_engine import TradingEngine
from app.logger import format_coin_amount, format_currency_usd, format_price_usd
from app.telegram_notifier import TelegramSendError, load_telegram_config_from_env, send_telegram_message


LIVE_REAL_TRADER_HEALTH_PATH = Path("data/health/live_real_trader.json")
POLL_SECONDS = 120


class RealExecutor:
    """Places actual Binance Spot market orders. Real funds, real risk.

    Buys are sized by quote amount (`quoteOrderQty`) so Binance handles the
    base-quantity math directly. Sells must be rounded to the symbol's
    LOT_SIZE step first, or Binance rejects the order. On any API error the
    order is simply not retried -- it's logged as a failed fill and the next
    candle tries again on its own terms, never a tight retry loop that could
    double-order.
    """

    def __init__(self, config: LiveTradingConfig):
        self._config = config

    def buy(self, *, session: dict[str, Any], symbol: str, decision: Any) -> dict[str, Any] | None:
        spend = session["usdt_balance"] if decision.fraction is None else session["usdt_balance"] * decision.fraction
        if spend <= 0:
            return None
        strategy_name = _strategy_name(session.get("slug"))
        try:
            order = place_market_buy_by_quote(config=self._config, symbol=symbol, quote_amount=spend)
        except BinanceTraderError as exc:
            print(f"LIVE BUY FAILED {symbol}: {exc}")
            _notify_telegram(
                _format_failure_message(strategy_name=strategy_name, symbol=symbol, side="BUY", error=str(exc))
            )
            return {"action": "LIVE_BUY_FAILED", "reason": str(exc)}

        executed_qty = _decimal_field(order, "executedQty")
        quote_spent = _decimal_field(order, "cummulativeQuoteQty")
        if executed_qty <= 0:
            _notify_telegram(
                _format_failure_message(
                    strategy_name=strategy_name, symbol=symbol, side="BUY", error="Order filled zero quantity."
                )
            )
            return {"action": "LIVE_BUY_FAILED", "reason": "Order filled zero quantity."}

        session["usdt_balance"] -= quote_spent
        session["base_balance"] += executed_qty
        record_fill = getattr(session["machine"], "record_fill", None)
        if record_fill is not None:
            record_fill(usdt_spent=quote_spent, base_bought=executed_qty)
        _notify_telegram(
            _format_fill_message(
                strategy_name=strategy_name,
                symbol=symbol,
                side="BUY",
                price=decision.price,
                quantity=executed_qty,
                quote_amount=quote_spent,
                reason=decision.reason,
            )
        )
        return {
            "action": "LIVE_BUY",
            "price": str(decision.price),
            "reason": decision.reason,
            "order_id": order.get("orderId"),
            "executed_qty": str(executed_qty),
            "quote_spent": str(quote_spent),
        }

    def sell(self, *, session: dict[str, Any], symbol: str, decision: Any) -> dict[str, Any] | None:
        quantity = round_quantity_down(symbol, session["base_balance"])
        if quantity <= 0:
            return None
        strategy_name = _strategy_name(session.get("slug"))
        try:
            order = place_market_sell_by_quantity(config=self._config, symbol=symbol, quantity=quantity)
        except BinanceTraderError as exc:
            print(f"LIVE SELL FAILED {symbol}: {exc}")
            _notify_telegram(
                _format_failure_message(strategy_name=strategy_name, symbol=symbol, side="SELL", error=str(exc))
            )
            return {"action": "LIVE_SELL_FAILED", "reason": str(exc)}

        executed_qty = _decimal_field(order, "executedQty")
        quote_received = _decimal_field(order, "cummulativeQuoteQty")
        if executed_qty <= 0:
            _notify_telegram(
                _format_failure_message(
                    strategy_name=strategy_name, symbol=symbol, side="SELL", error="Order filled zero quantity."
                )
            )
            return {"action": "LIVE_SELL_FAILED", "reason": "Order filled zero quantity."}

        session["usdt_balance"] += quote_received
        session["base_balance"] -= executed_qty
        if session["base_balance"] < 0:
            session["base_balance"] = Decimal("0")
        _notify_telegram(
            _format_fill_message(
                strategy_name=strategy_name,
                symbol=symbol,
                side="SELL",
                price=decision.price,
                quantity=executed_qty,
                quote_amount=quote_received,
                reason=decision.reason,
            )
        )
        return {
            "action": "LIVE_SELL",
            "price": str(decision.price),
            "reason": decision.reason,
            "order_id": order.get("orderId"),
            "executed_qty": str(executed_qty),
            "quote_received": str(quote_received),
        }


def _decimal_field(order: dict[str, object], key: str) -> Decimal:
    try:
        return Decimal(str(order.get(key, "0")))
    except Exception:  # noqa: BLE001 - malformed order response, treat as zero fill
        return Decimal("0")


def _strategy_name(slug: object) -> str:
    definition = LIVE_BOT_DEFINITIONS.get(slug) if isinstance(slug, str) else None
    if definition is not None:
        return str(definition.get("name", slug))
    return str(slug) if slug else "Unknown strategy"


def _format_fill_message(
    *,
    strategy_name: str,
    symbol: str,
    side: str,
    price: Decimal,
    quantity: Decimal,
    quote_amount: Decimal,
    reason: str,
) -> str:
    return "\n".join(
        [
            f"CoinPilot LIVE Trade -- {side}",
            "",
            f"Strategy: {strategy_name}",
            f"Symbol: {symbol}",
            f"Price: {format_price_usd(price)}",
            f"Quantity: {format_coin_amount(quantity)} ({format_currency_usd(quote_amount)})",
            f"Reason: {reason}",
            "",
            "Real order. Real funds.",
        ]
    )


def _format_failure_message(*, strategy_name: str, symbol: str, side: str, error: str) -> str:
    return "\n".join(
        [
            f"CoinPilot LIVE Trade -- {side} FAILED",
            "",
            f"Strategy: {strategy_name}",
            f"Symbol: {symbol}",
            f"Error: {error}",
            "",
            "Order attempt only. No funds moved.",
        ]
    )


def _notify_telegram(message: str) -> None:
    config = load_telegram_config_from_env()
    if config is None:
        return
    try:
        send_telegram_message(message, config)
    except TelegramSendError as exc:
        # Telegram is a monitoring side channel -- never let it block or
        # break the actual trading cycle.
        print(f"Live trader Telegram notification failed: {exc}")


_ENGINE: TradingEngine | None = None


def _get_engine() -> TradingEngine | None:
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    config = load_live_trading_config_from_env()
    if config is None:
        return None
    _ENGINE = TradingEngine(executor=RealExecutor(config), mode_filter=TRADING_MODE_LIVE)
    return _ENGINE


def run_live_real_trading_once() -> dict[str, Any]:
    """Run one cycle across every enabled live-mode bot/symbol. Returns the runtime payload written."""

    engine = _get_engine()
    if engine is None:
        print("Live real trader: BINANCE_LIVE_API_KEY/BINANCE_LIVE_API_SECRET not set -- skipping cycle, no orders placed.")
        return {}
    runtime = engine.run_cycle()
    write_live_trade_runtime(runtime)
    return runtime


def run_live_real_trading_loop() -> int:
    """Run continuously until Ctrl+C. Places real Binance market orders -- see module docstring."""

    print("CoinPilot LIVE REAL-MONEY trading loop started.")
    print("Safety: only bots with mode=='live' AND enabled==True are traded. Real Binance market orders.")
    try:
        while True:
            try:
                run_live_real_trading_once()
                write_success_heartbeat(
                    path=LIVE_REAL_TRADER_HEALTH_PATH,
                    service="live_real_trader",
                    interval_seconds=POLL_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001 - keep the loop alive.
                message = f"{type(exc).__name__}: {exc}"
                print(f"Live real trader error: {message}")
                write_error_heartbeat(
                    path=LIVE_REAL_TRADER_HEALTH_PATH,
                    service="live_real_trader",
                    interval_seconds=POLL_SECONDS,
                    error_message=message,
                )
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("Stopping CoinPilot live real trader.")
        return 0
