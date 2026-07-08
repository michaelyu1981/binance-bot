"""Claude Breakout Hunter.

Deterministic dry-run 1-minute breakout strategy. Buys a clean break of the
rolling 4-hour high confirmed by a volume surge, rejects chases that are too
many ATRs above the breakout level, and manages the position with an ATR
stop that ratchets into a trail once the trade is in profit. Single position
only, no averaging down. It must not execute live orders, call exchange
clients, or access Binance order endpoints.
"""

from __future__ import annotations

from decimal import Decimal

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.claude_common import ClaudeDecision, RollingMax, RollingStats, WilderAtr
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


BREAKOUT_WINDOW_CANDLES = 240
BREAKOUT_CLEARANCE = Decimal("1.0015")
VOLUME_WINDOW_CANDLES = 60
VOLUME_SURGE_MULTIPLIER = Decimal("1.5")
MAX_EXTENSION_ATR_MULTIPLIER = Decimal("4")
ATRP_MINIMUM_PERCENT = Decimal("0.05")
STOP_ATR_MULTIPLIER = Decimal("2")
TRAIL_ARM_ATR_MULTIPLIER = Decimal("2")
TRAIL_ATR_MULTIPLIER = Decimal("2")


class ClaudeBreakoutHunter:
    definition = StrategyDefinition(
        slug="claude_breakout_hunter",
        name="Claude Breakout Hunter",
        style="1-minute dry-run range-high breakout with volume confirmation",
        description=(
            "Buys a 0.15% clearance break of the rolling 4-hour high on a "
            "1.5x volume surge, rejects chases more than 4xATR above the "
            "level, stops at 2xATR, and trails 2xATR once 2xATR in profit."
        ),
    )

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._atr = WilderAtr(14)
        self._highs = RollingMax(BREAKOUT_WINDOW_CANDLES)
        self._volume_stats = RollingStats(VOLUME_WINDOW_CANDLES)
        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.stop_price = Decimal("0")
        self.peak_close = Decimal("0")
        self.trail_armed = False

    def on_candle_tick(self, candle: dict[str, Decimal]) -> ClaudeDecision:
        close = candle["close"]
        high = candle["high"]
        volume = candle["volume"]
        atr = self._atr.update(high, candle["low"], close)
        range_high = self._highs.maximum
        range_full = self._highs.is_full
        volume_mean = self._volume_stats.mean()
        volume_full = self._volume_stats.is_full
        self._highs.push(high)
        self._volume_stats.push(volume)

        if (
            atr is None
            or atr <= 0
            or not range_full
            or range_high is None
            or not volume_full
            or volume_mean is None
        ):
            return ClaudeDecision(action="WAIT", reason="Warming up indicators.")

        if self.is_in_position:
            if close > self.peak_close:
                self.peak_close = close
            if not self.trail_armed and close >= self.entry_price + (atr * TRAIL_ARM_ATR_MULTIPLIER):
                self.trail_armed = True
            floor = self.stop_price
            if self.trail_armed:
                floor = max(floor, self.peak_close - (atr * TRAIL_ATR_MULTIPLIER))
            if close <= floor:
                self._exit_position()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason="Breakout stop or armed trail hit.",
                    price=close,
                )
            return ClaudeDecision(action="HOLD", reason="Breakout position active; managing stop.")

        atrp = (atr / close) * Decimal("100")
        if atrp < ATRP_MINIMUM_PERCENT:
            return ClaudeDecision(action="WAIT", reason="Volatility below breakout minimum.")
        if close < range_high * BREAKOUT_CLEARANCE:
            return ClaudeDecision(action="WAIT", reason="No clean break of the rolling 4-hour high.")
        if volume < volume_mean * VOLUME_SURGE_MULTIPLIER:
            return ClaudeDecision(action="WAIT", reason="Breakout without a volume surge.")
        if close - range_high > atr * MAX_EXTENSION_ATR_MULTIPLIER:
            return ClaudeDecision(action="WAIT", reason="Too many ATRs above the level; chase rejected.")

        self.is_in_position = True
        self.entry_price = close
        self.stop_price = close - (atr * STOP_ATR_MULTIPLIER)
        self.peak_close = close
        self.trail_armed = False
        return ClaudeDecision(
            action="SIMULATED_BUY",
            reason="Range-high breakout with volume surge confirmation.",
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
                thesis=f"{symbol} cannot evaluate the breakout hunter without close and ATR data.",
                triggers=("Collect enough 1-minute candles before evaluating.",),
                invalidation=("No simulation without a full 4-hour rolling range.",),
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
            verdict="BREAKOUT SCANNING",
            score=summary.score,
            risk_level="High",
            thesis=f"{symbol} breakout hunter is scanning the rolling 4-hour high on 1-minute data.",
            triggers=(
                f"Entry needs a close >= {BREAKOUT_CLEARANCE}x the rolling {BREAKOUT_WINDOW_CANDLES}-candle high.",
                f"Volume must be >= {VOLUME_SURGE_MULTIPLIER}x its {VOLUME_WINDOW_CANDLES}-candle average.",
                f"Close must be within {MAX_EXTENSION_ATR_MULTIPLIER}xATR of the level to avoid chasing.",
            ),
            invalidation=(
                f"Stop is {STOP_ATR_MULTIPLIER}xATR below entry; trail arms after {TRAIL_ARM_ATR_MULTIPLIER}xATR of profit.",
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
