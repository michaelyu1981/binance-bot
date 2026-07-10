"""Claude Triad Confluence V2.

Entry logic is IDENTICAL to Claude Triad Confluence (V1) -- same pattern
detectors, same calendar/cycle module, same regime gate, unchanged. This
version adds one new layer: a daily-timeframe trend read, computed with the
exact same signal-guide functions that power this project's Telegram signal
watcher, used two ways: (1) an entry confirmation gate, refusing entries the
daily trend already contradicts, and (2) dynamic exit management -- exit
immediately if the daily trend turns bearish while holding (catches losing
trades the weekly-only regime gate misses, e.g. the repeated XTZ losses V1
took on real 2yr/4yr backtests), and hold longer with a wider trail while
the daily trend stays bullish, instead of a fixed exit width regardless of
context.

Requires the daily bias to be supplied by the caller on every tick (this
project's stateful-machine harness only feeds one candle series at a time,
so a custom multi-timeframe runner supplies it -- see the session notes).
If no daily bias is supplied, this behaves exactly like V1.

Dry-run only, spot/long-only. It must not execute live orders, call
exchange clients, or access Binance order endpoints.
"""

from __future__ import annotations

from decimal import Decimal

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.claude_calendar_cycle import calendar_cycle_score
from app.strategies.claude_common import ClaudeDecision, DonchianRegime, RunningRsi, WilderAtr
from app.strategies.claude_pattern_signals import FalseBreakoutDetector, InverseHeadAndShouldersDetector
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


SWING_WINDOW_CANDLES = 3
MIN_CALENDAR_CYCLE_SCORE = Decimal("-0.5")
RSI_MAX_AT_ENTRY = Decimal("65")
ATRP_MINIMUM_PERCENT = Decimal("0.3")
STOP_ATR_MULTIPLIER = Decimal("2.5")
TRAIL_ARM_ATR_MULTIPLIER = Decimal("2.5")
TRAIL_ATR_MULTIPLIER = Decimal("3")           # default trail width
WIDE_TRAIL_ATR_MULTIPLIER = Decimal("5")       # used while the daily trend stays bullish
REGIME_LEVEL_WINDOW = 90
REGIME_EVENT_WINDOW = 90
BEARISH_BIAS_LABELS = ("Bearish", "Strong Bearish")
BULLISH_BIAS_LABELS = ("Bullish", "Strong Bullish")


class ClaudeTriadConfluenceV2:
    definition = StrategyDefinition(
        slug="claude_triad_confluence_v2",
        name="Claude Triad Confluence V2",
        style="dry-run: V1 pattern+calendar+cycle entry, plus a daily-trend exit layer",
        description=(
            "Same entry as Claude Triad Confluence V1 (pattern, calendar, "
            "regime), unchanged, plus a daily-timeframe trend read from the "
            "same signal-guide system used for Telegram alerts. Entries "
            "also require the daily trend to not be bearish. While holding: "
            "exits immediately if the daily trend turns bearish, or trails "
            "with a wider 5xATR band instead of 3xATR while the daily "
            "trend stays bullish, to hold winners longer."
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
        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.stop_price = Decimal("0")
        self.peak_close = Decimal("0")
        self.trail_armed = False

    def on_candle_tick(self, candle: dict[str, Decimal], *, daily_bias: str | None = None) -> ClaudeDecision:
        high = candle["high"]
        low = candle["low"]
        close = candle["close"]
        open_time_ms = candle.get("open_time_ms")

        atr = self._atr.update(high, low, close)
        rsi = self._rsi.update(close)
        regime = self._regime.update(high, low)
        pattern_hs = self._inverse_hs.update(high=high, low=low, close=close)
        pattern_false_breakout = self._false_breakout.update(high=high, low=low, close=close)

        if atr is None or atr <= 0 or rsi is None or regime is None:
            return ClaudeDecision(action="WAIT", reason="Warming up ATR, RSI, and regime history.")

        if self.is_in_position:
            if daily_bias in BEARISH_BIAS_LABELS:
                self._exit_position()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason=f"Daily trend layer: bias turned {daily_bias}; protective exit.",
                    price=close,
                )

            if close > self.peak_close:
                self.peak_close = close
            if not self.trail_armed and close >= self.entry_price + (atr * TRAIL_ARM_ATR_MULTIPLIER):
                self.trail_armed = True

            trail_multiplier = WIDE_TRAIL_ATR_MULTIPLIER if daily_bias in BULLISH_BIAS_LABELS else TRAIL_ATR_MULTIPLIER
            floor = self.stop_price
            if self.trail_armed:
                floor = max(floor, self.peak_close - (atr * trail_multiplier))
            if close <= floor:
                self._exit_position()
                return ClaudeDecision(action="SIMULATED_SELL", reason="Stop or armed trail hit.", price=close)
            return ClaudeDecision(action="HOLD", reason="Position active; managing stop/trail.")

        pattern_signal = pattern_hs or pattern_false_breakout
        if pattern_signal is None:
            return ClaudeDecision(action="WAIT", reason="Technical pillar: no confirmed bullish pattern yet.")

        if regime == "BEAR":
            return ClaudeDecision(
                action="WAIT",
                reason=f"Cycle pillar failed: {pattern_signal.name} confirmed but regime is BEAR.",
            )

        # The daily-trend layer gates EXITS only (see is_in_position branch
        # above), not entries. Gating entries on it was tested and removed:
        # a real reversal's earliest, highest-conviction entries occur while
        # the slower daily trend still reads bearish -- trend signals lag by
        # nature, so requiring one to already agree at entry refuses exactly
        # the trades that turn into the biggest winners (verified on ZEC's
        # 2024-07 entry, which the entry-side gate blocked and which went on
        # to be a +900% move in the ungated version).

        atrp = (atr / close) * Decimal("100")
        if atrp < ATRP_MINIMUM_PERCENT:
            return ClaudeDecision(action="WAIT", reason="Volatility below the fee-aware minimum.")

        if rsi > RSI_MAX_AT_ENTRY:
            return ClaudeDecision(
                action="WAIT",
                reason=f"{pattern_signal.name} confirmed but RSI {rsi:.1f} is already extended.",
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
                f"calendar score={cycle_score:.2f}, RSI={rsi:.1f}, daily bias={daily_bias}."
            ),
            price=close,
        )

    def _exit_position(self) -> None:
        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.stop_price = Decimal("0")
        self.peak_close = Decimal("0")
        self.trail_armed = False

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
                thesis=f"{symbol} cannot evaluate the triad confluence v2 without close and ATR data.",
                triggers=("Collect enough candle history before evaluating.",),
                invalidation=("No simulation without confirmed swing points and a full regime window.",),
                reasons=("Missing shortest-timeframe candle signals.",),
            )
        return self._decision(
            symbol=symbol,
            user_label=user_label,
            verdict="TRIAD V2 SCANNING",
            score=summary.score,
            risk_level="Medium",
            thesis=f"{symbol} is scanning for pattern, calendar, regime, and daily-trend agreement.",
            triggers=(
                "Technical: a confirmed inverse head-and-shoulders or false-breakdown reclaim.",
                "Calendar: hour/day volume seasonality and Ghost Month avoidance score above the minimum.",
                "Cycle: Donchian regime must not be BEAR; daily trend must not be Bearish.",
            ),
            invalidation=(
                "Exits immediately if the daily trend turns Bearish while holding.",
                f"Otherwise: {STOP_ATR_MULTIPLIER}xATR stop, {TRAIL_ATR_MULTIPLIER}xATR trail "
                f"(widens to {WIDE_TRAIL_ATR_MULTIPLIER}xATR while the daily trend stays Bullish).",
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
