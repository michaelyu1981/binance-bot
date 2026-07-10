"""Claude Triad Confluence V3.

Entry logic is IDENTICAL to Claude Triad Confluence (V1) -- same pattern
detectors, same calendar/cycle module, same regime gate, unchanged. This
version adds two layers, both verified against this project's own real
2-4yr backtest history before being built (see session notes):

1. Regime-flip protective exit. V1 only checks the Donchian regime at
   entry; while holding, a regime flip to BEAR is ignored and the position
   rides on the ATR stop/trail alone. Traced against real XTZUSDT data,
   this let a trade slide from -37% to -66% unrealized after the regime
   had already turned BEAR for months. V3 exits immediately the tick the
   regime flips BEAR while holding. Unlike the V2 daily/weekly-bias gate
   (which never fired because it sampled a different-cadence series only
   once a week), regime is already computed on every native tick here, so
   there is no cross-timeframe sampling gap.

2. A structural-decline coin screen. XTZUSDT profiled as the only one of
   this project's five watchlist coins with a negative 4-year return
   (-84.8%), sitting 87.4% below its own multi-year high, and the only one
   to have spent meaningful time (25.2%) in a confirmed BEAR regime -- the
   other four coins showed 0.0%. Since this strategy is spot/long-only, a
   coin in structural decline can only ever be a drag: there is no way to
   profit from further downside. V3 tracks each coin's own rolling
   all-time high and its trailing BEAR-regime frequency; a coin that is
   deeply below its own high and has spent meaningfully more time in BEAR
   regime than this project's normal coins is flagged "structurally weak."
   New entries are blocked while a coin is flagged. If already holding
   when a coin becomes flagged, V3 takes any available gain immediately
   instead of waiting for the trail to arm -- the odds of a durable trend
   continuing are working against a coin with this profile.

(Tested and explicitly rejected during design: a BTC-extended /
alt-lagging "rotation" buy rule. It back-tested negative across two
formulations and a 285-combination parameter grid; the one seemingly
strong result, on BNBUSDT, turned out to be a single historical rally
double-counted ~9 times by overlapping weekly windows, not a repeatable
effect. Not included in V3.)

Dry-run only, spot/long-only. It must not execute live orders, call
exchange clients, or access Binance order endpoints.
"""

from __future__ import annotations

from decimal import Decimal

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.claude_calendar_cycle import calendar_cycle_score
from app.strategies.claude_common import (
    ClaudeDecision,
    DonchianRegime,
    RollingFraction,
    RollingMax,
    RunningRsi,
    WilderAtr,
)
from app.strategies.claude_pattern_signals import FalseBreakoutDetector, InverseHeadAndShouldersDetector
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


SWING_WINDOW_CANDLES = 3
MIN_CALENDAR_CYCLE_SCORE = Decimal("-0.5")
RSI_MAX_AT_ENTRY = Decimal("65")
ATRP_MINIMUM_PERCENT = Decimal("0.3")
STOP_ATR_MULTIPLIER = Decimal("2.5")
TRAIL_ARM_ATR_MULTIPLIER = Decimal("2.5")
TRAIL_ATR_MULTIPLIER = Decimal("3")
REGIME_LEVEL_WINDOW = 90
REGIME_EVENT_WINDOW = 90

# Structural-decline coin screen.
ALL_TIME_HIGH_WINDOW = 10_000  # effectively unbounded for any realistic backtest length
ATH_DRAWDOWN_BLOCK_THRESHOLD = Decimal("-0.70")  # block entries >70% below the coin's own rolling high
BEAR_FRACTION_LOOKBACK_CANDLES = 104  # ~2 years of weekly candles
BEAR_FRACTION_BLOCK_THRESHOLD = Decimal("0.15")  # block if >15% of the trailing lookback was BEAR regime


class ClaudeTriadConfluenceV3:
    definition = StrategyDefinition(
        slug="claude_triad_confluence_v3",
        name="Claude Triad Confluence V3",
        style="dry-run: V1 pattern+calendar+cycle entry, plus regime-exit and a structural-decline coin screen",
        description=(
            "Same entry as Claude Triad Confluence V1 (pattern, calendar, "
            "regime), unchanged. Adds two layers verified against this "
            "project's own real backtest history: (1) an immediate exit "
            "if the Donchian regime flips BEAR while holding, instead of "
            "relying on the ATR stop/trail alone, and (2) a structural-"
            "decline coin screen that blocks new entries -- and forces "
            "early profit-taking on open positions -- for a coin sitting "
            "deeply below its own multi-year high with an unusually high "
            "fraction of trailing time spent in a confirmed BEAR regime."
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
        self._all_time_high = RollingMax(ALL_TIME_HIGH_WINDOW)
        self._bear_fraction = RollingFraction(BEAR_FRACTION_LOOKBACK_CANDLES)
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
        self._all_time_high.push(high)
        self._bear_fraction.push(regime == "BEAR")

        if atr is None or atr <= 0 or rsi is None or regime is None:
            return ClaudeDecision(action="WAIT", reason="Warming up ATR, RSI, and regime history.")

        is_structurally_weak = self._is_structurally_weak(close)

        if self.is_in_position:
            if regime == "BEAR":
                self._exit_position()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason="Regime protection: regime turned BEAR while holding; protective exit.",
                    price=close,
                )

            if is_structurally_weak and close > self.entry_price:
                self._exit_position()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason="Structural-weakness layer: coin profile is weak; taking the available gain.",
                    price=close,
                )

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

        if is_structurally_weak:
            return ClaudeDecision(
                action="WAIT",
                reason="Structural-weakness layer: coin is deeply below its own high with elevated BEAR-regime history.",
            )

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
                    f"{cycle_score:.2f} is below {MIN_CALENDAR_CYCLE_SCORE}."
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

    def _is_structurally_weak(self, close: Decimal) -> bool:
        ath = self._all_time_high.maximum
        bear_fraction = self._bear_fraction.fraction
        if ath is None or ath <= 0 or bear_fraction is None:
            return False
        drawdown_from_ath = (close / ath) - Decimal("1")
        return drawdown_from_ath < ATH_DRAWDOWN_BLOCK_THRESHOLD and bear_fraction > BEAR_FRACTION_BLOCK_THRESHOLD

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
                thesis=f"{symbol} cannot evaluate the triad confluence v3 without close and ATR data.",
                triggers=("Collect enough candle history before evaluating.",),
                invalidation=("No simulation without confirmed swing points and a full regime window.",),
                reasons=("Missing shortest-timeframe candle signals.",),
            )
        return self._decision(
            symbol=symbol,
            user_label=user_label,
            verdict="TRIAD V3 SCANNING",
            score=summary.score,
            risk_level="Medium",
            thesis=f"{symbol} is scanning for pattern, calendar, and regime agreement, filtered for structural strength.",
            triggers=(
                "Technical: a confirmed inverse head-and-shoulders or false-breakdown reclaim.",
                "Calendar: hour/day volume seasonality and Ghost Month avoidance score above the minimum.",
                "Cycle: Donchian regime must not be BEAR, and the coin must not be structurally weak.",
            ),
            invalidation=(
                "Exits immediately if the regime turns BEAR while holding.",
                "Takes any available gain immediately if the coin becomes structurally weak while holding.",
                f"Otherwise: {STOP_ATR_MULTIPLIER}xATR stop, {TRAIL_ATR_MULTIPLIER}xATR trail.",
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
