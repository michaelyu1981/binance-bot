"""Claude Triad Confluence V5 -- portfolio (shared-capital) backtest.

Same portfolio construction as the V4 portfolio module (3 momentum-ranked
base slots open to any qualifying coin, plus 1 recovery slot reserved for
previously-traded coins at 3x weight -- see that module's docstring for
the full design rationale and the sweep that chose these numbers), with
two changes:

1. Runs on V5's per-coin rules (imported from
   claude_triad_confluence_v5.py), inheriting its stop-price sanity cap
   and fee-aware structural-weakness exit.

2. Fixes a latent timestamp-alignment bug: the V4 portfolio iterated all
   coins' candle lists BY INDEX, silently assuming every coin has a
   candle for every week. That holds for the current dataset (verified:
   all five coins share identical 208-candle weekly grids), but a single
   missing candle in any one coin would shift every later index and pair
   candles from different weeks across coins -- corrupting slot
   accounting without any error. V5 iterates the sorted union of
   timestamps and ticks each coin only on candles it actually has.

Dry-run only, spot/long-only. It must not execute live orders, call
exchange clients, or access Binance order endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.backtesting import DEFAULT_BACKTEST_FEE_RATE, load_backtest_candles
from app.strategies.claude_calendar_cycle import calendar_cycle_score
from app.strategies.claude_common import DonchianRegime, RollingFraction, RollingMax, RunningRsi, WilderAtr
from app.strategies.claude_pattern_signals import FalseBreakoutDetector, InverseHeadAndShouldersDetector
from app.strategies.claude_triad_confluence_v5 import (
    ATH_DRAWDOWN_BLOCK_THRESHOLD,
    ATRP_MINIMUM_PERCENT,
    BEAR_FRACTION_BLOCK_THRESHOLD,
    BEAR_FRACTION_LOOKBACK_CANDLES,
    FEE_AWARE_GAIN_BUFFER,
    MAX_STOP_DISTANCE_FRACTION,
    MIN_CALENDAR_CYCLE_SCORE,
    MOMENTUM_FLOOR_PERCENT,
    MOMENTUM_LOOKBACK_WEEKS,
    POST_EXIT_COOLDOWN_CANDLES,
    REGIME_EVENT_WINDOW,
    REGIME_LEVEL_WINDOW,
    RSI_MAX_AT_ENTRY,
    STOP_ATR_MULTIPLIER,
    SWING_WINDOW_CANDLES,
    TRAIL_ARM_ATR_MULTIPLIER,
    TRAIL_ATR_MULTIPLIER,
)
from collections import deque

ALL_TIME_HIGH_WINDOW = 10_000

DEFAULT_BASE_SLOTS = 3
DEFAULT_RECOVERY_SLOTS = 1
DEFAULT_RECOVERY_SLOT_WEIGHT = Decimal("3")


@dataclass
class _CoinSlot:
    """Mirrors ClaudeTriadConfluenceV5's entry/exit rules for one coin."""

    symbol: str
    atr: WilderAtr = field(default_factory=lambda: WilderAtr(14))
    rsi: RunningRsi = field(default_factory=lambda: RunningRsi(14))
    regime: DonchianRegime = field(default_factory=lambda: DonchianRegime(REGIME_LEVEL_WINDOW, REGIME_EVENT_WINDOW))
    inverse_hs: InverseHeadAndShouldersDetector = field(
        default_factory=lambda: InverseHeadAndShouldersDetector(SWING_WINDOW_CANDLES)
    )
    false_breakout: FalseBreakoutDetector = field(
        default_factory=lambda: FalseBreakoutDetector(SWING_WINDOW_CANDLES)
    )
    all_time_high: RollingMax = field(default_factory=lambda: RollingMax(ALL_TIME_HIGH_WINDOW))
    bear_fraction: RollingFraction = field(default_factory=lambda: RollingFraction(BEAR_FRACTION_LOOKBACK_CANDLES))
    close_history: deque = field(default_factory=lambda: deque(maxlen=MOMENTUM_LOOKBACK_WEEKS))

    is_in_position: bool = False
    has_ever_traded: bool = False
    slot_type: str | None = None  # "base" or "recovery" while held
    base_units: Decimal = Decimal("0")
    entry_price: Decimal = Decimal("0")
    stop_price: Decimal = Decimal("0")
    peak_close: Decimal = Decimal("0")
    trail_armed: bool = False
    candles_since_exit: int = 10 ** 9
    ctx: dict | None = None

    def precompute(self, candle) -> None:
        high, low, close = candle.high_price, candle.low_price, candle.close_price
        atr = self.atr.update(high, low, close)
        rsi = self.rsi.update(close)
        regime = self.regime.update(high, low)
        pattern_hs = self.inverse_hs.update(high=high, low=low, close=close)
        pattern_false_breakout = self.false_breakout.update(high=high, low=low, close=close)
        self.all_time_high.push(high)
        self.bear_fraction.push(regime == "BEAR")
        self.candles_since_exit += 1
        momentum = self._trailing_momentum(close)
        self.close_history.append(close)
        self.ctx = {
            "atr": atr,
            "rsi": rsi,
            "regime": regime,
            "pattern": pattern_hs or pattern_false_breakout,
            "close": close,
            "open_time_ms": candle.open_time_ms,
            "momentum": momentum,
        }

    def _trailing_momentum(self, close: Decimal) -> Decimal | None:
        if len(self.close_history) < MOMENTUM_LOOKBACK_WEEKS:
            return None
        past_close = self.close_history[0]
        if past_close <= 0:
            return None
        return (close / past_close - Decimal("1")) * Decimal("100")

    def _is_structurally_weak(self, close: Decimal) -> bool:
        ath = self.all_time_high.maximum
        bear_fraction = self.bear_fraction.fraction
        if ath is None or ath <= 0 or bear_fraction is None:
            return False
        drawdown_from_ath = (close / ath) - Decimal("1")
        return drawdown_from_ath < ATH_DRAWDOWN_BLOCK_THRESHOLD and bear_fraction > BEAR_FRACTION_BLOCK_THRESHOLD

    def try_exit(self) -> tuple[str, Decimal, Decimal] | None:
        """Returns (reason, price, base_units_sold) if this tick closes the position."""

        ctx = self.ctx
        atr, close, regime = ctx["atr"], ctx["close"], ctx["regime"]
        if atr is None or atr <= 0 or regime is None or not self.is_in_position:
            return None
        if regime == "BEAR":
            return self._close("Regime protection: regime turned BEAR while holding.", close)
        if self._is_structurally_weak(close) and close > self.entry_price * (Decimal("1") + FEE_AWARE_GAIN_BUFFER):
            return self._close("Structural-weakness layer: taking the available gain.", close)
        if close > self.peak_close:
            self.peak_close = close
        if not self.trail_armed and close >= self.entry_price + (atr * TRAIL_ARM_ATR_MULTIPLIER):
            self.trail_armed = True
        floor = self.stop_price
        if self.trail_armed:
            floor = max(floor, self.peak_close - (atr * TRAIL_ATR_MULTIPLIER))
        if close <= floor:
            return self._close("Stop or armed trail hit.", close)
        return None

    def _close(self, reason: str, price: Decimal) -> tuple[str, Decimal, Decimal]:
        units = self.base_units
        self.is_in_position = False
        self.slot_type = None
        self.base_units = Decimal("0")
        self.entry_price = Decimal("0")
        self.stop_price = Decimal("0")
        self.peak_close = Decimal("0")
        self.trail_armed = False
        self.candles_since_exit = 0
        return reason, price, units

    def entry_candidate_momentum(self) -> Decimal | None:
        """Returns this coin's momentum if it qualifies for entry this tick, else None."""

        ctx = self.ctx
        atr, rsi, regime, close = ctx["atr"], ctx["rsi"], ctx["regime"], ctx["close"]
        if atr is None or atr <= 0 or rsi is None or regime is None or self.is_in_position:
            return None
        if self.candles_since_exit < POST_EXIT_COOLDOWN_CANDLES or self._is_structurally_weak(close):
            return None
        pattern_signal = ctx["pattern"]
        if pattern_signal is None or regime == "BEAR":
            return None
        atrp = (atr / close) * Decimal("100")
        if atrp < ATRP_MINIMUM_PERCENT or rsi > RSI_MAX_AT_ENTRY:
            return None
        momentum = ctx["momentum"]
        if momentum is not None and momentum < MOMENTUM_FLOOR_PERCENT:
            return None
        cycle_score = calendar_cycle_score(ctx["open_time_ms"]) if ctx["open_time_ms"] is not None else Decimal("0")
        if cycle_score < MIN_CALENDAR_CYCLE_SCORE:
            return None
        return momentum if momentum is not None else Decimal("-999")

    def enter(self, usdt_allocated: Decimal, slot_type: str) -> None:
        ctx = self.ctx
        close, atr = ctx["close"], ctx["atr"]
        self.is_in_position = True
        self.has_ever_traded = True
        self.slot_type = slot_type
        self.entry_price = close
        atr_stop = close - (atr * STOP_ATR_MULTIPLIER)
        widest_allowed = close * (Decimal("1") - MAX_STOP_DISTANCE_FRACTION)
        self.stop_price = max(atr_stop, widest_allowed)
        self.peak_close = close
        self.trail_armed = False
        fee = usdt_allocated * DEFAULT_BACKTEST_FEE_RATE
        self.base_units = (usdt_allocated - fee) / close


@dataclass
class PortfolioCoinResult:
    symbol: str
    buys: int
    sells: int
    still_open: bool
    trades: list[tuple]


@dataclass
class PortfolioBacktestResult:
    starting_usdt: Decimal
    ending_usdt: Decimal
    profit_loss_percent: Decimal
    per_coin: dict[str, PortfolioCoinResult]


def run_v5_portfolio_backtest(
    *,
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "ZECUSDT", "XTZUSDT"),
    days: int = 1460,
    interval: str = "1w",
    starting_usdt: Decimal = Decimal("500"),
    base_slots: int = DEFAULT_BASE_SLOTS,
    recovery_slots: int = DEFAULT_RECOVERY_SLOTS,
    recovery_slot_weight: Decimal = DEFAULT_RECOVERY_SLOT_WEIGHT,
) -> PortfolioBacktestResult:
    all_candles = {symbol: load_backtest_candles(symbol=symbol, interval=interval, days=days) for symbol in symbols}

    # Align by timestamp, not index: tick each coin only on candles it has.
    candles_by_timestamp: dict[str, dict[int, object]] = {
        symbol: {c.open_time_ms: c for c in candles} for symbol, candles in all_candles.items()
    }
    all_timestamps = sorted({ts for per_coin in candles_by_timestamp.values() for ts in per_coin})

    slots = {symbol: _CoinSlot(symbol=symbol) for symbol in symbols}
    usdt = starting_usdt
    weight_units = Decimal(base_slots) + Decimal(recovery_slots) * recovery_slot_weight
    base_position_size = starting_usdt / weight_units
    recovery_position_size = base_position_size * recovery_slot_weight
    trades: dict[str, list[tuple]] = {symbol: [] for symbol in symbols}

    for ts in all_timestamps:
        ticked = [symbol for symbol in symbols if ts in candles_by_timestamp[symbol]]
        for symbol in ticked:
            slots[symbol].precompute(candles_by_timestamp[symbol][ts])

        for symbol in ticked:
            result = slots[symbol].try_exit()
            if result is None:
                continue
            reason, price, units = result
            gross = units * price
            proceeds = gross - (gross * DEFAULT_BACKTEST_FEE_RATE)
            usdt += proceeds
            trades[symbol].append((ts, "SIMULATED_SELL", price, reason, proceeds))

        open_base = sum(1 for symbol in symbols if slots[symbol].is_in_position and slots[symbol].slot_type == "base")
        open_recovery = sum(
            1 for symbol in symbols if slots[symbol].is_in_position and slots[symbol].slot_type == "recovery"
        )
        free_base = base_slots - open_base
        free_recovery = recovery_slots - open_recovery

        candidates = [
            (symbol, momentum)
            for symbol in ticked
            if (momentum := slots[symbol].entry_candidate_momentum()) is not None
        ]
        recovery_candidates = sorted((c for c in candidates if slots[c[0]].has_ever_traded), key=lambda c: -c[1])
        chosen_recovery = recovery_candidates[:free_recovery]
        chosen_recovery_symbols = {symbol for symbol, _ in chosen_recovery}
        base_pool = sorted(
            (c for c in candidates if c[0] not in chosen_recovery_symbols),
            key=lambda c: -c[1],
        )
        chosen_base = base_pool[:free_base]

        for symbol, _ in chosen_recovery:
            allocation = min(recovery_position_size, usdt)
            if allocation <= 0:
                continue
            slots[symbol].enter(allocation, "recovery")
            usdt -= allocation
            trades[symbol].append((ts, "SIMULATED_BUY", slots[symbol].entry_price, "entry (recovery slot)", allocation))

        for symbol, _ in chosen_base:
            allocation = min(base_position_size, usdt)
            if allocation <= 0:
                continue
            slots[symbol].enter(allocation, "base")
            usdt -= allocation
            trades[symbol].append((ts, "SIMULATED_BUY", slots[symbol].entry_price, "entry (base slot)", allocation))

    ending_usdt = usdt
    per_coin: dict[str, PortfolioCoinResult] = {}
    for symbol in symbols:
        slot = slots[symbol]
        if slot.is_in_position:
            last_close = all_candles[symbol][-1].close_price
            ending_usdt += slot.base_units * last_close
        buys = sum(1 for t in trades[symbol] if t[1] == "SIMULATED_BUY")
        sells = sum(1 for t in trades[symbol] if t[1] == "SIMULATED_SELL")
        per_coin[symbol] = PortfolioCoinResult(
            symbol=symbol, buys=buys, sells=sells, still_open=slot.is_in_position, trades=trades[symbol]
        )

    profit_loss_percent = (ending_usdt / starting_usdt - 1) * Decimal("100")
    return PortfolioBacktestResult(
        starting_usdt=starting_usdt,
        ending_usdt=ending_usdt,
        profit_loss_percent=profit_loss_percent,
        per_coin=per_coin,
    )
