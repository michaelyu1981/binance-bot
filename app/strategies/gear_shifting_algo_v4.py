"""CoinPilot Gear Shifting Algo V4.

This is a dry-run advisory model only. It must not execute live orders.
"""

from __future__ import annotations

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


ATRP_MINIMUM_PERCENT = "1.5"
BASELINE_PROFIT_MULTIPLIER = "1.005"
BREAKOUT_VOLUME_MULTIPLIER = "2.0"
BREAKOUT_MICRO_TRAIL_ATR_MULTIPLIER = "0.25"
GEAR_V4_TIER_PLAN = "20% / 30% / 50%"


class CoinPilotGearShiftingAlgoV4:
    definition = StrategyDefinition(
        slug="coinpilot_gear_shifting_algo_v4",
        name="CoinPilot Gear Shifting Algo V4",
        style="1-minute dry-run 3-gear hybrid with breakout extension guardrails",
        description="Adds ATRP volatility gating, 1.005x baseline exit, and breakout extension mode with strict profit guardrails.",
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
            thesis = f"{symbol} cannot evaluate Gear Shifting Algo V4 because candle signals are unavailable."
            triggers = ("Collect enough 1-minute candle history before evaluating V4.",)
            invalidation = ("No V4 simulation without candle, RSI, Bollinger, EMA, SMA, MACD, ATR, and volume data.",)
            reasons = ("Missing shortest-timeframe guide.",)
        elif self._v4_data_missing(guide):
            verdict = "BLOCKED_BY_RISK"
            score = max(0, summary.score - 25)
            risk_level = "High"
            thesis = f"{symbol} V4 is blocked because one or more required mathematical inputs are unavailable."
            triggers = (
                "Wait for close, SMA50, EMA20, Bollinger bands, RSI14, MACD, ATR14, and volume confirmation.",
            )
            invalidation = ("No simulated V4 state change while required data is missing or ATR is zero.",)
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                "V4 requires ATRP, volume confirmation, Bollinger bands, and MACD histogram.",
            )
        elif guide.atr_percent is not None and guide.atr_percent < 1.5:
            verdict = "BLOCKED_BY_VOLATILITY"
            score = max(0, summary.score - 15)
            risk_level = "Medium"
            thesis = (
                f"{symbol} fails the V4 ATRP volatility gate. ATRP is below {ATRP_MINIMUM_PERCENT}%, "
                "so the model skips the tick instead of forcing a scalp."
            )
            triggers = (f"Wait for ATRP >= {ATRP_MINIMUM_PERCENT}% before considering V4 entries.",)
            invalidation = ("Do not run Gear 1, Gear 2, or Gear 3 while volatility is below the ATRP gate.",)
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                f"ATRP gate: {ATRP_MINIMUM_PERCENT}% minimum.",
            )
        elif self._breakout_extension_setup(guide):
            verdict = "BREAKOUT EXTENSION WATCH"
            score = min(100, summary.score + 10)
            risk_level = "High"
            thesis = (
                f"{symbol} meets the V4 breakout extension profile: strong volume, close near/above upper band, "
                "and positive MACD histogram pressure. V4 would freeze standard gear rules and trail the breakout."
            )
            triggers = (
                f"Baseline profit checkpoint: average entry x {BASELINE_PROFIT_MULTIPLIER}.",
                f"Activate extension only if volume >= volume average x {BREAKOUT_VOLUME_MULTIPLIER}.",
                "While extension is active, update breakout_highest_peak whenever close makes a new high.",
                f"Micro-trail floor: breakout peak - {BREAKOUT_MICRO_TRAIL_ATR_MULTIPLIER} ATR.",
            )
            invalidation = (
                "Exit simulation if volume falls below average volume.",
                "Exit simulation if close drops below the micro-trail floor.",
                "Bypass all standard gear down-shift rules while breakout extension is active.",
            )
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                "V4 extension mode prioritizes profit protection over new entries.",
            )
        elif self._gear_one_snapback_setup(guide):
            verdict = "V4 GEAR 1 SNAP-BACK WATCH"
            score = min(100, summary.score + 6)
            risk_level = "High"
            thesis = (
                f"{symbol} passes the ATRP gate and matches V4 Gear 1 volatility snap-back conditions."
            )
            triggers = (
                "Gear 1 dry-run: close > SMA50, close < lower Bollinger Band, RSI < 25, MACD histogram < 0.",
                "Target entry price: lower Bollinger Band - 1.0 ATR; candle low must touch it.",
                "If held for 3 candles and still below average entry, shift to Gear 3 instead of taking a loss.",
            )
            invalidation = (
                "No live order execution.",
                "No Gear 1 simulation if ATRP falls below the volatility gate.",
            )
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                "V4 Gear 1 is a volatility snap-back setup.",
            )
        elif self._gear_two_prime_setup(guide):
            verdict = "V4 GEAR 2 PRIMED"
            score = min(100, summary.score + 3)
            risk_level = "High"
            thesis = (
                f"{symbol} is oversold at or below the lower band. V4 primes Gear 2 but waits for recovery confirmation."
            )
            triggers = (
                "Prime: close <= lower Bollinger Band and RSI < 30.",
                "Entry confirmation: RSI crosses above 30, MACD crosses above signal, and close remains below EMA20.",
                "If a secondary dump reaches average entry - 1.5 ATR after entry, shift to Gear 3.",
            )
            invalidation = (
                "No simulated Gear 2 entry without momentum confirmation.",
                "No live order execution.",
            )
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                "V4 Gear 2 separates oversold priming from momentum confirmation.",
            )
        elif self._gear_two_momentum_setup(guide):
            verdict = "V4 GEAR 2 MOMENTUM WATCH"
            score = min(100, summary.score + 5)
            risk_level = "Medium"
            thesis = (
                f"{symbol} shows V4 momentum recovery conditions, but persistent state is not enabled in this dashboard summary."
            )
            triggers = (
                "Dry-run entry would use 50% allocation after prior oversold priming.",
                "Trailing exit: highest price since entry - 0.5 ATR.",
                "Fallback to Gear 3 if price breaks average entry - 1.5 ATR.",
            )
            invalidation = (
                "No Gear 2 simulation without prior oversold priming.",
                "No live order execution.",
            )
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                "V4 momentum check sees MACD above signal while price remains below EMA20.",
            )
        else:
            verdict = "WAIT"
            score = summary.score
            risk_level = "High"
            thesis = (
                f"{symbol} passes no V4 entry or breakout-extension condition in the current snapshot."
            )
            triggers = (
                f"Volatility gate: ATRP >= {ATRP_MINIMUM_PERCENT}%.",
                "Gear 1: snap-back setup with lower-band overshoot.",
                "Gear 2: oversold prime followed by RSI and MACD recovery.",
                f"Breakout extension: baseline x {BASELINE_PROFIT_MULTIPLIER}, volume x {BREAKOUT_VOLUME_MULTIPLIER}, upper band, and improving MACD histogram.",
                f"Gear 3 defensive grid plan: {GEAR_V4_TIER_PLAN}.",
            )
            invalidation = (
                "No Gear 3 without a failed Gear 1 or Gear 2 state.",
                "No breakout extension without volume and upper-band confirmation.",
                "No live orders are allowed.",
            )
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                "Current snapshot does not activate V4.",
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

    def _v4_data_missing(self, guide: TechnicalSignalGuide) -> bool:
        return (
            guide.current_price is None
            or guide.current_price <= 0
            or guide.sma50 is None
            or guide.ema20 is None
            or guide.bollinger_upper is None
            or guide.bollinger_lower is None
            or guide.rsi14 is None
            or guide.macd is None
            or guide.macd_signal is None
            or guide.macd_histogram is None
            or guide.atr14 is None
            or guide.atr14 <= 0
            or guide.atr_percent is None
            or guide.volume_vs_average_percent is None
        )

    def _breakout_extension_setup(self, guide: TechnicalSignalGuide) -> bool:
        return (
            guide.current_price is not None
            and guide.bollinger_upper is not None
            and guide.macd_histogram is not None
            and guide.volume_vs_average_percent is not None
            and guide.volume_vs_average_percent >= 200
            and guide.current_price >= guide.bollinger_upper
            and guide.macd_histogram > 0
        )

    def _gear_one_snapback_setup(self, guide: TechnicalSignalGuide) -> bool:
        return (
            guide.current_price is not None
            and guide.sma50 is not None
            and guide.bollinger_lower is not None
            and guide.rsi14 is not None
            and guide.macd_histogram is not None
            and guide.current_price > guide.sma50
            and guide.current_price < guide.bollinger_lower
            and guide.rsi14 < 25
            and guide.macd_histogram < 0
        )

    def _gear_two_prime_setup(self, guide: TechnicalSignalGuide) -> bool:
        return (
            guide.current_price is not None
            and guide.bollinger_lower is not None
            and guide.rsi14 is not None
            and guide.current_price <= guide.bollinger_lower
            and guide.rsi14 < 30
        )

    def _gear_two_momentum_setup(self, guide: TechnicalSignalGuide) -> bool:
        return (
            guide.current_price is not None
            and guide.ema20 is not None
            and guide.rsi14 is not None
            and guide.macd is not None
            and guide.macd_signal is not None
            and guide.rsi14 > 30
            and guide.macd > guide.macd_signal
            and guide.current_price < guide.ema20
        )
