"""CoinPilot Reclaim v1."""

from __future__ import annotations

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.helpers import summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


class CoinPilotReclaim:
    definition = StrategyDefinition(
        slug="coinpilot_reclaim_v1",
        name="CoinPilot Reclaim v1",
        style="Pullback recovery and moving-average reclaim",
        description="Watches weak or oversold markets for recovery above key averages.",
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
        weak_but_recoverable = any(
            guide is not None
            and guide.rsi14 is not None
            and guide.rsi14 < 50
            and guide.price_vs_ema20 == "Below"
            for guide in (guide_4h, guide_1h)
        )
        if weak_but_recoverable and "Bearish" not in summary.bias:
            verdict = "RECLAIM WATCH"
            thesis = f"{symbol} is weak short term, but not fully bearish across the summary."
            triggers = (
                "Wait for close back above EMA20.",
                "Then require RSI recovery above 50 before treating it as a recovery setup.",
            )
            invalidation = ("Cancel if price breaks support before reclaiming EMA20.",)
            risk_level = "Medium"
        elif weak_but_recoverable:
            verdict = "WAIT"
            thesis = f"{symbol} is weak and needs proof of recovery before any bullish interpretation."
            triggers = ("Wait for EMA20 reclaim plus RSI above 50.",)
            invalidation = ("Avoid catching the dip while higher timeframe bias remains bearish.",)
            risk_level = "High"
        else:
            verdict = "NO RECLAIM SETUP"
            thesis = f"{symbol} is not currently matching the reclaim setup rules."
            triggers = ("Wait for a pullback, stabilization, and reclaim of EMA20.",)
            invalidation = ("Do not use reclaim logic when price is already extended or structure is unclear.",)
            risk_level = "Low"
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
            reasons=summary_reasons(summary, guide_4h, guide_1h),
        )
