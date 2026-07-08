"""Claude Breakout Hunter V2.

Deterministic dry-run breakout strategy, improved from V1 using 1-year
backtest evidence. V2 keeps V1's loss avoidance and adds: a stop below the
breakout level instead of below entry (survives retests), a volatility
compression gate, a tested-level age requirement, a trend filter, a
post-loss cooldown with failed-level memory, a fast-failure exit, and a
wider trail armed later so big trends run. Single position only, no
averaging down. It must not execute live orders, call exchange clients, or
access Binance order endpoints.
"""

from __future__ import annotations

from decimal import Decimal

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.claude_common import ClaudeDecision, RollingMax, RollingStats, RunningEma, WilderAtr
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


BREAKOUT_WINDOW_CANDLES = 240
BREAKOUT_CLEARANCE = Decimal("1.0015")
MIN_LEVEL_AGE_CANDLES = 12
VOLUME_WINDOW_CANDLES = 60
VOLUME_SURGE_MULTIPLIER = Decimal("1.5")
MAX_EXTENSION_ATR_MULTIPLIER = Decimal("2")
ATRP_MINIMUM_PERCENT = Decimal("0.05")
TREND_EMA_PERIOD = 200
COMPRESSION_ATR_WINDOW = 240
COMPRESSION_ATR_MAX_RATIO = Decimal("1.0")
STOP_BELOW_LEVEL_ATR_MULTIPLIER = Decimal("0.5")
FAST_FAIL_CANDLES = 8
TRAIL_ARM_ATR_MULTIPLIER = Decimal("3")
TRAIL_ATR_MULTIPLIER = Decimal("3")
LOSS_COOLDOWN_CANDLES = 24
FAILED_LEVEL_ATR_TOLERANCE = Decimal("0.5")


class ClaudeBreakoutHunterV2:
    definition = StrategyDefinition(
        slug="claude_breakout_hunter_v2",
        name="Claude Breakout Hunter V2",
        style="dry-run tested-level breakout with retest-tolerant stop and trend filter",
        description=(
            "Buys a volume-confirmed break of an aged rolling high only in "
            "an uptrend after volatility compression. Stops below the level "
            "to survive retests, exits fast if the break stalls, cools down "
            "after losses, and trails 3xATR armed at 3xATR so trends run."
        ),
    )

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._atr = WilderAtr(14)
        self._highs = RollingMax(BREAKOUT_WINDOW_CANDLES)
        self._volume_stats = RollingStats(VOLUME_WINDOW_CANDLES)
        self._atr_stats = RollingStats(COMPRESSION_ATR_WINDOW)
        self._trend_ema = RunningEma(TREND_EMA_PERIOD)
        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.level_at_entry = Decimal("0")
        self.stop_price = Decimal("0")
        self.peak_close = Decimal("0")
        self.trail_armed = False
        self.candles_held = 0
        self.cooldown_remaining = 0
        self.failed_level: Decimal | None = None

    def on_candle_tick(self, candle: dict[str, Decimal]) -> ClaudeDecision:
        close = candle["close"]
        high = candle["high"]
        volume = candle["volume"]
        atr = self._atr.update(high, candle["low"], close)
        range_high = self._highs.maximum
        range_full = self._highs.is_full
        level_age = self._highs.maximum_age
        volume_mean = self._volume_stats.mean()
        volume_full = self._volume_stats.is_full
        atr_mean = self._atr_stats.mean()
        trend_ema = self._trend_ema.update(close)
        self._highs.push(high)
        self._volume_stats.push(volume)
        if atr is not None:
            self._atr_stats.push(atr)

        if (
            atr is None
            or atr <= 0
            or not range_full
            or range_high is None
            or level_age is None
            or not volume_full
            or volume_mean is None
            or atr_mean is None
            or atr_mean <= 0
            or trend_ema is None
        ):
            return ClaudeDecision(action="WAIT", reason="Warming up indicators.")

        if self.is_in_position:
            self.candles_held += 1
            if close > self.peak_close:
                self.peak_close = close
            if not self.trail_armed and close >= self.entry_price + (atr * TRAIL_ARM_ATR_MULTIPLIER):
                self.trail_armed = True
            floor = self.stop_price
            if self.trail_armed:
                floor = max(floor, self.peak_close - (atr * TRAIL_ATR_MULTIPLIER))
            if close <= floor:
                return self._exit_position(close, "Stop below level or armed trail hit.")
            if (
                not self.trail_armed
                and self.candles_held >= FAST_FAIL_CANDLES
                and close < self.entry_price
            ):
                return self._exit_position(close, "Fast failure; breakout stalled below entry.")
            return ClaudeDecision(action="HOLD", reason="Breakout position active; managing stop.")

        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            return ClaudeDecision(action="WAIT", reason="Cooling down after a losing breakout.")

        atrp = (atr / close) * Decimal("100")
        if atrp < ATRP_MINIMUM_PERCENT:
            return ClaudeDecision(action="WAIT", reason="Volatility below breakout minimum.")
        if close < trend_ema:
            return ClaudeDecision(action="WAIT", reason="Below trend EMA; longs only in uptrends.")
        if atr > atr_mean * COMPRESSION_ATR_MAX_RATIO:
            return ClaudeDecision(action="WAIT", reason="No volatility compression before the break.")
        if close < range_high * BREAKOUT_CLEARANCE:
            return ClaudeDecision(action="WAIT", reason="No clean break of the rolling high.")
        if level_age < MIN_LEVEL_AGE_CANDLES:
            return ClaudeDecision(action="WAIT", reason="Level too fresh; not a tested ceiling.")
        if close - range_high > atr * MAX_EXTENSION_ATR_MULTIPLIER:
            return ClaudeDecision(action="WAIT", reason="Too many ATRs above the level; chase rejected.")
        if volume < volume_mean * VOLUME_SURGE_MULTIPLIER:
            return ClaudeDecision(action="WAIT", reason="Breakout without a volume surge.")
        if (
            self.failed_level is not None
            and abs(range_high - self.failed_level) <= atr * FAILED_LEVEL_ATR_TOLERANCE
        ):
            return ClaudeDecision(action="WAIT", reason="Level already failed once; waiting for a new level.")

        self.is_in_position = True
        self.entry_price = close
        self.level_at_entry = range_high
        self.stop_price = range_high - (atr * STOP_BELOW_LEVEL_ATR_MULTIPLIER)
        self.peak_close = close
        self.trail_armed = False
        self.candles_held = 0
        return ClaudeDecision(
            action="SIMULATED_BUY",
            reason="Aged-level breakout in uptrend after compression, volume confirmed.",
            price=close,
        )

    def _exit_position(self, close: Decimal, reason: str) -> ClaudeDecision:
        if close < self.entry_price:
            self.cooldown_remaining = LOSS_COOLDOWN_CANDLES
            self.failed_level = self.level_at_entry
        else:
            self.failed_level = None
        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.level_at_entry = Decimal("0")
        self.stop_price = Decimal("0")
        self.peak_close = Decimal("0")
        self.trail_armed = False
        self.candles_held = 0
        return ClaudeDecision(action="SIMULATED_SELL", reason=reason, price=close)

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
                thesis=f"{symbol} cannot evaluate breakout hunter V2 without close and ATR data.",
                triggers=("Collect enough candle history before evaluating.",),
                invalidation=("No simulation without a full rolling range and trend EMA.",),
                reasons=("Missing shortest-timeframe candle signals.",),
            )
        atrp = (guide.atr14 / guide.current_price) * Decimal("100")
        if atrp < ATRP_MINIMUM_PERCENT:
            return self._decision(
                symbol=symbol,
                user_label=user_label,
                verdict="BLOCKED_BY_VOLATILITY",
                score=max(0, summary.score - 15),
                risk_level="Medium",
                thesis=f"{symbol} volatility is below the {ATRP_MINIMUM_PERCENT}% breakout minimum.",
                triggers=(f"Wait for ATRP >= {ATRP_MINIMUM_PERCENT}% before scanning breakouts.",),
                invalidation=("Breakouts in dead volatility are usually fee-negative noise.",),
                reasons=summary_reasons(summary, guide, guides_by_interval.get("1h")),
            )
        return self._decision(
            symbol=symbol,
            user_label=user_label,
            verdict="V2 BREAKOUT SCANNING",
            score=summary.score,
            risk_level="Medium",
            thesis=f"{symbol} V2 is scanning for aged-level breakouts in an uptrend after compression.",
            triggers=(
                f"Entry needs a close >= {BREAKOUT_CLEARANCE}x the rolling {BREAKOUT_WINDOW_CANDLES}-candle high.",
                f"The level must be >= {MIN_LEVEL_AGE_CANDLES} candles old with price above EMA{TREND_EMA_PERIOD}.",
                f"ATR must be at or below its {COMPRESSION_ATR_WINDOW}-candle average (compression).",
                f"Volume must be >= {VOLUME_SURGE_MULTIPLIER}x its {VOLUME_WINDOW_CANDLES}-candle average.",
            ),
            invalidation=(
                f"Stop sits {STOP_BELOW_LEVEL_ATR_MULTIPLIER}xATR below the broken level, not below entry.",
                f"Fast exit if still below entry after {FAST_FAIL_CANDLES} candles.",
                f"After a loss: {LOSS_COOLDOWN_CANDLES}-candle cooldown and the failed level is skipped.",
                "No live orders are allowed.",
            ),
            reasons=summary_reasons(summary, guide, guides_by_interval.get("1h")),
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
