"""Claude Triad Confluence.

An original algorithm blending three independent pillars into one deterministic
entry: TECHNICAL (a confirmed bullish chart pattern -- inverse head-and-
shoulders or a false-breakdown/bear-trap reclaim), CALENDAR (documented
behavioral-finance calendar effects: hour-of-day and day-of-week volume
seasonality measured from this project's own history, plus Ghost Month
avoidance), and CYCLE (a Donchian new-high/new-low regime read, refusing new
entries into a confirmed downtrend). All three must align before a trade is
considered; none alone is sufficient.

Designed for the daily chart, long-only spot. Every position carries a
mandatory ATR stop-loss (no unprotected downside, learned the hard way from
every no-stop-loss strategy in this project's history) and a trail that arms
once the trade is meaningfully in profit, so a real trend is not capped
early. Single position, no averaging down.

Dry-run only, advisory. It must not execute live orders, call exchange
clients, or access Binance order endpoints.
"""

from __future__ import annotations

from decimal import Decimal

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.claude_calendar_cycle import calendar_cycle_score
from app.strategies.claude_common import ClaudeDecision, DonchianRegime, RunningRsi, WilderAtr
from app.strategies.claude_pattern_signals import FalseBreakoutDetector, InverseHeadAndShouldersDetector
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


SWING_WINDOW_CANDLES = 3
MIN_CALENDAR_CYCLE_SCORE = Decimal("-0.5")  # allow mildly unfavorable timing, block clearly bad windows
RSI_MAX_AT_ENTRY = Decimal("65")  # don't buy an already-extended move
ATRP_MINIMUM_PERCENT = Decimal("0.3")  # daily-scale fee-aware floor
STOP_ATR_MULTIPLIER = Decimal("2.5")
TRAIL_ARM_ATR_MULTIPLIER = Decimal("2.5")
TRAIL_ATR_MULTIPLIER = Decimal("3")
REGIME_LEVEL_WINDOW = 90  # ~3 months of daily candles
REGIME_EVENT_WINDOW = 90


class ClaudeTriadConfluence:
    definition = StrategyDefinition(
        slug="claude_triad_confluence",
        name="Claude Triad Confluence",
        style="dry-run daily-chart blend: pattern + calendar seasonality + regime cycle",
        description=(
            "Buys only when three independent pillars agree: a confirmed "
            "bullish pattern (inverse head-and-shoulders or a false-"
            "breakdown reclaim), a favorable calendar/volume-seasonality "
            "score (hour-of-day, day-of-week, and Ghost Month avoidance, "
            "measured from this project's own history), and a non-bear "
            "Donchian regime. Every position has a mandatory 2.5xATR stop "
            "and a 3xATR trail that arms once meaningfully in profit."
        ),
    )

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._atr = WilderAtr(14)
        self._rsi = RunningRsi(14)
        self._regime = DonchianRegime(REGIME_LEVEL_WINDOW, REGIME_EVENT_WINDOW)
        self._inverse_hs = InverseHeadAndShouldersDetector(SWING_WINDOW_CANDLES)
        self._false_breakout = FalseBreakoutDetector(SWING_WINDOW_CANDLES)
        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.stop_price = Decimal("0")
        self.peak_close = Decimal("0")
        self.trail_armed = False

    def on_candle_tick(self, candle: dict[str, Decimal]) -> ClaudeDecision:
        high = candle["high"]
        low = candle["low"]
        close = candle["close"]
        open_time_ms = candle.get("open_time_ms")

        atr = self._atr.update(high, low, close)
        rsi = self._rsi.update(close)
        regime = self._regime.update(high, low)
        pattern_hs = self._inverse_hs.update(high=high, low=low, close=close)
        pattern_false_breakout = self._false_breakout.update(high=high, low=low, close=close)

        if atr is None or atr <= 0 or rsi is None or regime is None:
            return ClaudeDecision(action="WAIT", reason="Warming up ATR, RSI, and regime history.")

        if self.is_in_position:
            if close > self.peak_close:
                self.peak_close = close
            if not self.trail_armed and close >= self.entry_price + (atr * TRAIL_ARM_ATR_MULTIPLIER):
                self.trail_armed = True
            floor = self.stop_price
            if self.trail_armed:
                floor = max(floor, self.peak_close - (atr * TRAIL_ATR_MULTIPLIER))
            if close <= floor:
                self._exit_position()
                return ClaudeDecision(action="SIMULATED_SELL", reason="Stop or armed trail hit.", price=close)
            return ClaudeDecision(action="HOLD", reason="Position active; managing stop/trail.")

        pattern_signal = pattern_hs or pattern_false_breakout
        if pattern_signal is None:
            return ClaudeDecision(action="WAIT", reason="Technical pillar: no confirmed bullish pattern yet.")

        if regime == "BEAR":
            return ClaudeDecision(
                action="WAIT",
                reason=f"Cycle pillar failed: {pattern_signal.name} confirmed but regime is BEAR.",
            )

        atrp = (atr / close) * Decimal("100")
        if atrp < ATRP_MINIMUM_PERCENT:
            return ClaudeDecision(action="WAIT", reason="Volatility below the fee-aware minimum.")

        if rsi > RSI_MAX_AT_ENTRY:
            return ClaudeDecision(
                action="WAIT",
                reason=f"{pattern_signal.name} confirmed but RSI {rsi:.1f} is already extended.",
            )

        cycle_score = calendar_cycle_score(open_time_ms) if open_time_ms is not None else Decimal("0")
        if cycle_score < MIN_CALENDAR_CYCLE_SCORE:
            return ClaudeDecision(
                action="WAIT",
                reason=(
                    f"Calendar pillar failed: {pattern_signal.name} confirmed but timing score "
                    f"{cycle_score:.2f} is below {MIN_CALENDAR_CYCLE_SCORE} (thin volume window or Ghost Month)."
                ),
            )

        self.is_in_position = True
        self.entry_price = close
        self.stop_price = close - (atr * STOP_ATR_MULTIPLIER)
        self.peak_close = close
        self.trail_armed = False
        return ClaudeDecision(
            action="SIMULATED_BUY",
            reason=(
                f"Triad confluence: {pattern_signal.detail} Regime={regime}, "
                f"calendar score={cycle_score:.2f}, RSI={rsi:.1f}."
            ),
            price=close,
        )

    def _exit_position(self) -> None:
        self.is_in_position = False
        self.entry_price = Decimal("0")
        self.stop_price = Decimal("0")
        self.peak_close = Decimal("0")
        self.trail_armed = False

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
                thesis=f"{symbol} cannot evaluate the triad confluence without close and ATR data.",
                triggers=("Collect enough daily candle history before evaluating.",),
                invalidation=("No simulation without confirmed swing points and a full regime window.",),
                reasons=("Missing shortest-timeframe candle signals.",),
            )
        return self._decision(
            symbol=symbol,
            user_label=user_label,
            verdict="TRIAD SCANNING",
            score=summary.score,
            risk_level="Medium",
            thesis=f"{symbol} is scanning for pattern, calendar, and regime agreement on the daily chart.",
            triggers=(
                "Technical: a confirmed inverse head-and-shoulders or false-breakdown reclaim.",
                "Calendar: hour/day volume seasonality and Ghost Month avoidance score above the minimum.",
                "Cycle: Donchian regime must not be BEAR.",
            ),
            invalidation=(
                f"Stop is {STOP_ATR_MULTIPLIER}xATR below entry; trail arms after {TRAIL_ARM_ATR_MULTIPLIER}xATR of profit.",
                "No live orders are allowed.",
            ),
            reasons=summary_reasons(summary, guide, guides_by_interval.get("1d")),
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
