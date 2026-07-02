"""CoinPilot Gear Shifting Algo.

This is a dry-run advisory model only. It must not execute live orders.
"""

from __future__ import annotations

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


GEAR_GRID_EXIT_MULTIPLIER = "1.005"
GEAR_ONE_ALLOCATION = "20%"
GEAR_TWO_ALLOCATION = "50%"
GEAR_THREE_TIER_PLAN = "20% / 30% / 50%"


class CoinPilotGearShiftingAlgo:
    definition = StrategyDefinition(
        slug="coinpilot_gear_shifting_algo_v1",
        name="CoinPilot Gear Shifting Algo",
        style="1-minute dry-run 3-gear hybrid state machine",
        description="Models volatility snap-back, momentum trailing, and defensive grid accumulation as deterministic gears.",
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
            thesis = f"{symbol} cannot run the gear-shift dry-run check because candle signals are unavailable."
            triggers = ("Collect enough 1-minute candle history before evaluating the gear state machine.",)
            invalidation = ("Do not simulate gears without close, RSI, Bollinger, EMA, SMA, MACD, and ATR data.",)
            reasons = ("Missing shortest-timeframe guide.",)
        elif self._gear_data_missing(guide):
            verdict = "BLOCKED_BY_RISK"
            score = max(0, summary.score - 20)
            risk_level = "High"
            thesis = f"{symbol} gear-shift logic is blocked because one or more required indicators are unavailable."
            triggers = (
                "Wait for close, SMA50, EMA20, Bollinger lower, RSI14, MACD histogram, and ATR14.",
            )
            invalidation = ("No simulated gear entry while required math inputs are missing or ATR is zero.",)
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                "Required gear inputs are incomplete.",
            )
        elif self._gear_one_snapback_setup(guide):
            verdict = "GEAR 1 SNAP-BACK WATCH"
            score = min(100, summary.score + 6)
            risk_level = "High"
            thesis = (
                f"{symbol} matches the Gear 1 volatility snap-back filter: above SMA50, below lower band, "
                "RSI below 25, and MACD histogram negative. This remains dry-run only."
            )
            triggers = (
                f"Gear 1 dry-run: buy simulation uses {GEAR_ONE_ALLOCATION} if candle low touches lower band - 1.0 ATR.",
                "Exit simulation: close at or above EMA20 resets to Gear 0.",
                "If still below average entry after 3 candles, shift to Gear 3 instead of realizing a loss.",
            )
            invalidation = (
                "Block if ATR is missing or zero.",
                "Block if price loses structure and Gear 3 allocation would exceed the max cycle budget.",
                "No live orders are allowed.",
            )
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                "Gear 1 is a fast snap-back model, not a live trading command.",
            )
        elif self._gear_two_prime_setup(guide):
            verdict = "GEAR 2 PRIMED"
            score = min(100, summary.score + 3)
            risk_level = "High"
            thesis = (
                f"{symbol} is oversold at or below the lower Bollinger Band. Gear 2 is primed, "
                "but the model waits for RSI recovery and MACD confirmation before simulation."
            )
            triggers = (
                "Prime state: RSI was oversold below 30 at or below the lower Bollinger Band.",
                f"Gear 2 dry-run entry: {GEAR_TWO_ALLOCATION} after RSI crosses above 30, MACD line crosses above signal, and close remains below EMA20.",
                "Trailing exit simulation: highest price since entry - 0.5 ATR.",
            )
            invalidation = (
                "Do not simulate entry until momentum confirmation appears.",
                "If price breaks average entry - 1.5 ATR after entry, shift to Gear 3 defensive grid.",
            )
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                "Gear 2 separates oversold priming from actual momentum confirmation.",
            )
        elif self._gear_two_momentum_setup(guide):
            verdict = "GEAR 2 MOMENTUM WATCH"
            score = min(100, summary.score + 5)
            risk_level = "Medium"
            thesis = (
                f"{symbol} shows a momentum recovery profile suitable for Gear 2 dry-run review, "
                "but CoinPilot has not enabled persistent gear state yet."
            )
            triggers = (
                f"Gear 2 dry-run entry would use {GEAR_TWO_ALLOCATION} after prior oversold priming.",
                "Track highest price since entry for a 0.5 ATR trailing stop simulation.",
                "Shift to Gear 3 if price later breaks average entry - 1.5 ATR.",
            )
            invalidation = (
                "No Gear 2 simulation without prior oversold priming.",
                "No live order execution.",
            )
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                "MACD is bullish while price remains below EMA20.",
            )
        else:
            verdict = "WAIT"
            score = summary.score
            risk_level = "High"
            thesis = (
                f"{symbol} does not currently match Gear 1 or Gear 2 dry-run entry conditions. "
                "Gear 3 is only entered after a failed Gear 1 or Gear 2 state."
            )
            triggers = (
                "Gear 1: close > SMA50, close < lower band, RSI < 25, MACD histogram < 0, then low touches lower band - 1.0 ATR.",
                "Gear 2 prime: close <= lower band and RSI < 30.",
                f"Gear 3 defensive grid uses {GEAR_THREE_TIER_PLAN} and exits at average entry x {GEAR_GRID_EXIT_MULTIPLIER}.",
            )
            invalidation = (
                "No Gear 3 without a prior failed Gear 1 or Gear 2 state.",
                "No live orders are allowed.",
                "No simulation if ATR or price data is invalid.",
            )
            reasons = summary_reasons(summary, guide, guides_by_interval.get("1h")) + (
                "Current snapshot does not activate a gear.",
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

    def _gear_data_missing(self, guide: TechnicalSignalGuide) -> bool:
        return (
            guide.current_price is None
            or guide.current_price <= 0
            or guide.sma50 is None
            or guide.ema20 is None
            or guide.bollinger_lower is None
            or guide.rsi14 is None
            or guide.macd is None
            or guide.macd_signal is None
            or guide.macd_histogram is None
            or guide.atr14 is None
            or guide.atr14 <= 0
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
