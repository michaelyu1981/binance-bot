"""CoinPilot Trend Guard v1."""

from __future__ import annotations

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.helpers import is_bearish, is_bullish, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


class CoinPilotTrendGuard:
    definition = StrategyDefinition(
        slug="coinpilot_trend_guard_v1",
        name="CoinPilot Trend Guard v1",
        style="Multi-timeframe trend confirmation",
        description="Waits for 1D and 4H alignment before treating a setup as actionable.",
    )

    def evaluate(
        self,
        *,
        symbol: str,
        summary: MultiTimeframeSignalSummary,
        guides_by_interval: dict[str, TechnicalSignalGuide],
        user_label: str,
    ) -> StrategyDecision:
        guide_1d = guides_by_interval.get("1d")
        guide_4h = guides_by_interval.get("4h")
        aligned_bullish = is_bullish(guide_1d) and is_bullish(guide_4h)
        aligned_bearish = is_bearish(guide_1d) and is_bearish(guide_4h)
        if aligned_bullish and summary.score >= 65:
            verdict = "BUY WATCH"
            thesis = f"{symbol} has higher-timeframe bullish alignment, but CoinPilot remains advisory only."
            triggers = (
                "Wait for price to hold above 4H support.",
                "Prefer entries only after momentum stays bullish on 4H and 1D.",
            )
            invalidation = ("Stand down if 4H closes below support or the 1D bias turns bearish.",)
            risk_level = "Medium"
        elif aligned_bearish:
            verdict = "RISK-OFF"
            thesis = f"{symbol} has higher-timeframe bearish alignment, so trend-following entries are not clean."
            triggers = ("Wait for reclaim above SMA50 or a new bullish 4H structure.",)
            invalidation = ("Avoid long bias while 1D and 4H remain bearish.",)
            risk_level = "High"
        else:
            verdict = "WAIT"
            thesis = f"{symbol} does not have clean 1D and 4H alignment yet."
            triggers = ("Wait for 1D and 4H bias to agree before treating the setup as tradable.",)
            invalidation = ("Avoid forcing trades while timeframes conflict.",)
            risk_level = "Medium"
        return StrategyDecision(
            strategy=self.definition,
            user_label=user_label,
            symbol=symbol,
            verdict=verdict,
            score=summary.score,
            risk_level=risk_level,
            mode="Advisory only",
            thesis=thesis,
            triggers=triggers,
            invalidation=invalidation,
            reasons=summary_reasons(summary, guide_1d, guide_4h),
        )
