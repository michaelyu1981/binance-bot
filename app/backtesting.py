"""Deterministic dry-run backtesting for CoinPilot strategies.

Backtests use local public candle data only. They do not use Binance account
data, do not call order endpoints, and must not place real orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3

from app.config import PUBLIC_MARKET_WATCHLIST
from app.strategies import STRATEGIES, StrategyDefinition, strategy_by_slug
from app.strategies.claude_triad_confluence import ClaudeTriadConfluence
from app.strategies.claude_triad_confluence_v2 import ClaudeTriadConfluenceV2
from app.strategies.claude_triad_confluence_v3 import ClaudeTriadConfluenceV3
from app.strategies.claude_triad_confluence_v4 import ClaudeTriadConfluenceV4
from app.strategies.claude_triad_confluence_v5 import ClaudeTriadConfluenceV5


DEFAULT_BACKTEST_STARTING_USDT = Decimal("100")
DEFAULT_BACKTEST_FEE_RATE = Decimal("0.001")
DEFAULT_BACKTEST_INTERVAL = "1h"
DEFAULT_BACKTEST_DAYS = 90
DEFAULT_BACKTEST_DB_PATH = Path("data/historical_market_data.sqlite3")
BACKTEST_PERIODS = (90, 365, 730)
MIN_CANDLES_FOR_SIGNALS = 60

CLAUDE_STATEFUL_MACHINES = {
    "claude_triad_confluence": ClaudeTriadConfluence,
    "claude_triad_confluence_v2": ClaudeTriadConfluenceV2,
    "claude_triad_confluence_v3": ClaudeTriadConfluenceV3,
    "claude_triad_confluence_v4": ClaudeTriadConfluenceV4,
    "claude_triad_confluence_v5": ClaudeTriadConfluenceV5,
}


@dataclass(frozen=True)
class BacktestCandle:
    symbol: str
    interval: str
    open_time_ms: int
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal


@dataclass(frozen=True)
class BacktestTrade:
    timestamp_ms: int
    action: str
    price: Decimal
    usdt_balance: Decimal
    base_balance: Decimal
    reason: str


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    strategy: StrategyDefinition
    interval: str
    requested_days: int
    available_days: Decimal | None
    starting_usdt: Decimal
    ending_value_usdt: Decimal
    profit_loss_usdt: Decimal
    profit_loss_percent: Decimal
    trades: tuple[BacktestTrade, ...]
    candles_used: int
    skipped_reason: str | None


def run_backtests(
    *,
    days: int = DEFAULT_BACKTEST_DAYS,
    interval: str = DEFAULT_BACKTEST_INTERVAL,
    symbols: tuple[str, ...] = PUBLIC_MARKET_WATCHLIST,
    strategy_slug: str = "all",
    starting_usdt: Decimal = DEFAULT_BACKTEST_STARTING_USDT,
    db_path: Path = DEFAULT_BACKTEST_DB_PATH,
) -> tuple[BacktestResult, ...]:
    """Run deterministic local backtests for symbols and strategies."""

    strategies = STRATEGIES if strategy_slug == "all" else (strategy_by_slug(strategy_slug),)
    results: list[BacktestResult] = []
    for symbol in symbols:
        candles = load_backtest_candles(
            symbol=symbol,
            interval=interval,
            days=days,
            db_path=db_path,
        )
        for strategy in strategies:
            results.append(
                run_strategy_backtest(
                    symbol=symbol,
                    strategy=strategy,
                    candles=candles,
                    interval=interval,
                    requested_days=days,
                    starting_usdt=starting_usdt,
                )
            )
    return tuple(results)


def load_backtest_candles(
    *,
    symbol: str,
    interval: str,
    days: int,
    db_path: Path = DEFAULT_BACKTEST_DB_PATH,
) -> tuple[BacktestCandle, ...]:
    """Load local candles for a backtest window."""

    if not db_path.exists():
        return ()
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    try:
        connection = sqlite3.connect(db_path)
        rows = connection.execute(
            """
            SELECT symbol, interval, open_time_ms, open, high, low, close, volume
            FROM candles
            WHERE symbol = ? AND interval = ? AND open_time_ms >= ?
            ORDER BY open_time_ms ASC
            """,
            (symbol, interval, cutoff_ms),
        ).fetchall()
    except sqlite3.Error:
        return ()
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass

    return tuple(
        BacktestCandle(
            symbol=row[0],
            interval=row[1],
            open_time_ms=int(row[2]),
            open_price=Decimal(row[3]),
            high_price=Decimal(row[4]),
            low_price=Decimal(row[5]),
            close_price=Decimal(row[6]),
            volume=Decimal(row[7]),
        )
        for row in rows
    )


def run_strategy_backtest(
    *,
    symbol: str,
    strategy: StrategyDefinition,
    candles: tuple[BacktestCandle, ...],
    interval: str,
    requested_days: int,
    starting_usdt: Decimal,
) -> BacktestResult:
    """Run one deterministic strategy simulation over candles."""

    available_days = _available_days(candles)
    if len(candles) < MIN_CANDLES_FOR_SIGNALS:
        return _skipped_result(
            symbol=symbol,
            strategy=strategy,
            interval=interval,
            requested_days=requested_days,
            available_days=available_days,
            starting_usdt=starting_usdt,
            candles_used=len(candles),
            reason=f"Not enough candles for indicators. Need at least {MIN_CANDLES_FOR_SIGNALS}.",
        )
    if available_days is not None and available_days < Decimal(str(requested_days)) * Decimal("0.8"):
        return _skipped_result(
            symbol=symbol,
            strategy=strategy,
            interval=interval,
            requested_days=requested_days,
            available_days=available_days,
            starting_usdt=starting_usdt,
            candles_used=len(candles),
            reason=f"Only {available_days:.1f} days available locally for requested {requested_days} days.",
        )

    if strategy.slug not in CLAUDE_STATEFUL_MACHINES:
        return _skipped_result(
            symbol=symbol,
            strategy=strategy,
            interval=interval,
            requested_days=requested_days,
            available_days=available_days,
            starting_usdt=starting_usdt,
            candles_used=len(candles),
            reason=f"No stateful backtest registered for strategy '{strategy.slug}'.",
        )

    return _run_claude_stateful_backtest(
        symbol=symbol,
        strategy=strategy,
        candles=candles,
        interval=interval,
        requested_days=requested_days,
        available_days=available_days,
        starting_usdt=starting_usdt,
    )


def _run_claude_stateful_backtest(
    *,
    symbol: str,
    strategy: StrategyDefinition,
    candles: tuple[BacktestCandle, ...],
    interval: str,
    requested_days: int,
    available_days: Decimal | None,
    starting_usdt: Decimal,
) -> BacktestResult:
    """Feed raw candles to a self-contained Claude machine with O(1) tick state."""

    machine = CLAUDE_STATEFUL_MACHINES[strategy.slug]()
    usdt_balance = starting_usdt
    base_balance = Decimal("0")
    trades: list[BacktestTrade] = []

    for candle in candles:
        decision = machine.on_candle_tick(
            {
                "open": candle.open_price,
                "high": candle.high_price,
                "low": candle.low_price,
                "close": candle.close_price,
                "volume": candle.volume,
                "open_time_ms": candle.open_time_ms,
            }
        )
        if decision.action == "SIMULATED_BUY" and decision.price is not None and usdt_balance > 0:
            spend = usdt_balance if decision.fraction is None else usdt_balance * decision.fraction
            if spend <= 0:
                continue
            fee = spend * DEFAULT_BACKTEST_FEE_RATE
            base_bought = (spend - fee) / decision.price
            usdt_balance -= spend
            base_balance += base_bought
            record_fill = getattr(machine, "record_fill", None)
            if record_fill is not None:
                record_fill(usdt_spent=spend, base_bought=base_bought)
            trades.append(
                BacktestTrade(
                    timestamp_ms=candle.open_time_ms,
                    action="SIMULATED_BUY",
                    price=decision.price,
                    usdt_balance=usdt_balance,
                    base_balance=base_balance,
                    reason=decision.reason,
                )
            )
        elif decision.action == "SIMULATED_SELL" and decision.price is not None and base_balance > 0:
            gross_usdt = base_balance * decision.price
            fee = gross_usdt * DEFAULT_BACKTEST_FEE_RATE
            usdt_balance += gross_usdt - fee
            base_balance = Decimal("0")
            trades.append(
                BacktestTrade(
                    timestamp_ms=candle.open_time_ms,
                    action="SIMULATED_SELL",
                    price=decision.price,
                    usdt_balance=usdt_balance,
                    base_balance=base_balance,
                    reason=decision.reason,
                )
            )

    last_close = candles[-1].close_price
    ending_value = usdt_balance + (base_balance * last_close)
    profit_loss = ending_value - starting_usdt
    profit_loss_percent = (profit_loss / starting_usdt) * Decimal("100") if starting_usdt else Decimal("0")
    return BacktestResult(
        symbol=symbol,
        strategy=strategy,
        interval=interval,
        requested_days=requested_days,
        available_days=available_days,
        starting_usdt=starting_usdt,
        ending_value_usdt=ending_value,
        profit_loss_usdt=profit_loss,
        profit_loss_percent=profit_loss_percent,
        trades=tuple(trades),
        candles_used=len(candles),
        skipped_reason=None,
    )


def format_backtest_results(results: tuple[BacktestResult, ...]) -> str:
    """Format backtest results for CLI output."""

    lines = [
        "CoinPilot deterministic backtest",
        "Safety: local public candle data only; no API key; no account access; no orders.",
        "Assumption: Backtest v1 uses simulated market entries/exits and 0.1% fee.",
        "",
    ]
    for result in results:
        status = result.skipped_reason or (
            f"ending {result.ending_value_usdt:.2f} USDT "
            f"({result.profit_loss_percent:+.2f}%)"
        )
        lines.append(
            f"{result.symbol} | {result.strategy.name} | {result.requested_days}d | "
            f"{result.candles_used} candles | {status}"
        )
    return "\n".join(lines)


def _available_days(candles: tuple[BacktestCandle, ...]) -> Decimal | None:
    if len(candles) < 2:
        return None
    seconds = Decimal(candles[-1].open_time_ms - candles[0].open_time_ms) / Decimal("1000")
    return seconds / Decimal("86400")


def _skipped_result(
    *,
    symbol: str,
    strategy: StrategyDefinition,
    interval: str,
    requested_days: int,
    available_days: Decimal | None,
    starting_usdt: Decimal,
    candles_used: int,
    reason: str,
) -> BacktestResult:
    return BacktestResult(
        symbol=symbol,
        strategy=strategy,
        interval=interval,
        requested_days=requested_days,
        available_days=available_days,
        starting_usdt=starting_usdt,
        ending_value_usdt=starting_usdt,
        profit_loss_usdt=Decimal("0"),
        profit_loss_percent=Decimal("0"),
        trades=(),
        candles_used=candles_used,
        skipped_reason=reason,
    )
