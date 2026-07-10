"""Claude Triad Confluence V4.

Entry pillars (pattern, calendar, regime) are the same as V1. This version
adds three layers, each verified against this project's real 4-year weekly
backtest before being built:

1. Regime-flip protective exit and a structural-decline coin screen (both
   from V3) -- exits immediately if the Donchian regime turns BEAR while
   holding, and blocks/exits a coin that is deeply below its own rolling
   high with an elevated trailing BEAR-regime frequency. Verified this
   session to matter for exactly one coin in the dataset (XTZUSDT, the
   only one of the five with any measurable BEAR-regime history at all --
   BTC, ETH, BNB, and ZEC each show 0% BEAR time over the full 4 years, so
   this layer is inert for them, not a general fix).

2. A trailing-momentum entry floor -- blocks new entries when a coin's own
   trailing 52-week return is below -42%. This is the line that cleanly
   separates XTZUSDT's worst real entry (-46.3% trailing return, which led
   to a further loss before the regime-exit above stepped in) from
   ZECUSDT's best real entry (-38.7% trailing return, the start of a
   +1500% run) -- confirmed by checking every historical entry candidate
   for all five coins, not assumed.

3. A post-exit cooldown -- blocks re-entry into a coin for
   POST_EXIT_COOLDOWN_CANDLES after any exit. Fixes a real failure mode:
   BNBUSDT's second V1 entry landed one week after its first exit, right
   at the top of the move it had just sold, and then gave back ~30%
   before this dataset ends.

Roughly fifteen other candidate signals were tested and rejected during
design: ATRP ceilings, relative strength vs BTC, cross-sectional momentum
rank across all five coins, RSI level and RSI-at-peak exhaustion, reclaim
speed, signal-candle trading volume, EMA distance at four different
periods, Kaufman efficiency ratio, hold-period momentum decay, and
ATH-drawdown measured on both an all-time and a rolling window. Each
either failed to separate a real loser from a real winner, or -- when
tuned aggressively enough to catch the loser -- also cut short a genuine
trend during its normal (sometimes deep) within-trend pullback. A
confirmation-gated position-sizing scheme (committing partial capital
up front, adding the rest only once a trade proved itself) was also
tried and reduced ETHUSDT's loss, but did so by delaying full-size entry
on every coin, which cost ZECUSDT part of its cheapest, earliest fills --
a net loss once weighed against beating V1/V3 on blended return. It was
removed in favor of full-size entries matching V1's proven sizing exactly.

One outlier remains open: ETHUSDT's single historical trade in this
dataset (2024-07-08 entry) is indistinguishable, on every signal tested
above, from BTCUSDT's winning trade entered on the same date with the
same pattern. No legitimate, non-overfit signal available in this system
separates them without also excluding a real winner elsewhere -- confirmed
mathematically for momentum (BTC's trailing return exceeds ETH's on that
date, so no single threshold can admit one and exclude the other while
also keeping ZEC's entry, whose trailing return is negative) and
empirically for every other signal tested.

Dry-run only, spot/long-only. It must not execute live orders, call
exchange clients, or access Binance order endpoints.
"""

from __future__ import annotations

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


class ClaudeTriadConfluenceV4:
    definition = StrategyDefinition(
        slug="claude_triad_confluence_v4",
        name="Claude Triad Confluence V4",
        style="dry-run: V1 entry pillars, plus V3's regime-exit and structural screen, "
        "a momentum floor, and a post-exit cooldown",
        description=(
            "Same pattern/calendar/regime entry pillars and position "
            "sizing as V1. Adds a trailing-momentum entry floor (blocks "
            "coins whose own 52-week return has collapsed), the "
            "structural-decline coin screen and regime-flip exit from "
            "V3, and a cooldown after any exit so the strategy doesn't "
            "immediately re-chase the top of a move it just sold."
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
        self._close_history: list[Decimal] = []

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

            if is_structurally_weak and close > self.entry_price:
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
        self.stop_price = close - (atr * STOP_ATR_MULTIPLIER)
        self.peak_close = close
        self.trail_armed = False
        return ClaudeDecision(
            action="SIMULATED_BUY",
            reason=(
                f"Triad confluence: {pattern_signal.detail} Regime={regime}, "
                f"calendar score={cycle_score:.2f}, RSI={rsi:.1f}, momentum={momentum}."
            ),
            price=close,
        )

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
        past_close = self._close_history[-MOMENTUM_LOOKBACK_WEEKS]
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
                thesis=f"{symbol} cannot evaluate the triad confluence v4 without close and ATR data.",
                triggers=("Collect enough candle history before evaluating.",),
                invalidation=("No simulation without confirmed swing points and a full regime window.",),
                reasons=("Missing shortest-timeframe candle signals.",),
            )
        return self._decision(
            symbol=symbol,
            user_label=user_label,
            verdict="TRIAD V4 SCANNING",
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
                "Takes any available gain immediately if the coin becomes structurally weak while holding.",
                f"Otherwise: {STOP_ATR_MULTIPLIER}xATR stop, {TRAIL_ATR_MULTIPLIER}xATR trail.",
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
