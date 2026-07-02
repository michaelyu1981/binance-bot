"""CoinPilot Grid Accumulation Scalper.

This is a dry-run advisory model only. It must not execute live orders.
"""

from __future__ import annotations

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


GRID_SCALPER_PROFIT_TARGET_MULTIPLIER = "1.008"
GRID_SCALPER_TIER_ALLOCATIONS = "20% / 30% / 50%"


class CoinPilotGridAccumulationScalper:
    definition = StrategyDefinition(
        slug="coinpilot_grid_accumulation_scalper_v1",
        name="CoinPilot Grid Accumulation Scalper",
        style="1-minute dry-run grid accumulation model",
        description="Models 20/30/50 tiered spot accumulation and a full simulated exit at average entry x 1.008.",
    )

    def evaluate(
        self,
        *,
        symbol: str,
        summary: MultiTimeframeSignalSummary,
        guides_by_interval: dict[str, TechnicalSignalGuide],
        user_label: str,
    ) -> StrategyDecision:
        guide = shortest_timeframe_guide(guides_by_interval)
        if guide is None:
            verdict = "BLOCKED_BY_RISK"
            score = 0
            risk_level = "High"
            thesis = f"{symbol} cannot be evaluated because candle/indicator data is unavailable."
            triggers = ("Collect enough 1-minute candle data before dry-run simulation.",)
            invalidation = ("Do not run grid logic without candle, RSI, Bollinger, and ATR data.",)
            reasons = ("Missing shortest-timeframe guide.",)
        elif self._grid_data_missing(guide):
            verdict = "BLOCKED_BY_RISK"
            score = max(0, summary.score - 20)
            risk_level = "High"
            thesis = f"{symbol} grid scalper is blocked because required 1-minute-style indicators are incomplete."
            triggers = ("Wait for close, lower Bollinger Band, RSI14, and ATR14 to be available.",)
            invalidation = ("No simulated tier entry while indicators are missing or ATR is zero.",)
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                "Required grid inputs: close, lower Bollinger Band, RSI14, ATR14.",
            )
        elif guide.current_price <= guide.bollinger_lower and guide.rsi14 < 30:
            verdict = "SIMULATED BUY TIER 1 WATCH"
            score = min(100, summary.score + 5)
            risk_level = "High"
            thesis = (
                f"{symbol} is near the dry-run Tier 1 condition: close below lower Bollinger Band "
                "with RSI below 30. This is averaging-down logic and stays simulation-only."
            )
            triggers = (
                "Tier 1 dry-run: allocate 20% of max sequence budget.",
                "Tier 2 dry-run: if price falls 1.0 ATR below Tier 1 entry, allocate 30%.",
                "Tier 3 dry-run: if price falls 2.0 ATR below Tier 2 entry, allocate 50%.",
                f"Exit dry-run: sell simulated full base balance at average entry x {GRID_SCALPER_PROFIT_TARGET_MULTIPLIER}.",
            )
            invalidation = (
                "Block if max allocation would be exceeded.",
                "Block if ATR is missing or zero.",
                "Block if trend keeps breaking down after Tier 3.",
            )
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                f"Tier allocation model: {GRID_SCALPER_TIER_ALLOCATIONS}.",
                f"Profit target multiplier: {GRID_SCALPER_PROFIT_TARGET_MULTIPLIER}, or 0.8% above blended average entry.",
            )
        else:
            verdict = "WAIT"
            score = summary.score
            risk_level = "High"
            thesis = (
                f"{symbol} does not meet the Tier 1 dry-run trigger. Grid accumulation is high risk "
                "because it adds exposure while price drops."
            )
            triggers = (
                "Wait for close <= lower Bollinger Band and RSI < 30 for Tier 1 simulation.",
                "Do not simulate Tier 2 or Tier 3 until a prior tier exists in dry-run state.",
                f"Target exit remains average entry x {GRID_SCALPER_PROFIT_TARGET_MULTIPLIER}.",
            )
            invalidation = (
                "No live orders are allowed.",
                "No martingale or doubling beyond the fixed 20/30/50 tier plan.",
                "No simulation if ATR or price data is invalid.",
            )
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                f"Tier allocation model: {GRID_SCALPER_TIER_ALLOCATIONS}.",
                "Current Tier 1 trigger is not fully active.",
            )

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

    def _grid_data_missing(self, guide: TechnicalSignalGuide) -> bool:
        return (
            guide.current_price is None
            or guide.current_price <= 0
            or guide.bollinger_lower is None
            or guide.rsi14 is None
            or guide.atr14 is None
            or guide.atr14 <= 0
        )
