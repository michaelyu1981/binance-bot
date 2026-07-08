"""Claude Dip Accumulator.

Michael's tiered dip-buying design, implemented deterministically for dry-run
backtesting. Entry: a Bollinger W-bottom — a first pierce of the 20/2 lower
band by 0.25xATR with RSI14 <= 22 arms the setup, and the buy fires only on
an up-close retest of that low that holds inside the band (selling pressure
exhausted). New ladders need a Donchian non-BEAR regime (BEAR = new-low
share >= 0.65, i.e. new highs must still be at least 35% of high/low
events), a dip at least 10% below the 30-day high, and calm bands (no
bandwidth blowout). Four further tiers (12.5% to 27.5%, deepest largest)
fill at widening drops of 3.5% to 6.5% below each previous fill, reaching
~-18.6% below tier 1 when fully deployed. Exit: only once price is at least
2xATR above average entry, hold
while EMA9 > EMA21 momentum persists and sell the close that loses it — or
take profit outright at +12% above average entry. No new cycle starts while
RSI is overheated (>= 70) or price sits at the upper band.

WARNING: this is an averaging-down ladder with no stop-loss by design. A
deep bear leg leaves it fully deployed and holding. It must not execute live
orders, call exchange clients, or access Binance order endpoints.
"""

from __future__ import annotations

from decimal import Decimal

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.claude_common import (
    ClaudeDecision,
    DonchianRegime,
    RollingMax,
    RollingStats,
    RunningEma,
    RunningRsi,
    WilderAtr,
)
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


BOLLINGER_PERIOD = 20
BOLLINGER_STDDEV_MULTIPLIER = Decimal("2")
RSI_ENTRY_MAXIMUM = Decimal("22")
RSI_OVERHEAT_MINIMUM = Decimal("70")
ATRP_MINIMUM_PERCENT = Decimal("0.05")
BAND_PIERCE_ATR_MULTIPLIER = Decimal("0.25")
W_RETEST_WINDOW_CANDLES = 60
W_RETEST_ATR_TOLERANCE = Decimal("0.5")
BANDWIDTH_WINDOW = 120
BANDWIDTH_VETO_MULTIPLIER = Decimal("2")
DEPTH_GATE_WINDOW = 720
DEPTH_GATE_MAX_OF_HIGH = Decimal("0.90")
DISASTER_STOP_MULTIPLIER = Decimal("0.90")
TAKE_PROFIT_MULTIPLIER = Decimal("1.12")
SELL_READY_ATR_MULTIPLIER = Decimal("2")
FAST_EMA_PERIOD = 9
SLOW_EMA_PERIOD = 21

# Five-tier ladder: percent of cycle capital per tier, and the drop below
# the previous fill that triggers each add. Tier 1 is a meaningful 20% so
# shallow winning cycles carry real capital; the deepest tier is the
# largest. Fully deployed ~-18.6% below the tier-1 price.
TIER_SIZES = (
    Decimal("0.20"),
    Decimal("0.125"),
    Decimal("0.175"),
    Decimal("0.225"),
    Decimal("0.275"),
)
TIER_DROP_MULTIPLIERS = (
    Decimal("0.965"),
    Decimal("0.955"),
    Decimal("0.945"),
    Decimal("0.935"),
)


def _fractions_of_remaining(sizes: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    """Convert cycle-capital tier sizes into fractions of the remaining cash."""

    fractions = []
    remaining = Decimal("1")
    for size in sizes:
        fractions.append(size / remaining)
        remaining -= size
    return tuple(fractions)


TIER_FRACTIONS_OF_REMAINING = _fractions_of_remaining(TIER_SIZES)


class ClaudeDipAccumulator:
    definition = StrategyDefinition(
        slug="claude_dip_accumulator",
        name="Claude Dip Accumulator",
        style="dry-run tiered dip buyer: Bollinger W-bottom entry, 5-tier ladder",
        description=(
            "Arms on a lower-band pierce with RSI<=22, buys 20% when the "
            "retest of that low holds inside the band, adds 4 widening tiers "
            "(12.5%-27.5%, deepest largest) down to ~-18.6% below entry, "
            "then exits on lost EMA9/21 momentum once 2xATR above average "
            "entry, or at +12% take profit. Bandwidth blowouts, BEAR "
            "regimes, and shallow (<10%) dips veto new ladders."
        ),
    )

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._atr = WilderAtr(14)
        self._rsi = RunningRsi(14)
        self._bollinger = RollingStats(BOLLINGER_PERIOD)
        self._fast_ema = RunningEma(FAST_EMA_PERIOD)
        self._slow_ema = RunningEma(SLOW_EMA_PERIOD)
        self._regime = DonchianRegime()
        self._high720 = RollingMax(DEPTH_GATE_WINDOW)
        self._bandwidth_stats = RollingStats(BANDWIDTH_WINDOW)
        self._previous_close: Decimal | None = None
        self._w_first_low: Decimal | None = None
        self._w_age = 0
        self._reset_cycle()

    def _reset_cycle(self) -> None:
        self.tiers_filled = 0
        self.last_fill_price = Decimal("0")
        self.total_usdt_spent = Decimal("0")
        self.total_base_bought = Decimal("0")
        self.average_entry_price = Decimal("0")
        self._w_first_low = None
        self._w_age = 0

    @property
    def is_in_position(self) -> bool:
        return self.tiers_filled > 0

    def on_candle_tick(self, candle: dict[str, Decimal]) -> ClaudeDecision:
        close = candle["close"]
        regime = self._regime.update(candle["high"], candle["low"])
        month_high = self._high720.maximum if self._high720.is_full else None
        self._high720.push(candle["high"])
        atr = self._atr.update(candle["high"], candle["low"], close)
        rsi = self._rsi.update(close)
        self._bollinger.push(close)
        band_mean = self._bollinger.mean() if self._bollinger.is_full else None
        band_stddev = self._bollinger.stddev() if self._bollinger.is_full else None
        fast = self._fast_ema.update(close)
        slow = self._slow_ema.update(close)

        if (
            atr is None
            or atr <= 0
            or rsi is None
            or band_mean is None
            or band_stddev is None
            or fast is None
            or slow is None
        ):
            return ClaudeDecision(action="WAIT", reason="Warming up indicators.")

        lower_band = band_mean - (band_stddev * BOLLINGER_STDDEV_MULTIPLIER)
        upper_band = band_mean + (band_stddev * BOLLINGER_STDDEV_MULTIPLIER)
        bandwidth = (upper_band - lower_band) / band_mean if band_mean > 0 else None
        if bandwidth is not None:
            self._bandwidth_stats.push(bandwidth)
        bandwidth_mean = self._bandwidth_stats.mean()
        previous_close = self._previous_close
        self._previous_close = close

        if self.is_in_position:
            if self.tiers_filled < len(TIER_SIZES):
                trigger = self.last_fill_price * TIER_DROP_MULTIPLIERS[self.tiers_filled - 1]
                if close <= trigger:
                    tier_number = self.tiers_filled + 1
                    size_percent = TIER_SIZES[self.tiers_filled] * 100
                    return self._tier_buy(
                        close,
                        f"Tier {tier_number}: ladder drop reached; adding {size_percent}% of cycle capital.",
                    )
            elif close <= self.last_fill_price * DISASTER_STOP_MULTIPLIER:
                self._reset_cycle()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason="Disaster stop: 10% below the final ladder tier; bounding the loss.",
                    price=close,
                )

            if close >= self.average_entry_price * TAKE_PROFIT_MULTIPLIER:
                self._reset_cycle()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason=f"Take profit: +{(TAKE_PROFIT_MULTIPLIER - 1) * 100:.0f}% above average entry.",
                    price=close,
                )
            sell_ready = close >= self.average_entry_price + (atr * SELL_READY_ATR_MULTIPLIER)
            if sell_ready:
                if fast > slow:
                    return ClaudeDecision(
                        action="HOLD",
                        reason=f"{SELL_READY_ATR_MULTIPLIER}xATR above average entry but EMA momentum continues; not selling yet.",
                    )
                self._reset_cycle()
                return ClaudeDecision(
                    action="SIMULATED_SELL",
                    reason=f"{SELL_READY_ATR_MULTIPLIER}xATR above average entry and EMA9/21 momentum is gone.",
                    price=close,
                )
            return ClaudeDecision(action="HOLD", reason="Ladder active; waiting on recovery.")

        if self._w_first_low is not None:
            self._w_age += 1
            if self._w_age > W_RETEST_WINDOW_CANDLES or close > band_mean:
                self._w_first_low = None
                self._w_age = 0

        if regime is None:
            return ClaudeDecision(action="WAIT", reason="Donchian regime still warming up; no new ladders.")
        if regime == "BEAR":
            self._w_first_low = None
            self._w_age = 0
            return ClaudeDecision(
                action="WAIT",
                reason="Donchian regime is BEAR (new lows outpace new highs); no new ladders.",
            )
        if rsi >= RSI_OVERHEAT_MINIMUM or close >= upper_band:
            return ClaudeDecision(action="WAIT", reason="Overheated: RSI high or at upper band; no trade.")
        atrp = (atr / close) * Decimal("100")
        if atrp < ATRP_MINIMUM_PERCENT:
            return ClaudeDecision(action="WAIT", reason="Volatility below fee-aware minimum.")
        if month_high is None:
            return ClaudeDecision(action="WAIT", reason="Depth gate warming up; no new ladders.")
        if close > month_high * DEPTH_GATE_MAX_OF_HIGH:
            return ClaudeDecision(
                action="WAIT",
                reason="Depth gate: dip is under 10% below the 30-day high; shallow dips become bags.",
            )
        if bandwidth is not None and bandwidth_mean is not None and bandwidth > bandwidth_mean * BANDWIDTH_VETO_MULTIPLIER:
            return ClaudeDecision(
                action="WAIT",
                reason="Bandwidth blowout: bands expanding violently; crash in progress, not a stretched dip.",
            )

        pierce_line = lower_band - (atr * BAND_PIERCE_ATR_MULTIPLIER)
        if self._w_first_low is None:
            if close <= pierce_line and rsi <= RSI_ENTRY_MAXIMUM:
                self._w_first_low = candle["low"]
                self._w_age = 0
                return ClaudeDecision(
                    action="WAIT",
                    reason="W-bottom armed: first low marked; waiting for a retest that holds inside the band.",
                )
            return ClaudeDecision(action="WAIT", reason="No deep oversold pierce of the lower band.")

        if candle["low"] < self._w_first_low:
            self._w_first_low = candle["low"]
        if (
            self._w_age >= 3
            and candle["low"] <= self._w_first_low + (atr * W_RETEST_ATR_TOLERANCE)
            and close >= lower_band
            and previous_close is not None
            and close > previous_close
        ):
            self._w_first_low = None
            self._w_age = 0
            return self._tier_buy(close, "Tier 1: W-bottom retest held inside the band; buying 5%.")
        return ClaudeDecision(action="WAIT", reason="W-bottom armed; waiting for the retest to hold inside the band.")

    def _tier_buy(self, close: Decimal, reason: str) -> ClaudeDecision:
        fraction = TIER_FRACTIONS_OF_REMAINING[self.tiers_filled]
        self.tiers_filled += 1
        self.last_fill_price = close
        return ClaudeDecision(
            action="SIMULATED_BUY",
            reason=reason,
            price=close,
            fraction=fraction,
        )

    def record_fill(self, *, usdt_spent: Decimal, base_bought: Decimal) -> None:
        """Called by the backtest harness so average entry reflects fees."""

        self.total_usdt_spent += usdt_spent
        self.total_base_bought += base_bought
        if self.total_base_bought > 0:
            self.average_entry_price = self.total_usdt_spent / self.total_base_bought

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
                thesis=f"{symbol} cannot evaluate the dip accumulator without close and ATR data.",
                triggers=("Collect enough candle history before evaluating.",),
                invalidation=("No simulation without Bollinger, RSI, EMA, and ATR history.",),
                reasons=("Missing shortest-timeframe candle signals.",),
            )
        return self._decision(
            symbol=symbol,
            user_label=user_label,
            verdict="DIP SCANNING",
            score=summary.score,
            risk_level="High",
            thesis=f"{symbol} dip accumulator is scanning for oversold lower-band dips.",
            triggers=(
                f"A pierce of the lower band by {BAND_PIERCE_ATR_MULTIPLIER}xATR with RSI <= {RSI_ENTRY_MAXIMUM} arms a W-bottom.",
                "Tier 1 buys 20% when the retest of that low holds inside the band on an up-close.",
                "Four more tiers (12.5%-27.5%, deepest largest) fill at widening 3.5%-6.5% drops to ~-18.6%.",
                f"Sell at +12%, or once {SELL_READY_ATR_MULTIPLIER}xATR above average entry when EMA momentum ends.",
            ),
            invalidation=(
                "No new cycle while the Donchian regime is BEAR (new lows outpacing new highs).",
                f"No new cycle while RSI >= {RSI_OVERHEAT_MINIMUM} or price is at the upper band.",
                "No stop-loss by design: a deep bear leg holds fully deployed.",
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
