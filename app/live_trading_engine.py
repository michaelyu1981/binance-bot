"""Shared trading engine driving both the paper and real trading loops.

`app/live_paper_trader.py` (virtual fills, mode == "simulated") and
`app/live_real_trader.py` (real Binance market orders, mode == "live") both
drive strategy decisions through this same `TradingEngine`, so a bot's
behavior -- when to buy, when to sell, how much -- is identical in both
modes. The only difference between them is which `TradeExecutor` is plugged
in: `PaperExecutor` (in live_paper_trader.py) simulates a fill with an
estimated fee against a virtual balance; `RealExecutor` (in
live_real_trader.py) places an actual market order and records whatever
Binance actually filled. This module itself never calls a Binance order
endpoint -- it only holds session bookkeeping and delegates every buy/sell
to whichever executor its caller constructed it with.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from app.backtesting import DEFAULT_BACKTEST_DB_PATH
from app.binance_reader import BinancePublicMarketError, Candle, fetch_public_candles
from app.candle_store import upsert_candles
from app.live_bot_state import LIVE_BOT_DEFINITIONS, TRADING_MODE_SIMULATED, read_live_bot_config
from app.strategies.claude_modified_martingale_atr import ClaudeModifiedMartingaleATR
from app.strategies.claude_modified_martingale_rsi import ClaudeModifiedMartingaleRSI
from app.strategies.claude_triad_confluence_v5 import ClaudeTriadConfluenceV5


SEED_CANDLE_LIMIT = 100
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


class TradeExecutor(Protocol):
    """Executes a strategy's buy/sell decision against a session's balance.

    Implementations mutate `session["usdt_balance"]`/`session["base_balance"]`
    and call `session["machine"].record_fill(...)` if present, then return a
    trade-log record dict, or None if nothing was actually filled.
    """

    def buy(self, *, session: dict[str, Any], symbol: str, decision: Any) -> dict[str, Any] | None: ...

    def sell(self, *, session: dict[str, Any], symbol: str, decision: Any) -> dict[str, Any] | None: ...


class TradingEngine:
    """Runs one bot family (paper or live) -- owns its own in-memory session state.

    Turn Off / Turn On is a hard reset by design: turning a bot off
    immediately forgets all of its sessions (every symbol), and turning it
    back on always rebuilds fresh sessions from whatever capital/params/
    symbols are configured *at that moment* -- never a session resumed from
    before it was last enabled. See `_PREVIOUSLY_ENABLED`-equivalent tracking
    below.

    Known limitation: session state (ladder level, average price, running
    indicator averages, virtual/live balance bookkeeping) lives only in this
    process's memory. A restart of the loop process resets every currently
    running bot to idle, same as an off/on toggle would.
    """

    def __init__(self, *, executor: TradeExecutor, mode_filter: str):
        self._executor = executor
        self._mode_filter = mode_filter
        self._sessions: dict[tuple[str, str], dict[str, Any]] = {}
        self._previously_enabled: dict[str, bool] = {}

    def _clear_sessions_for_bot(self, slug: str) -> None:
        stale_keys = [key for key in self._sessions if key[0] == slug]
        for key in stale_keys:
            del self._sessions[key]

    def _seed_session(self, session: dict[str, Any], symbol: str, interval: str) -> None:
        """Warm up RSI/ATR running averages with recent history so a freshly
        toggled-on bot doesn't sit idle for ~14 candle-intervals before its
        indicators are ready. Seeding candles run through the exact same
        `_process_candle` fill logic as live candles, keeping the machine's
        ladder state and the session's own balance bookkeeping consistent.
        """

        try:
            history = fetch_public_candles(symbol, interval, limit=SEED_CANDLE_LIMIT)
        except BinancePublicMarketError:
            return
        upsert_candles(history, db_path=DEFAULT_BACKTEST_DB_PATH)
        if len(history) < 2:
            return
        # Exclude the last candle: it may still be forming and will be
        # re-fetched and processed normally as "the latest closed candle" on
        # the first real cycle.
        for candle in history[:-1]:
            self._process_candle(session, symbol, candle)
        session["last_open_time_ms"] = history[-2].open_time_ms

    def _get_session(
        self,
        slug: str,
        symbol: str,
        *,
        capital: Decimal,
        params: dict[str, Any],
        interval: str,
    ) -> dict[str, Any]:
        key = (slug, symbol)
        if key not in self._sessions:
            builder = _STRATEGY_BUILDERS[slug]
            session: dict[str, Any] = {
                "machine": builder(capital, params),
                "usdt_balance": capital,
                "base_balance": Decimal("0"),
                "last_open_time_ms": None,
                "trade_log": [],
            }
            self._seed_session(session, symbol, interval)
            self._sessions[key] = session
        return self._sessions[key]

    def _process_candle(self, session: dict[str, Any], symbol: str, candle: Candle) -> None:
        """Feed one closed candle to the strategy and delegate any resulting fill to the executor."""

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
            record = self._executor.buy(session=session, symbol=symbol, decision=decision)
        elif decision.action == "SIMULATED_SELL" and decision.price is not None and session["base_balance"] > 0:
            record = self._executor.sell(session=session, symbol=symbol, decision=decision)

        if record is not None:
            record["timestamp_ms"] = candle.open_time_ms
            session["trade_log"].append(record)
            session["trade_log"] = session["trade_log"][-RECENT_TRADES_KEPT:]

    @staticmethod
    def _position_reference_lines(machine: Any, take_profit_percent: Decimal | None) -> list[dict[str, str]]:
        """Reference lines to overlay on this bot's chart, if a position is currently open.

        The Martingale ladders (RSI/ATR) track an average cost basis and a
        fixed take-profit percent above it. Triad Confluence V5 has no
        ladder -- it tracks a single entry price and an ATR-based stop
        instead. Each family gets the overlay that actually matches what
        it's waiting on.
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

    def run_cycle(self) -> dict[str, Any]:
        """Run one cycle across every bot whose config matches this engine's mode filter."""

        config = read_live_bot_config()
        runtime: dict[str, Any] = {}

        for slug in LIVE_BOT_DEFINITIONS:
            bot_config = config.get(slug)
            mode = bot_config.get("mode", TRADING_MODE_SIMULATED) if isinstance(bot_config, dict) else TRADING_MODE_SIMULATED
            enabled = (
                isinstance(bot_config, dict)
                and bool(bot_config.get("enabled"))
                and mode == self._mode_filter
            )
            was_enabled = self._previously_enabled.get(slug, False)

            if not enabled:
                if was_enabled:
                    # Just turned off (or switched mode away): stop and
                    # forget immediately, don't wait for a future re-enable
                    # to clean up after it.
                    self._clear_sessions_for_bot(slug)
                self._previously_enabled[slug] = False
                continue

            if not was_enabled:
                # Just turned on: guarantee a clean rebuild under whatever
                # is configured right now, never a session resumed from
                # before it was last enabled.
                self._clear_sessions_for_bot(slug)
            self._previously_enabled[slug] = True

            interval = str(bot_config.get("interval") or LIVE_BOT_DEFINITIONS[slug]["recommended_interval"])
            capital_by_symbol = bot_config.get("capital_by_symbol") or {}
            params = bot_config.get("params") or {}
            symbols = bot_config.get("symbols") or []

            take_profit_percent_raw = params.get("take_profit_percent")
            take_profit_percent = Decimal(str(take_profit_percent_raw)) if take_profit_percent_raw is not None else None

            bot_runtime: dict[str, Any] = {}
            for symbol in symbols:
                capital = Decimal(str(capital_by_symbol.get(symbol, "1000")))
                session = self._get_session(slug, symbol, capital=capital, params=params, interval=interval)
                try:
                    candles = fetch_public_candles(symbol, interval, limit=2)
                except BinancePublicMarketError as exc:
                    bot_runtime[symbol] = {"error": str(exc)}
                    continue
                upsert_candles(candles, db_path=DEFAULT_BACKTEST_DB_PATH)

                if len(candles) >= 2:
                    latest_closed = candles[-2]  # the last entry may still be the currently-forming candle
                    if session["last_open_time_ms"] != latest_closed.open_time_ms:
                        self._process_candle(session, symbol, latest_closed)
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
                    "reference_lines": self._position_reference_lines(session["machine"], take_profit_percent),
                    "recent_trades": session["trade_log"],
                }
            runtime[slug] = bot_runtime

        return runtime
