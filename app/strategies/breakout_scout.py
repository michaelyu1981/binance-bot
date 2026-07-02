"""CoinPilot Breakout Scout v1."""

from __future__ import annotations

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.helpers import breakout_trigger, is_bullish, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


class CoinPilotBreakoutScout:
    definition = StrategyDefinition(
        slug="coinpilot_breakout_scout_v1",
        name="CoinPilot Breakout Scout v1",
        style="Volatility squeeze and resistance breakout watch",
        description="Looks for compressed volatility and a clear breakout level before acting.",
    )

    def evaluate(
        self,
        *,
        symbol: str,
        summary: MultiTimeframeSignalSummary,
        guides_by_interval: dict[str, TechnicalSignalGuide],
        user_label: str,
    ) -> StrategyDecision:
        guide_4h = guides_by_interval.get("4h")
        guide_1h = guides_by_interval.get("1h")
        squeeze = any(
            guide is not None and guide.bollinger_squeeze in ("Yes", "Possible")
            for guide in (guide_4h, guide_1h)
        )
        bullish_pressure = is_bullish(guide_4h) or is_bullish(guide_1h)
        if squeeze and bullish_pressure:
            verdict = "BREAKOUT WATCH"
            score = min(100, summary.score + 8)
            thesis = f"{symbol} has compression with bullish pressure; wait for a confirmed break, not a guess."
            triggers = (
                breakout_trigger(guide_4h),
                "Confirm with RSI above 55 and MACD histogram improving.",
            )
            invalidation = ("Cancel the setup if price rejects resistance and loses the Bollinger middle band.",)
            risk_level = "Medium"
        elif squeeze:
            verdict = "WAIT"
            score = summary.score
            thesis = f"{symbol} may be compressing, but direction is not confirmed."
            triggers = ("Wait for a clean close outside the range with momentum confirmation.",)
            invalidation = ("Do not trade the squeeze before direction is confirmed.",)
            risk_level = "Medium"
        else:
            verdict = "NO BREAKOUT SETUP"
            score = max(0, summary.score - 8)
            thesis = f"{symbol} is not showing a clean volatility squeeze breakout setup."
            triggers = ("Wait for Bollinger squeeze or a clean resistance break.",)
            invalidation = ("Ignore breakout logic while volatility and structure are ordinary.",)
            risk_level = "Low"
        return StrategyDecision(
            strategy=self.definition,
            user_label=user_label,
            symbol=symbol,
            verdict=verdict,
            score=score,
            risk_level=risk_level,
            mode="Advisory only",
            thesis=thesis,
            triggers=triggers,
            invalidation=invalidation,
            reasons=summary_reasons(summary, guide_4h, guide_1h),
        )
