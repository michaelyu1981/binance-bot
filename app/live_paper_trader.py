"""Live paper-trading loop for the 3 approved CoinPilot strategies.

Every cycle, drives `app.live_trading_engine.TradingEngine` for every bot the
user has toggled on with mode == "simulated" (see `app/live_bot_state.py`).
The engine feeds each bot's strategy instance the latest closed candle and
calls back into `PaperExecutor` below, which simulates the resulting fill
against a virtual per-symbol USDT/base balance -- the exact same fee model
and fill logic as `app/backtesting.py`, just one live candle at a time
instead of replaying history.

PAPER TRADING ONLY. `PaperExecutor` never calls a Binance order endpoint,
uses signed/account credentials, or places a real trade -- it only ever
mutates the in-memory virtual balance in `session`. Bots with mode == "live"
are entirely out of scope for this file; see `app/live_real_trader.py` for
that (which places real orders through `app/binance_trader.py`, gated by
Michael's exact phrase "enable live spot trading." per
docs/binance-api-key-policy.md).

Turn Off / Turn On is a hard reset by design -- see
`app.live_trading_engine.TradingEngine` docstring.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import time
from typing import Any

from app.health import write_error_heartbeat, write_success_heartbeat
from app.live_bot_state import TRADING_MODE_SIMULATED, write_live_bot_runtime
from app.live_trading_engine import TradingEngine


LIVE_PAPER_TRADER_HEALTH_PATH = Path("data/health/live_paper_trader.json")
POLL_SECONDS = 120
FEE_RATE = Decimal("0.001")


class PaperExecutor:
    """Simulates a fill against a virtual per-symbol balance. Never touches Binance orders."""

    def buy(self, *, session: dict[str, Any], symbol: str, decision: Any) -> dict[str, Any] | None:
        spend = session["usdt_balance"] if decision.fraction is None else session["usdt_balance"] * decision.fraction
        if spend <= 0:
            return None
        fee = spend * FEE_RATE
        base_bought = (spend - fee) / decision.price
        session["usdt_balance"] -= spend
        session["base_balance"] += base_bought
        record_fill = getattr(session["machine"], "record_fill", None)
        if record_fill is not None:
            record_fill(usdt_spent=spend, base_bought=base_bought)
        return {"action": "SIMULATED_BUY", "price": str(decision.price), "reason": decision.reason}

    def sell(self, *, session: dict[str, Any], symbol: str, decision: Any) -> dict[str, Any] | None:
        gross_usdt = session["base_balance"] * decision.price
        fee = gross_usdt * FEE_RATE
        session["usdt_balance"] += gross_usdt - fee
        session["base_balance"] = Decimal("0")
        return {"action": "SIMULATED_SELL", "price": str(decision.price), "reason": decision.reason}


_ENGINE = TradingEngine(executor=PaperExecutor(), mode_filter=TRADING_MODE_SIMULATED)


def run_live_paper_trading_once() -> dict[str, Any]:
    """Run one cycle across every enabled simulated-mode bot/symbol. Returns the runtime payload written."""

    runtime = _ENGINE.run_cycle()
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
