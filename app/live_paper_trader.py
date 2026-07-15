"""Live paper-trading loop for the 3 approved CoinPilot strategies.

Every cycle, reads `data/live_bot_config.json` (written by the LiveBotTrader
dashboard tab). For each bot the user has toggled on, fetches the latest
closed candle at that bot's configured interval for every enabled symbol,
feeds it to a persistent strategy instance, and simulates the resulting
decision against a virtual per-symbol USDT/base balance -- the exact same
fee model and fill logic as `app/backtesting.py`, just one live candle at a
time instead of replaying history.

PAPER TRADING ONLY. This file must never call a Binance order endpoint, use
signed/account credentials, or place a real trade. Enabling real order
execution requires Michael's exact phrase "enable live spot trading." per
`docs/binance-api-key-policy.md` and is not implemented anywhere in this
file.

Known limitation: each strategy instance's ladder state (level, average
price, RSI/ATR running averages) lives only in this process's memory. A
restart of this loop resets every bot to idle. Persisting that internal
state across restarts is future work, not implemented here.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import time
from typing import Any

from app.backtesting import DEFAULT_BACKTEST_DB_PATH
from app.binance_reader import BinancePublicMarketError, Candle, fetch_public_candles
from app.candle_store import upsert_candles
from app.health import write_error_heartbeat, write_success_heartbeat
from app.live_bot_state import (
    LIVE_BOT_DEFINITIONS,
    read_live_bot_config,
    write_live_bot_runtime,
)
from app.strategies.claude_modified_martingale_atr import ClaudeModifiedMartingaleATR
from app.strategies.claude_modified_martingale_rsi import ClaudeModifiedMartingaleRSI
from app.strategies.claude_triad_confluence_v5 import ClaudeTriadConfluenceV5


LIVE_PAPER_TRADER_HEALTH_PATH = Path("data/health/live_paper_trader.json")
POLL_SECONDS = 120
FEE_RATE = Decimal("0.001")
# Kept in the runtime JSON so the dashboard's scrollable trade-history table
# has real history to show, not just the last handful of fills.
RECENT_TRADES_KEPT = 300


def _build_rsi_machine(capital: Decimal, params: dict[str, Any]) -> ClaudeModifiedMartingaleRSI:
    return ClaudeModifiedMartingaleRSI(
        total_capital_usd=capital,
        take_profit_percent=Decimal(str(params.get("take_profit_percent", "4.5"))),
        rsi_entry_max=Decimal(str(params.get("rsi_entry_max", "50"))),
        step_drop_percent=Decimal(str(params.get("step_drop_percent", "2.0"))),
        bull_reentry_min_body_percent=Decimal(str(params.get("bull_reentry_min_body_percent", "0.3"))),
    )


def _build_atr_machine(capital: Decimal, params: dict[str, Any]) -> ClaudeModifiedMartingaleATR:
    return ClaudeModifiedMartingaleATR(
        total_capital_usd=capital,
        take_profit_percent=Decimal(str(params.get("take_profit_percent", "2.5"))),
        atr_multiplier=Decimal(str(params.get("atr_multiplier", "2.2"))),
        rsi_entry_max=Decimal(str(params.get("rsi_entry_max", "50"))),
    )


def _build_confluence_machine(capital: Decimal, params: dict[str, Any]) -> ClaudeTriadConfluenceV5:
    return ClaudeTriadConfluenceV5()


_STRATEGY_BUILDERS = {
    "claude_modified_martingale_rsi": _build_rsi_machine,
    "claude_modified_martingale_atr": _build_atr_machine,
    "claude_triad_confluence_v5": _build_confluence_machine,
}

# In-memory only for this process's lifetime -- see the "Known limitation" note above.
_SESSIONS: dict[tuple[str, str], dict[str, Any]] = {}


SEED_CANDLE_LIMIT = 100


def _seed_session(session: dict[str, Any], symbol: str, interval: str) -> None:
    """Warm up RSI/ATR running averages with recent history so a freshly
    toggled-on bot doesn't sit idle for ~14 candle-intervals before its
    indicators are ready.

    Seeding candles run through the exact same `_process_candle` fill logic
    as live candles (not a bare `on_candle_tick` call) -- otherwise the
    strategy's internal ladder (level, average price) could end up "in a
    position" from historical price action while the session's own virtual
    usdt/base balance never recorded the corresponding fill, leaving the two
    inconsistent. Running the real fill logic keeps them in sync, and
    correctly reflects what this bot's position would actually be if it had
    been running over that history.
    """

    try:
        history = fetch_public_candles(symbol, interval, limit=SEED_CANDLE_LIMIT)
    except BinancePublicMarketError:
        return
    upsert_candles(history, db_path=DEFAULT_BACKTEST_DB_PATH)
    if len(history) < 2:
        return
    # Exclude the last candle: it may still be forming and will be re-fetched
    # and processed normally as "the latest closed candle" on the first real cycle.
    for candle in history[:-1]:
        _process_candle(session, candle)
    session["last_open_time_ms"] = history[-2].open_time_ms


def _get_session(slug: str, symbol: str, *, capital: Decimal, params: dict[str, Any], interval: str) -> dict[str, Any]:
    key = (slug, symbol)
    if key not in _SESSIONS:
        builder = _STRATEGY_BUILDERS[slug]
        session: dict[str, Any] = {
            "machine": builder(capital, params),
            "usdt_balance": capital,
            "base_balance": Decimal("0"),
            "last_open_time_ms": None,
            "trade_log": [],
        }
        _seed_session(session, symbol, interval)
        _SESSIONS[key] = session
    return _SESSIONS[key]


def _process_candle(session: dict[str, Any], candle: Candle) -> None:
    """Feed one closed candle to the strategy and simulate any resulting fill."""

    machine = session["machine"]
    candle_dict = {
        "open": candle.open_price,
        "high": candle.high_price,
        "low": candle.low_price,
        "close": candle.close_price,
        "volume": candle.volume,
        "open_time_ms": candle.open_time_ms,
    }
    decision = machine.on_candle_tick(candle_dict)
    record: dict[str, Any] | None = None

    if decision.action == "SIMULATED_BUY" and decision.price is not None and session["usdt_balance"] > 0:
        spend = session["usdt_balance"] if decision.fraction is None else session["usdt_balance"] * decision.fraction
        if spend > 0:
            fee = spend * FEE_RATE
            base_bought = (spend - fee) / decision.price
            session["usdt_balance"] -= spend
            session["base_balance"] += base_bought
            record_fill = getattr(machine, "record_fill", None)
            if record_fill is not None:
                record_fill(usdt_spent=spend, base_bought=base_bought)
            record = {"action": "SIMULATED_BUY", "price": str(decision.price), "reason": decision.reason}
    elif decision.action == "SIMULATED_SELL" and decision.price is not None and session["base_balance"] > 0:
        gross_usdt = session["base_balance"] * decision.price
        fee = gross_usdt * FEE_RATE
        session["usdt_balance"] += gross_usdt - fee
        session["base_balance"] = Decimal("0")
        record = {"action": "SIMULATED_SELL", "price": str(decision.price), "reason": decision.reason}

    if record is not None:
        record["timestamp_ms"] = candle.open_time_ms
        session["trade_log"].append(record)
        session["trade_log"] = session["trade_log"][-RECENT_TRADES_KEPT:]


def _position_reference_lines(machine: Any, take_profit_percent: Decimal | None) -> list[dict[str, str]]:
    """Reference lines to overlay on this bot's chart, if a position is currently open.

    The Martingale ladders (RSI/ATR) track an average cost basis and a fixed
    take-profit percent above it. Triad Confluence V5 has no ladder -- it
    tracks a single entry price and an ATR-based stop instead. Each family
    gets the overlay that actually matches what it's waiting on.
    """

    average_price = getattr(machine, "average_price", None)
    if average_price is not None and average_price > 0:
        lines = [{"label": "Avg Entry", "price": str(average_price)}]
        if take_profit_percent is not None:
            take_profit_price = average_price * (Decimal("1") + take_profit_percent / Decimal("100"))
            lines.append({"label": "Take Profit", "price": str(take_profit_price)})
        return lines

    if getattr(machine, "is_in_position", False):
        entry_price = getattr(machine, "entry_price", None)
        stop_price = getattr(machine, "stop_price", None)
        lines = []
        if entry_price:
            lines.append({"label": "Entry", "price": str(entry_price)})
        if stop_price:
            lines.append({"label": "Stop", "price": str(stop_price)})
        return lines

    return []


def run_live_paper_trading_once() -> dict[str, Any]:
    """Run one cycle across every enabled bot/symbol. Returns the runtime payload written."""

    config = read_live_bot_config()
    runtime: dict[str, Any] = {}

    for slug in LIVE_BOT_DEFINITIONS:
        bot_config = config.get(slug)
        if not isinstance(bot_config, dict) or not bot_config.get("enabled"):
            continue

        interval = str(bot_config.get("interval") or LIVE_BOT_DEFINITIONS[slug]["recommended_interval"])
        capital_by_symbol = bot_config.get("capital_by_symbol") or {}
        params = bot_config.get("params") or {}
        symbols = bot_config.get("symbols") or []

        take_profit_percent_raw = params.get("take_profit_percent")
        take_profit_percent = Decimal(str(take_profit_percent_raw)) if take_profit_percent_raw is not None else None

        bot_runtime: dict[str, Any] = {}
        for symbol in symbols:
            capital = Decimal(str(capital_by_symbol.get(symbol, "1000")))
            session = _get_session(slug, symbol, capital=capital, params=params, interval=interval)
            try:
                candles = fetch_public_candles(symbol, interval, limit=2)
            except BinancePublicMarketError as exc:
                bot_runtime[symbol] = {"error": str(exc)}
                continue
            upsert_candles(candles, db_path=DEFAULT_BACKTEST_DB_PATH)

            if len(candles) >= 2:
                latest_closed = candles[-2]  # the last entry may still be the currently-forming candle
                if session["last_open_time_ms"] != latest_closed.open_time_ms:
                    _process_candle(session, latest_closed)
                    session["last_open_time_ms"] = latest_closed.open_time_ms

            last_price = candles[-1].close_price if candles else None
            current_value = (
                session["usdt_balance"] + session["base_balance"] * last_price
                if last_price is not None
                else session["usdt_balance"]
            )
            bot_runtime[symbol] = {
                "usdt_balance": str(session["usdt_balance"]),
                "base_balance": str(session["base_balance"]),
                "current_price": str(last_price) if last_price is not None else None,
                "current_value": str(current_value),
                "reference_lines": _position_reference_lines(session["machine"], take_profit_percent),
                "recent_trades": session["trade_log"],
            }
        runtime[slug] = bot_runtime

    write_live_bot_runtime(runtime)
    return runtime


def run_live_paper_trading_loop() -> int:
    """Run continuously until Ctrl+C. Paper trading only -- see module docstring."""

    print("CoinPilot live paper-trading loop started.")
    print("Safety: PAPER TRADING ONLY. No real orders. No account API. No withdrawals.")
    try:
        while True:
            try:
                run_live_paper_trading_once()
                write_success_heartbeat(
                    path=LIVE_PAPER_TRADER_HEALTH_PATH,
                    service="live_paper_trader",
                    interval_seconds=POLL_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001 - keep the loop alive.
                message = f"{type(exc).__name__}: {exc}"
                print(f"Live paper trader error: {message}")
                write_error_heartbeat(
                    path=LIVE_PAPER_TRADER_HEALTH_PATH,
                    service="live_paper_trader",
                    interval_seconds=POLL_SECONDS,
                    error_message=message,
                )
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("Stopping CoinPilot live paper trader.")
        return 0
