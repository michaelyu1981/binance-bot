"""CoinPilot No-Martingale Guard v1."""

from __future__ import annotations

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.helpers import is_bearish, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


class CoinPilotNoMartingaleGuard:
    definition = StrategyDefinition(
        slug="coinpilot_no_martingale_guard_v1",
        name="CoinPilot No-Martingale Guard v1",
        style="Risk-control guardrail",
        description="Rejects averaging-down or doubling-down logic until trend and risk improve.",
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
        guide_1d = guides_by_interval.get("1d")
        dangerous = (
            summary.overall in ("SELL", "STRONG SELL", "AVOID")
            or summary.bias in ("Bearish", "Strong Bearish")
            or is_bearish(guide_4h)
        )
        if dangerous:
            verdict = "DO NOT AVERAGE DOWN"
            risk_level = "High"
            thesis = f"{symbol} fails the risk guard. Doubling down or martingale-style sizing is blocked."
            triggers = ("Reduce risk first; wait for trend recovery and support confirmation.",)
            invalidation = ("No martingale logic is allowed by this project safety policy.",)
        else:
            verdict = "RISK CHECK PASS"
            risk_level = "Medium"
            thesis = f"{symbol} does not trigger the martingale risk block, but this is still not permission to trade."
            triggers = ("Use fixed risk only in future dry-run simulations.",)
            invalidation = ("Block the setup if the 4H or 1D trend turns bearish.",)
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
