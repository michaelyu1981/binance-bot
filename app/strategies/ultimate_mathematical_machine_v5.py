"""Ultimate Mathematical Machine V5.

This is a deterministic dry-run state machine only. It must not execute live
orders, call exchange clients, or access Binance order endpoints.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
import math

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


ATRP_MINIMUM_PERCENT = Decimal("1.5")
Z_SCORE_LOOKBACK = 100
Z_SCORE_ENTRY_THRESHOLD = Decimal("-3.2")
BASELINE_PROFIT_MULTIPLIER = Decimal("1.005")
FIB_TRAIL_ATR_MULTIPLIER = Decimal("0.236")
TIME_DECAY_CANDLES = 3
DEFENSIVE_GRID_ATR_MULTIPLIER = Decimal("2.0")
INITIAL_ALLOCATION_FRACTION = Decimal("0.5")
REMAINING_ALLOCATION_FRACTION = Decimal("0.5")
TRIG_VELOCITY_DEGREES = Decimal("70.0")


@dataclass(frozen=True)
class V5CandleFrame:
    close: Decimal
    low: Decimal
    atr14: Decimal
    ema3: Decimal | None
    previous_ema3: Decimal | None
    closes: tuple[Decimal, ...]


@dataclass(frozen=True)
class V5Decision:
    action: str
    reason: str
    price: Decimal | None = None
    usdt_amount: Decimal | None = None
    base_amount: Decimal | None = None
    z_score: Decimal | None = None
    theta_degrees: Decimal | None = None


class UltimateMathematicalMachineV5:
    definition = StrategyDefinition(
        slug="ultimate_mathematical_machine_v5",
        name="UltimateMathematicalMachineV5",
        style="1-minute dry-run Gaussian, trigonometric, and time-decay scalper",
        description="Uses ATRP, 100-period Z-score exhaustion, EMA3 velocity angle, Poisson-style time decay, and Fibonacci micro-trail exits.",
    )

    def __init__(self, max_allocation_usdt: Decimal | float | str = Decimal("100")) -> None:
        self.max_allocation_usdt = Decimal(str(max_allocation_usdt))
        self.reset()

    def reset(self) -> None:
        self.is_in_position = False
        self.current_state = 0
        self.candles_held_counter = 0
        self.total_base_accumulated = Decimal("0")
        self.total_usdt_spent = Decimal("0")
        self.average_entry_price = Decimal("0")
        self.highest_peak_since_entry = Decimal("0")
        self.fib_trail_active = False

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
            return self._decision(
                symbol=symbol,
                user_label=user_label,
                verdict="BLOCKED_BY_RISK",
                score=0,
                risk_level="High",
                thesis=f"{symbol} cannot evaluate V5 because candle signals are unavailable.",
                triggers=("Collect enough 1-minute candle history before evaluating V5.",),
                invalidation=("No V5 simulation without close, ATR, EMA velocity, and 100-period close history.",),
                reasons=("Missing shortest-timeframe guide.",),
            )
        if guide.current_price is None or guide.atr14 is None or guide.atr14 <= 0:
            return self._decision(
                symbol=symbol,
                user_label=user_label,
                verdict="BLOCKED_BY_RISK",
                score=max(0, summary.score - 25),
                risk_level="High",
                thesis=f"{symbol} V5 is blocked because close or ATR data is unavailable.",
                triggers=("Wait for valid close and ATR14 data.",),
                invalidation=("No V5 simulation while ATR is missing or zero.",),
                reasons=summary_reasons(summary, guide, guides_by_interval.get("1h")),
            )
        if guide.atr_percent is not None and guide.atr_percent < ATRP_MINIMUM_PERCENT:
            return self._decision(
                symbol=symbol,
                user_label=user_label,
                verdict="BLOCKED_BY_VOLATILITY",
                score=max(0, summary.score - 15),
                risk_level="Medium",
                thesis=f"{symbol} fails V5 ATRP gate. ATRP is below {ATRP_MINIMUM_PERCENT}%.",
                triggers=(f"Wait for ATRP >= {ATRP_MINIMUM_PERCENT}% before V5 entry scanning.",),
                invalidation=("No Gaussian exhaustion trap while volatility is compressed.",),
                reasons=summary_reasons(summary, guide, guides_by_interval.get("1h")),
            )
        return self._decision(
            symbol=symbol,
            user_label=user_label,
            verdict="V5 SCANNING",
            score=summary.score,
            risk_level="High",
            thesis=f"{symbol} V5 is ready to scan for Z-score exhaustion on 1-minute data.",
            triggers=(
                f"Entry requires 100-period Z-score <= {Z_SCORE_ENTRY_THRESHOLD}.",
                f"Baseline profit target is average entry x {BASELINE_PROFIT_MULTIPLIER}.",
                f"Fibonacci trail activates only when EMA3 velocity angle >= {TRIG_VELOCITY_DEGREES} degrees.",
            ),
            invalidation=(
                f"Poisson-style time decay exits after {TIME_DECAY_CANDLES} candles if still below average entry.",
                "No live orders are allowed.",
            ),
            reasons=summary_reasons(summary, guide, guides_by_interval.get("1h")),
        )

    def on_candle_tick(
        self,
        candle_data: dict[str, Decimal | float | str],
        indicator_data: dict[str, Decimal | float | str | Sequence[Decimal | float | str]],
    ) -> V5Decision:
        frame = self._frame(candle_data, indicator_data)
        if frame is None:
            return V5Decision(action="BLOCKED_BY_RISK", reason="Missing required candle or indicator data.")

        atrp = (frame.atr14 / frame.close) * Decimal("100")
        if not self.is_in_position:
            if atrp < ATRP_MINIMUM_PERCENT:
                return V5Decision(action="WAIT", reason="ATRP volatility gate blocked the tick.")

            z_score = self._z_score(frame.closes)
            if z_score is None:
                return V5Decision(action="WAIT", reason="Need at least 100 closes for Gaussian Z-score.")

            if z_score <= Z_SCORE_ENTRY_THRESHOLD:
                usdt_amount = self.max_allocation_usdt * INITIAL_ALLOCATION_FRACTION
                self._simulate_buy(usdt_amount=usdt_amount, price=frame.close)
                return V5Decision(
                    action="SIMULATED_BUY",
                    reason="Gaussian exhaustion trap triggered.",
                    price=frame.close,
                    usdt_amount=usdt_amount,
                    z_score=z_score,
                )
            return V5Decision(action="WAIT", reason="No Gaussian exhaustion anomaly.", z_score=z_score)

        self.candles_held_counter += 1
        if frame.close > self.highest_peak_since_entry:
            self.highest_peak_since_entry = frame.close

        if self.fib_trail_active:
            fib_floor = self.highest_peak_since_entry - (FIB_TRAIL_ATR_MULTIPLIER * frame.atr14)
            if frame.close < fib_floor:
                base_amount = self.total_base_accumulated
                self.reset()
                return V5Decision(
                    action="SIMULATED_SELL",
                    reason="Fibonacci peak micro-trail floor broke.",
                    price=frame.close,
                    base_amount=base_amount,
                )
            return V5Decision(action="HOLD", reason="Fibonacci trail active; peak guardrails still hold.")

        if frame.close >= self.average_entry_price * BASELINE_PROFIT_MULTIPLIER:
            theta = self._theta_degrees(frame.ema3, frame.previous_ema3)
            if theta is not None and theta >= TRIG_VELOCITY_DEGREES:
                self.fib_trail_active = True
                self.current_state = 2
                self.highest_peak_since_entry = frame.close
                return V5Decision(
                    action="HOLD_EXTENSION",
                    reason="Baseline target reached with trigonometric velocity; Fibonacci trail activated.",
                    price=frame.close,
                    theta_degrees=theta,
                )
            base_amount = self.total_base_accumulated
            self.reset()
            return V5Decision(
                action="SIMULATED_SELL",
                reason="Baseline 0.5% profit target reached without vertical velocity extension.",
                price=frame.close,
                base_amount=base_amount,
                theta_degrees=theta,
            )

        if self.candles_held_counter >= TIME_DECAY_CANDLES and frame.close < self.average_entry_price:
            base_amount = self.total_base_accumulated
            self.reset()
            return V5Decision(
                action="SIMULATED_SELL",
                reason="Poisson-style time-decay guardrail failed after 3 candles.",
                price=frame.close,
                base_amount=base_amount,
            )

        if (
            self.total_usdt_spent <= self.max_allocation_usdt * INITIAL_ALLOCATION_FRACTION
            and frame.close <= self.average_entry_price - (DEFENSIVE_GRID_ATR_MULTIPLIER * frame.atr14)
        ):
            usdt_amount = self.max_allocation_usdt * REMAINING_ALLOCATION_FRACTION
            self._simulate_buy(usdt_amount=usdt_amount, price=frame.close)
            return V5Decision(
                action="SIMULATED_BUY",
                reason="Defensive grid layer deployed after -2 ATR move.",
                price=frame.close,
                usdt_amount=usdt_amount,
            )

        return V5Decision(action="HOLD", reason="Position active; no exit or grid condition triggered.")

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

    def _simulate_buy(self, *, usdt_amount: Decimal, price: Decimal) -> None:
        base_amount = usdt_amount / price
        self.total_base_accumulated += base_amount
        self.total_usdt_spent += usdt_amount
        self.average_entry_price = self.total_usdt_spent / self.total_base_accumulated
        self.is_in_position = True
        self.current_state = 1
        self.highest_peak_since_entry = max(self.highest_peak_since_entry, price)
        self.candles_held_counter = 0

    def _frame(
        self,
        candle_data: dict[str, Decimal | float | str],
        indicator_data: dict[str, Decimal | float | str | Sequence[Decimal | float | str]],
    ) -> V5CandleFrame | None:
        close = self._decimal(candle_data.get("close"))
        low = self._decimal(candle_data.get("low"))
        atr = self._decimal(indicator_data.get("atr14") or indicator_data.get("atr"))
        if close is None or close <= 0 or low is None or atr is None or atr <= 0:
            return None
        closes_raw = indicator_data.get("closes")
        closes = self._decimal_sequence(closes_raw)
        if not closes:
            return None
        return V5CandleFrame(
            close=close,
            low=low,
            atr14=atr,
            ema3=self._decimal(indicator_data.get("ema3")),
            previous_ema3=self._decimal(indicator_data.get("previous_ema3")),
            closes=closes,
        )

    def _z_score(self, closes: tuple[Decimal, ...]) -> Decimal | None:
        if len(closes) < Z_SCORE_LOOKBACK:
            return None
        window = closes[-Z_SCORE_LOOKBACK:]
        mean = sum(window, Decimal("0")) / Decimal(Z_SCORE_LOOKBACK)
        variance = sum((value - mean) ** 2 for value in window) / Decimal(Z_SCORE_LOOKBACK)
        stddev = Decimal(str(math.sqrt(float(variance))))
        if stddev == 0:
            return None
        return (window[-1] - mean) / stddev

    def _theta_degrees(self, ema3: Decimal | None, previous_ema3: Decimal | None) -> Decimal | None:
        if ema3 is None or previous_ema3 is None:
            return None
        return Decimal(str(math.degrees(math.atan(float(ema3 - previous_ema3)))))

    def _decimal(self, value: object) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    def _decimal_sequence(self, values: object) -> tuple[Decimal, ...]:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return ()
        parsed = []
        for value in values:
            decimal = self._decimal(value)
            if decimal is None:
                return ()
            parsed.append(decimal)
        return tuple(parsed)
