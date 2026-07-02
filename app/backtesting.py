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
from app.indicators import build_indicator_snapshot
from app.signals import build_multi_timeframe_signal_summary, build_technical_signal_guide
from app.strategies import (
    STRATEGIES,
    StrategyDefinition,
    build_strategy_decision,
    strategy_by_slug,
)


DEFAULT_BACKTEST_STARTING_USDT = Decimal("100")
DEFAULT_BACKTEST_FEE_RATE = Decimal("0.001")
DEFAULT_BACKTEST_INTERVAL = "1h"
DEFAULT_BACKTEST_DAYS = 90
DEFAULT_BACKTEST_DB_PATH = Path("data/historical_market_data.sqlite3")
BACKTEST_PERIODS = (90, 365, 730)
ROLLING_CANDLE_WINDOW = 120
MIN_CANDLES_FOR_SIGNALS = 60
_SIGNAL_CACHE: dict[tuple[str, str, int, int], object] = {}


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

    _SIGNAL_CACHE.clear()
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

    usdt_balance = starting_usdt
    base_balance = Decimal("0")
    average_entry = Decimal("0")
    peak_value = starting_usdt
    trades: list[BacktestTrade] = []

    for index in range(MIN_CANDLES_FOR_SIGNALS - 1, len(candles)):
        window = candles[max(0, index - ROLLING_CANDLE_WINDOW + 1) : index + 1]
        candle = candles[index]
        decision = _strategy_decision_for_window(
            symbol=symbol,
            strategy=strategy,
            interval=interval,
            window=window,
        )
        current_value = usdt_balance + (base_balance * candle.close_price)
        peak_value = max(peak_value, current_value)

        if base_balance == 0 and usdt_balance > 0 and _is_entry_verdict(decision.verdict):
            spend = usdt_balance
            fee = spend * DEFAULT_BACKTEST_FEE_RATE
            net_spend = spend - fee
            base_balance = net_spend / candle.close_price
            usdt_balance = Decimal("0")
            average_entry = candle.close_price
            trades.append(
                BacktestTrade(
                    timestamp_ms=candle.open_time_ms,
                    action="SIMULATED_BUY",
                    price=candle.close_price,
                    usdt_balance=usdt_balance,
                    base_balance=base_balance,
                    reason=decision.verdict,
                )
            )
            continue

        if base_balance > 0 and _should_exit(
            strategy=strategy,
            verdict=decision.verdict,
            close_price=candle.close_price,
            average_entry=average_entry,
        ):
            gross_usdt = base_balance * candle.close_price
            fee = gross_usdt * DEFAULT_BACKTEST_FEE_RATE
            usdt_balance = gross_usdt - fee
            base_balance = Decimal("0")
            average_entry = Decimal("0")
            trades.append(
                BacktestTrade(
                    timestamp_ms=candle.open_time_ms,
                    action="SIMULATED_SELL",
                    price=candle.close_price,
                    usdt_balance=usdt_balance,
                    base_balance=base_balance,
                    reason=decision.verdict,
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


def _strategy_decision_for_window(
    *,
    symbol: str,
    strategy: StrategyDefinition,
    interval: str,
    window: tuple[BacktestCandle, ...],
):
    cache_key = (symbol, interval, window[-1].open_time_ms, len(window))
    cached = _SIGNAL_CACHE.get(cache_key)
    if cached is None:
        highs = tuple(candle.high_price for candle in window)
        lows = tuple(candle.low_price for candle in window)
        closes = tuple(candle.close_price for candle in window)
        volumes = tuple(candle.volume for candle in window)
        snapshot = build_indicator_snapshot(
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
        )
        guide = build_technical_signal_guide(
            symbol=symbol,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            snapshot=snapshot,
        )
        summary = build_multi_timeframe_signal_summary(
            symbol=symbol,
            guides_by_interval={interval: guide},
        )
        cached = (guide, summary)
        _SIGNAL_CACHE[cache_key] = cached
    guide, summary = cached
    return build_strategy_decision(
        strategy=strategy,
        symbol=symbol,
        summary=summary,
        guides_by_interval={interval: guide},
    )


def _is_entry_verdict(verdict: str) -> bool:
    return verdict in {
        "BUY WATCH",
        "BREAKOUT WATCH",
        "RECLAIM WATCH",
        "SIMULATED BUY TIER 1 WATCH",
        "GEAR 1 SNAP-BACK WATCH",
        "GEAR 2 MOMENTUM WATCH",
    }


def _should_exit(
    *,
    strategy: StrategyDefinition,
    verdict: str,
    close_price: Decimal,
    average_entry: Decimal,
) -> bool:
    if verdict in {"RISK-OFF", "DO NOT AVERAGE DOWN", "BLOCKED_BY_RISK", "AVOID"}:
        return True
    target = Decimal("1.02")
    if strategy.slug == "coinpilot_grid_accumulation_scalper_v1":
        target = Decimal("1.008")
    elif strategy.slug == "coinpilot_gear_shifting_algo_v1":
        target = Decimal("1.005")
    return average_entry > 0 and close_price >= average_entry * target


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
