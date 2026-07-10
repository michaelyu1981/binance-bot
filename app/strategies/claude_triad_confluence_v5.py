"""Claude Triad Confluence V5.

Identical trading logic to V4 (pattern/calendar/regime entry pillars,
trailing-momentum entry floor, structural-decline coin screen, regime-flip
protective exit, post-exit cooldown, 2.5xATR stop with a 3xATR trail that
arms at 2.5xATR of profit), plus fixes for four defects found in a code
review of V4 -- each verified against the real 4-year weekly dataset
before shipping:

1. Stop-price sanity cap. V4 sets the stop at close - 2.5xATR with no
   floor. ZECUSDT's ATR reached 50.95% of price in this dataset, at which
   point that formula yields a NEGATIVE stop -- a stop that can never
   fire, i.e. an unprotected position (the exact no-stop-loss failure
   mode that sank this project's legacy strategies). V5 caps the stop at
   MAX_STOP_DISTANCE_FRACTION (50%) below entry. Verified non-binding on
   every historical trade in this dataset (all backtest results are
   byte-identical to V4); it exists to protect future entries during
   volatility spikes.

2. Fee-aware profit check on the structural-weakness exit. V4's "take
   the available gain" exit required only close > entry_price; a close
   0.1% above entry actually realizes a small LOSS after the 0.2%
   round-trip fee. V5 requires close > entry * (1 + FEE_AWARE_GAIN_BUFFER).

3. Bounded momentum history. V4 appended every close to an unbounded
   list; only the trailing MOMENTUM_LOOKBACK_WEEKS closes are ever read.
   V5 uses a fixed-size deque, restoring the O(1)-per-tick memory
   contract that lets these strategies run on minute-scale data.

4. Readable trade reasons. V4 interpolated raw Decimals into the BUY
   reason (27 digits of momentum); V5 formats them.

The V4 module docstring's full record of what was tested and rejected
(seventeen entry/exit signals, confirmation-gated sizing) still applies;
none of that analysis changed here.

Dry-run only, spot/long-only. It must not execute live orders, call
exchange clients, or access Binance order endpoints.
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.claude_calendar_cycle import calendar_cycle_score
from app.strategies.claude_common import (
    ClaudeDecision,
    DonchianRegime,
    RollingFraction,
    RollingMax,
    RunningRsi,
    WilderAtr,
)
from app.strategies.claude_pattern_signals import FalseBreakoutDetector, InverseHeadAndShouldersDetector
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


SWING_WINDOW_CANDLES = 3
MIN_CALENDAR_CYCLE_SCORE = Decimal("-0.5")
RSI_MAX_AT_ENTRY = Decimal("65")
ATRP_MINIMUM_PERCENT = Decimal("0.3")
STOP_ATR_MULTIPLIER = Decimal("2.5")
TRAIL_ARM_ATR_MULTIPLIER = Decimal("2.5")
TRAIL_ATR_MULTIPLIER = Decimal("3")
REGIME_LEVEL_WINDOW = 90
REGIME_EVENT_WINDOW = 90

ALL_TIME_HIGH_WINDOW = 10_000
ATH_DRAWDOWN_BLOCK_THRESHOLD = Decimal("-0.5")
BEAR_FRACTION_LOOKBACK_CANDLES = 104
BEAR_FRACTION_BLOCK_THRESHOLD = Decimal("0.15")

MOMENTUM_LOOKBACK_WEEKS = 52
MOMENTUM_FLOOR_PERCENT = Decimal("-42")

POST_EXIT_COOLDOWN_CANDLES = 8

# The stop may never sit more than this fraction below entry, no matter how
# large ATR is -- prevents the negative/never-firing stop V4 allowed.
MAX_STOP_DISTANCE_FRACTION = Decimal("0.50")
# Round-trip fees are 0.2%; require at least this much gross gain before the
# structural-weakness exit may call a sale "taking the gain".
FEE_AWARE_GAIN_BUFFER = Decimal("0.003")


class ClaudeTriadConfluenceV5:
    definition = StrategyDefinition(
        slug="claude_triad_confluence_v5",
        name="Claude Triad Confluence V5",
        style="dry-run: V4 logic with a stop-price sanity cap, fee-aware exits, and O(1) memory",
        description=(
            "Same trading rules as V4: pattern/calendar/regime entry "
            "pillars, a trailing-momentum entry floor, the structural-"
            "decline coin screen, a regime-flip protective exit, and a "
            "post-exit cooldown. Fixes four V4 defects: a stop that "
            "could compute below zero during extreme volatility (an "
            "unprotected position), a take-the-gain exit that ignored "
            "trading fees, unbounded memory growth in the momentum "
            "window, and unreadable trade reasons."
        ),
    )

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._atr = WilderAtr(14)
        self._rsi = RunningRsi(14)
        self._regime = DonchianRegime(REGIME_LEVEL_WINDOW, REGIME_EVENT_WINDOW)
        self._inverse_hs = InverseHeadAndShouldersDetector(SWING_WINDOW_CANDLES)
        self._false_breakout = FalseBreakoutDetector(SWING_WINDOW_CANDLES)
        self._all_time_high = RollingMax(ALL_TIME_HIGH_WINDOW)
        self._bear_fraction = RollingFraction(BEAR_FRACTION_LOOKBACK_CANDLES)
        self._close_history: deque[Decimal] = deque(maxlen=MOMENTUM_LOOKBACK_WEEKS)

        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.stop_price = Decimal("0")
        self.peak_close = Decimal("0")
        self.trail_armed = False
        self.candles_since_exit = 10 ** 9

    def on_candle_tick(self, candle: dict[str, Decimal]) -> ClaudeDecision:
        high = candle["high"]
        low = candle["low"]
        close = candle["close"]
        open_time_ms = candle.get("open_time_ms")

        atr = self._atr.update(high, low, close)
        rsi = self._rsi.update(close)
        regime = self._regime.update(high, low)
        pattern_hs = self._inverse_hs.update(high=high, low=low, close=close)
        pattern_false_breakout = self._false_breakout.update(high=high, low=low, close=close)
        self._all_time_high.push(high)
        self._bear_fraction.push(regime == "BEAR")
        self.candles_since_exit += 1
        momentum = self._trailing_momentum(close)
        self._close_history.append(close)

        if atr is None or atr <= 0 or rsi is None or regime is None:
            return ClaudeDecision(action="WAIT", reason="Warming up ATR, RSI, and regime history.")

        is_structurally_weak = self._is_structurally_weak(close)

        if self.is_in_position:
            if regime == "BEAR":
                self._exit_position()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason="Regime protection: regime turned BEAR while holding; protective exit.",
                    price=close,
                )

            if is_structurally_weak and close > self.entry_price * (Decimal("1") + FEE_AWARE_GAIN_BUFFER):
                self._exit_position()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason="Structural-weakness layer: coin profile is weak; taking the available gain.",
                    price=close,
                )

            if close > self.peak_close:
                self.peak_close = close
            if not self.trail_armed and close >= self.entry_price + (atr * TRAIL_ARM_ATR_MULTIPLIER):
                self.trail_armed = True
            floor = self.stop_price
            if self.trail_armed:
                floor = max(floor, self.peak_close - (atr * TRAIL_ATR_MULTIPLIER))
            if close <= floor:
                self._exit_position()
                return ClaudeDecision(action="SIMULATED_SELL", reason="Stop or armed trail hit.", price=close)
            return ClaudeDecision(action="HOLD", reason="Position active; managing stop/trail.")

        if self.candles_since_exit < POST_EXIT_COOLDOWN_CANDLES:
            return ClaudeDecision(action="WAIT", reason="Cooldown after the last exit.")

        if is_structurally_weak:
            return ClaudeDecision(
                action="WAIT",
                reason="Structural-weakness layer: coin is deeply below its own high with elevated BEAR-regime history.",
            )

        pattern_signal = pattern_hs or pattern_false_breakout
        if pattern_signal is None:
            return ClaudeDecision(action="WAIT", reason="Technical pillar: no confirmed bullish pattern yet.")

        if regime == "BEAR":
            return ClaudeDecision(
                action="WAIT",
                reason=f"Cycle pillar failed: {pattern_signal.name} confirmed but regime is BEAR.",
            )

        atrp = (atr / close) * Decimal("100")
        if atrp < ATRP_MINIMUM_PERCENT:
            return ClaudeDecision(action="WAIT", reason="Volatility below the fee-aware minimum.")

        if rsi > RSI_MAX_AT_ENTRY:
            return ClaudeDecision(
                action="WAIT",
                reason=f"{pattern_signal.name} confirmed but RSI {rsi:.1f} is already extended.",
            )

        if momentum is not None and momentum < MOMENTUM_FLOOR_PERCENT:
            return ClaudeDecision(
                action="WAIT",
                reason=f"Momentum pillar failed: trailing {MOMENTUM_LOOKBACK_WEEKS}-week return {momentum:.1f}% "
                f"is below the {MOMENTUM_FLOOR_PERCENT}% floor.",
            )

        cycle_score = calendar_cycle_score(open_time_ms) if open_time_ms is not None else Decimal("0")
        if cycle_score < MIN_CALENDAR_CYCLE_SCORE:
            return ClaudeDecision(
                action="WAIT",
                reason=(
                    f"Calendar pillar failed: {pattern_signal.name} confirmed but timing score "
                    f"{cycle_score:.2f} is below {MIN_CALENDAR_CYCLE_SCORE}."
                ),
            )

        self.is_in_position = True
        self.entry_price = close
        self.stop_price = self._initial_stop_price(close, atr)
        self.peak_close = close
        self.trail_armed = False
        momentum_text = f"{momentum:.1f}%" if momentum is not None else "n/a"
        return ClaudeDecision(
            action="SIMULATED_BUY",
            reason=(
                f"Triad confluence: {pattern_signal.detail} Regime={regime}, "
                f"calendar score={cycle_score:.2f}, RSI={rsi:.1f}, momentum={momentum_text}."
            ),
            price=close,
        )

    def _initial_stop_price(self, close: Decimal, atr: Decimal) -> Decimal:
        atr_stop = close - (atr * STOP_ATR_MULTIPLIER)
        widest_allowed = close * (Decimal("1") - MAX_STOP_DISTANCE_FRACTION)
        return max(atr_stop, widest_allowed)

    def _is_structurally_weak(self, close: Decimal) -> bool:
        ath = self._all_time_high.maximum
        bear_fraction = self._bear_fraction.fraction
        if ath is None or ath <= 0 or bear_fraction is None:
            return False
        drawdown_from_ath = (close / ath) - Decimal("1")
        return drawdown_from_ath < ATH_DRAWDOWN_BLOCK_THRESHOLD and bear_fraction > BEAR_FRACTION_BLOCK_THRESHOLD

    def _trailing_momentum(self, close: Decimal) -> Decimal | None:
        if len(self._close_history) < MOMENTUM_LOOKBACK_WEEKS:
            return None
        past_close = self._close_history[0]
        if past_close <= 0:
            return None
        return (close / past_close - Decimal("1")) * Decimal("100")

    def _exit_position(self) -> None:
        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.stop_price = Decimal("0")
        self.peak_close = Decimal("0")
        self.trail_armed = False
        self.candles_since_exit = 0

    def evaluate(
        self,
        *,
        symbol: str,
        summary: MultiTimeframeSignalSummary,
        guides_by_interval: dict[str, TechnicalSignalGuide],
        user_label: str,
    ) -> StrategyDecision:
        guide = shortest_timeframe_guide(guides_by_interval)
        if guide is None or guide.current_price is None or guide.atr14 is None or guide.atr14 <= 0:
            return self._decision(
                symbol=symbol,
                user_label=user_label,
                verdict="BLOCKED_BY_RISK",
                score=0,
                risk_level="High",
                thesis=f"{symbol} cannot evaluate the triad confluence v5 without close and ATR data.",
                triggers=("Collect enough candle history before evaluating.",),
                invalidation=("No simulation without confirmed swing points and a full regime window.",),
                reasons=("Missing shortest-timeframe candle signals.",),
            )
        return self._decision(
            symbol=symbol,
            user_label=user_label,
            verdict="TRIAD V5 SCANNING",
            score=summary.score,
            risk_level="Medium",
            thesis=f"{symbol} is scanning for pattern, calendar, regime, and momentum agreement.",
            triggers=(
                "Technical: a confirmed inverse head-and-shoulders or false-breakdown reclaim.",
                "Momentum: trailing 52-week return must not be collapsed.",
                "Calendar: hour/day volume seasonality and Ghost Month avoidance score above the minimum.",
                "Cycle: Donchian regime must not be BEAR, and the coin must not be structurally weak.",
            ),
            invalidation=(
                "Exits immediately if the regime turns BEAR while holding.",
                "Takes any fee-covered gain immediately if the coin becomes structurally weak while holding.",
                f"Otherwise: {STOP_ATR_MULTIPLIER}xATR stop (never wider than "
                f"{MAX_STOP_DISTANCE_FRACTION * 100}% below entry), {TRAIL_ATR_MULTIPLIER}xATR trail.",
                "No live orders are allowed.",
            ),
            reasons=summary_reasons(summary, guide, guides_by_interval.get("1d")),
        )

    def _decision(
        self,
        *,
        symbol: str,
        user_label: str,
        verdict: str,
        score: int,
        risk_level: str,
        thesis: str,
        triggers: tuple[str, ...],
        invalidation: tuple[str, ...],
        reasons: tuple[str, ...],
    ) -> StrategyDecision:
        return StrategyDecision(
            strategy=self.definition,
            user_label=user_label,
            symbol=symbol,
            verdict=verdict,
            score=score,
            risk_level=risk_level,
            mode="Dry-run advisory only",
            thesis=thesis,
            triggers=triggers,
            invalidation=invalidation,
            reasons=reasons,
        )
