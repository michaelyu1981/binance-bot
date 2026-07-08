"""Claude Trend Rider.

Deterministic dry-run 1-minute trend follower. Enters on an EMA9/EMA21 cross
confirmed by ATR-normalized slope, exits on a chandelier trail or a cross
back down, and never caps profits. Loss is bounded by an initial ATR stop.
Single position only, no averaging down. It must not execute live orders,
call exchange clients, or access Binance order endpoints.
"""

from __future__ import annotations

from decimal import Decimal

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.claude_common import ClaudeDecision, RunningEma, WilderAtr
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


FAST_EMA_PERIOD = 9
SLOW_EMA_PERIOD = 21
ATRP_MINIMUM_PERCENT = Decimal("0.05")
SLOPE_ATR_MINIMUM = Decimal("0.10")
INITIAL_STOP_ATR_MULTIPLIER = Decimal("2.5")
TRAIL_ATR_MULTIPLIER = Decimal("3")


class ClaudeTrendRider:
    definition = StrategyDefinition(
        slug="claude_trend_rider",
        name="Claude Trend Rider",
        style="1-minute dry-run EMA trend follower with chandelier trail",
        description=(
            "Enters on EMA9 crossing above EMA21 with ATR-normalized slope "
            "confirmation, trails a 3xATR chandelier from the peak close, and "
            "never caps winners. Initial stop is 2.5xATR."
        ),
    )

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._atr = WilderAtr(14)
        self._fast_ema = RunningEma(FAST_EMA_PERIOD)
        self._slow_ema = RunningEma(SLOW_EMA_PERIOD)
        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.stop_price = Decimal("0")
        self.peak_close = Decimal("0")

    def on_candle_tick(self, candle: dict[str, Decimal]) -> ClaudeDecision:
        close = candle["close"]
        atr = self._atr.update(candle["high"], candle["low"], close)
        previous_fast = self._fast_ema.value
        previous_slow = self._slow_ema.value
        fast = self._fast_ema.update(close)
        slow = self._slow_ema.update(close)

        if atr is None or atr <= 0 or fast is None or slow is None or previous_fast is None or previous_slow is None:
            return ClaudeDecision(action="WAIT", reason="Warming up indicators.")

        if self.is_in_position:
            if close > self.peak_close:
                self.peak_close = close
            trail = self.peak_close - (atr * TRAIL_ATR_MULTIPLIER)
            floor = max(self.stop_price, trail)
            if close <= floor:
                self._exit_position()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason="Chandelier trail or initial stop hit.",
                    price=close,
                )
            if fast < slow:
                self._exit_position()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason="EMA9 crossed back below EMA21; trend over.",
                    price=close,
                )
            return ClaudeDecision(action="HOLD", reason="Trend intact; trailing the peak.")

        atrp = (atr / close) * Decimal("100")
        if atrp < ATRP_MINIMUM_PERCENT:
            return ClaudeDecision(action="WAIT", reason="Volatility below trend minimum.")
        crossed_up = previous_fast <= previous_slow and fast > slow
        if not crossed_up:
            return ClaudeDecision(action="WAIT", reason="No EMA9/EMA21 cross up.")
        slope_normalized = (slow - previous_slow) / atr
        if slope_normalized < SLOPE_ATR_MINIMUM:
            return ClaudeDecision(action="WAIT", reason="Cross up without normalized slope confirmation.")

        self.is_in_position = True
        self.entry_price = close
        self.stop_price = close - (atr * INITIAL_STOP_ATR_MULTIPLIER)
        self.peak_close = close
        return ClaudeDecision(
            action="SIMULATED_BUY",
            reason="EMA cross up with ATR-normalized slope confirmation.",
            price=close,
        )

    def _exit_position(self) -> None:
        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.stop_price = Decimal("0")
        self.peak_close = Decimal("0")

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
                thesis=f"{symbol} cannot evaluate the trend rider without close and ATR data.",
                triggers=("Collect enough 1-minute candles before evaluating.",),
                invalidation=("No simulation without EMA and ATR history.",),
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
                thesis=f"{symbol} volatility is below the {ATRP_MINIMUM_PERCENT}% trend minimum.",
                triggers=(f"Wait for ATRP >= {ATRP_MINIMUM_PERCENT}% before scanning crosses.",),
                invalidation=("Trend entries in dead volatility churn fees.",),
                reasons=summary_reasons(summary, guide, guides_by_interval.get("1h")),
            )
        return self._decision(
            symbol=symbol,
            user_label=user_label,
            verdict="TREND SCANNING",
            score=summary.score,
            risk_level="Medium",
            thesis=f"{symbol} trend rider is scanning for confirmed EMA crosses on 1-minute data.",
            triggers=(
                f"Entry needs EMA{FAST_EMA_PERIOD} crossing above EMA{SLOW_EMA_PERIOD}.",
                f"Slow-EMA slope must be >= {SLOPE_ATR_MINIMUM} ATR per candle.",
                f"Exit trails a {TRAIL_ATR_MULTIPLIER}xATR chandelier; no profit cap.",
            ),
            invalidation=(
                f"Initial stop is {INITIAL_STOP_ATR_MULTIPLIER}xATR below entry.",
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
