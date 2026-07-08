"""Claude Mean Reversion Sniper.

Deterministic dry-run 1-minute scalper. Buys deep statistical flushes only
after the first green candle, targets an ATR-scaled fee-aware profit, and
cuts losses at a hard ATR stop or a time stop. Single position only, no
averaging down. It must not execute live orders, call exchange clients, or
access Binance order endpoints.
"""

from __future__ import annotations

from decimal import Decimal

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.claude_common import ClaudeDecision, RollingStats, WilderAtr
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


Z_SCORE_LOOKBACK = 100
Z_SCORE_ENTRY_THRESHOLD = Decimal("-2.8")
ATRP_MINIMUM_PERCENT = Decimal("0.06")
MINIMUM_TARGET_FRACTION = Decimal("0.008")
TARGET_ATR_MULTIPLIER = Decimal("6")
STOP_ATR_MULTIPLIER = Decimal("3")
TIME_STOP_CANDLES = 45


class ClaudeMeanReversionSniper:
    definition = StrategyDefinition(
        slug="claude_mean_reversion_sniper",
        name="Claude Mean Reversion Sniper",
        style="1-minute dry-run statistical exhaustion scalper",
        description=(
            "Buys 100-period Z-score flushes below -2.8 after the first green "
            "candle, targets max(0.8%, 6xATR) to clear fees, stops at 3xATR, "
            "and time-stops after 45 candles."
        ),
    )

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._atr = WilderAtr(14)
        self._stats = RollingStats(Z_SCORE_LOOKBACK)
        self._previous_close: Decimal | None = None
        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.target_price = Decimal("0")
        self.stop_price = Decimal("0")
        self.candles_held = 0

    def on_candle_tick(self, candle: dict[str, Decimal]) -> ClaudeDecision:
        close = candle["close"]
        atr = self._atr.update(candle["high"], candle["low"], close)
        z_score = self._stats.z_score(close)
        self._stats.push(close)
        previous_close = self._previous_close
        self._previous_close = close

        if atr is None or atr <= 0 or z_score is None or previous_close is None:
            return ClaudeDecision(action="WAIT", reason="Warming up indicators.")

        if self.is_in_position:
            self.candles_held += 1
            if close >= self.target_price:
                self._exit_position()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason="Fee-aware ATR profit target reached.",
                    price=close,
                )
            if close <= self.stop_price:
                self._exit_position()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason="Hard 3xATR stop hit; loss bounded.",
                    price=close,
                )
            if self.candles_held >= TIME_STOP_CANDLES:
                self._exit_position()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason="Time stop; mean-reversion thesis expired.",
                    price=close,
                )
            return ClaudeDecision(action="HOLD", reason="Waiting on target, stop, or time stop.")

        atrp = (atr / close) * Decimal("100")
        if atrp < ATRP_MINIMUM_PERCENT:
            return ClaudeDecision(action="WAIT", reason="Volatility below scalp minimum.")
        if z_score > Z_SCORE_ENTRY_THRESHOLD:
            return ClaudeDecision(action="WAIT", reason="No statistical exhaustion flush.")
        if close <= previous_close:
            return ClaudeDecision(action="WAIT", reason="Flush present but no green candle yet.")

        target_fraction = max(MINIMUM_TARGET_FRACTION, (atr * TARGET_ATR_MULTIPLIER) / close)
        self.is_in_position = True
        self.entry_price = close
        self.target_price = close * (Decimal("1") + target_fraction)
        self.stop_price = close - (atr * STOP_ATR_MULTIPLIER)
        self.candles_held = 0
        return ClaudeDecision(
            action="SIMULATED_BUY",
            reason="Exhaustion flush with green-candle confirmation.",
            price=close,
        )

    def _exit_position(self) -> None:
        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.target_price = Decimal("0")
        self.stop_price = Decimal("0")
        self.candles_held = 0

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
                thesis=f"{symbol} cannot evaluate the sniper without close and ATR data.",
                triggers=("Collect enough 1-minute candles before evaluating.",),
                invalidation=("No simulation without ATR and 100 closes of history.",),
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
                thesis=f"{symbol} volatility is below the {ATRP_MINIMUM_PERCENT}% scalp minimum.",
                triggers=(f"Wait for ATRP >= {ATRP_MINIMUM_PERCENT}% before scanning flushes.",),
                invalidation=("Fee drag exceeds expected move in compressed volatility.",),
                reasons=summary_reasons(summary, guide, guides_by_interval.get("1h")),
            )
        return self._decision(
            symbol=symbol,
            user_label=user_label,
            verdict="SNIPER SCANNING",
            score=summary.score,
            risk_level="High",
            thesis=f"{symbol} sniper is scanning for Z-score flushes on 1-minute data.",
            triggers=(
                f"Entry needs 100-period Z-score <= {Z_SCORE_ENTRY_THRESHOLD} plus a green candle.",
                f"Target is max({MINIMUM_TARGET_FRACTION * 100}%, {TARGET_ATR_MULTIPLIER}xATR) above entry.",
                f"Stop is {STOP_ATR_MULTIPLIER}xATR below entry; time stop after {TIME_STOP_CANDLES} candles.",
            ),
            invalidation=(
                "No averaging down; one position at a time.",
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
