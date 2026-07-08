"""Micyu Bear Accumulation Period.

Michael's long-horizon bear-market accumulator, implemented deterministically
for dry-run backtesting. Purpose: buy deep, confirmed discounts against a
long (365-day) reference high in nine widening tiers (-10% to -90%, bigger
buys deeper), so capital is saved for the biggest drops instead of spent
early. A 30-day time-backstop advances to the next tier even without a big
drop, so a slow multi-month bleed with no single sharp dip still gets
bought — but only once RSI14 is at or below 50, so the backstop still
requires some softening of momentum rather than firing on a completely
blind schedule. Each depth-triggered buy requires an oversold read (RSI or
lower Bollinger band) plus a panic confirmation (a volume surge or extreme
RSI) so it is buying real capitulation, not routine noise. There is no
technical sell condition and no stop-loss: the only exit is the average
entry price reaching 20x, at which point everything is sold and a fresh
cycle begins.

WARNING: this is a maximum-conviction, unbounded-risk design by explicit
request. There is no stop-loss and no partial profit-taking — capital
committed here can be a total loss if the asset fails before recovering.
It must not execute live orders, call exchange clients, or access Binance
order endpoints.

NOTE: the reference-high window (8760 candles) and time-backstop window
(720 candles) are tuned for 1-hour candles ("30 days" and "365 days" at 1h
resolution). Using another candle interval requires rescaling both windows
proportionally, the same way the other Claude strategies' 30-day windows
are rescaled for 15m/4h backtests.
"""

from __future__ import annotations

from decimal import Decimal

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.claude_common import (
    ClaudeDecision,
    RollingMax,
    RollingStats,
    RunningRsi,
    WilderAtr,
)
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


BOLLINGER_PERIOD = 20
BOLLINGER_STDDEV_MULTIPLIER = Decimal("2")
RSI_ENTRY_MAXIMUM = Decimal("40")
RSI_PANIC_OVERRIDE = Decimal("25")
BACKSTOP_RSI_MAXIMUM = Decimal("50")
ATRP_MINIMUM_PERCENT = Decimal("0.05")
VOLUME_WINDOW_CANDLES = 60
VOLUME_SURGE_MULTIPLIER = Decimal("1.2")

REFERENCE_HIGH_WINDOW_CANDLES = 8760  # 365 days at 1h resolution
TIME_BACKSTOP_CANDLES = 720  # 30 days at 1h resolution
SELL_MULTIPLE = Decimal("20")  # sell everything at 20x average entry, no other exit

# Nine depth tiers measured against the SAME 365-day reference high, all
# sizes as a percent of total cycle capital. Sizes grow with depth: shallow
# tiers are small (this is not yet a confirmed capitulation), the largest
# tiers sit at -50% to -90% where real bear-market bottoms tend to form.
DEPTH_TIERS_PERCENT = (10, 20, 30, 40, 50, 60, 70, 80, 90)
TIER_SIZES = (
    Decimal("0.05"),
    Decimal("0.07"),
    Decimal("0.10"),
    Decimal("0.13"),
    Decimal("0.15"),
    Decimal("0.15"),
    Decimal("0.15"),
    Decimal("0.10"),
    Decimal("0.10"),
)


def _fractions_of_remaining(sizes: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    """Convert cycle-capital tier sizes into fractions of the remaining cash."""

    fractions = []
    remaining = Decimal("1")
    for size in sizes:
        fractions.append(size / remaining if remaining > 0 else Decimal("0"))
        remaining -= size
    return tuple(fractions)


TIER_FRACTIONS_OF_REMAINING = _fractions_of_remaining(TIER_SIZES)


class MicyuBearAccumulationPeriod:
    definition = StrategyDefinition(
        slug="micyu_bear_accumulation_period",
        name="Micyu Bear Accumulation Period",
        style="dry-run long-horizon bear-market accumulator, 9-tier ladder, no sell until 20x",
        description=(
            "Buys nine widening tiers (5%-15% of capital, deepest largest) "
            "at -10% down to -90% below the 365-day high, confirmed by RSI "
            "or lower-band oversold plus a volume-surge or extreme-RSI "
            "panic check. A 30-day time-backstop advances to the next tier "
            "even without a sharp drop, as long as price is still >=10% "
            "below the high. No sell condition except average entry "
            "reaching 20x, at which point everything sells and a fresh "
            "cycle begins."
        ),
    )

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._atr = WilderAtr(14)
        self._rsi = RunningRsi(14)
        self._bollinger = RollingStats(BOLLINGER_PERIOD)
        self._volume_stats = RollingStats(VOLUME_WINDOW_CANDLES)
        self._reference_high = RollingMax(REFERENCE_HIGH_WINDOW_CANDLES)
        self._reset_cycle()

    def _reset_cycle(self) -> None:
        self.tiers_filled = 0
        self.total_usdt_spent = Decimal("0")
        self.total_base_bought = Decimal("0")
        self.average_entry_price = Decimal("0")
        self.candles_since_last_buy = TIME_BACKSTOP_CANDLES  # eligible immediately

    @property
    def is_in_position(self) -> bool:
        return self.tiers_filled > 0

    def on_candle_tick(self, candle: dict[str, Decimal]) -> ClaudeDecision:
        close = candle["close"]
        reference_high = self._reference_high.maximum if self._reference_high.is_full else None
        self._reference_high.push(candle["high"])
        atr = self._atr.update(candle["high"], candle["low"], close)
        rsi = self._rsi.update(close)
        self._bollinger.push(close)
        band_mean = self._bollinger.mean() if self._bollinger.is_full else None
        band_stddev = self._bollinger.stddev() if self._bollinger.is_full else None
        volume_mean = self._volume_stats.mean() if self._volume_stats.is_full else None
        self._volume_stats.push(candle["volume"])
        self.candles_since_last_buy += 1

        # The 20x exit depends only on close vs. average entry, never on any
        # indicator, so a position already at target can always sell even
        # while other indicators (or the 365-day reference high) are still
        # warming up after a restart.
        if self.is_in_position and close >= self.average_entry_price * SELL_MULTIPLE:
            self._reset_cycle()
            return ClaudeDecision(
                action="SIMULATED_SELL",
                reason=f"{SELL_MULTIPLE}x average entry reached; selling everything.",
                price=close,
            )

        if reference_high is None:
            return ClaudeDecision(action="WAIT", reason="Reference high still warming up (365 days of history).")
        if atr is None or atr <= 0 or rsi is None or band_mean is None or band_stddev is None or volume_mean is None:
            return ClaudeDecision(action="WAIT", reason="Warming up indicators.")

        if self.tiers_filled >= len(TIER_SIZES):
            return ClaudeDecision(action="HOLD", reason="Fully deployed across all nine tiers; awaiting 20x exit.")

        depth_percent = DEPTH_TIERS_PERCENT[self.tiers_filled]
        tier_price = reference_high * (Decimal("1") - Decimal(depth_percent) / Decimal("100"))
        depth_reached = close <= tier_price

        lower_band = band_mean - (band_stddev * BOLLINGER_STDDEV_MULTIPLIER)
        oversold = close <= lower_band or rsi <= RSI_ENTRY_MAXIMUM
        panic = candle["volume"] >= volume_mean * VOLUME_SURGE_MULTIPLIER or rsi <= RSI_PANIC_OVERRIDE
        atrp = (atr / close) * Decimal("100")
        volatility_ok = atrp >= ATRP_MINIMUM_PERCENT

        if depth_reached and oversold and panic and volatility_ok:
            return self._tier_buy(
                close,
                f"Tier {self.tiers_filled + 1}: -{depth_percent}% below the 365-day high with oversold and panic confirmation.",
            )

        backstop_floor = reference_high * (Decimal("1") - Decimal(DEPTH_TIERS_PERCENT[0]) / Decimal("100"))
        backstop_time_ready = self.candles_since_last_buy >= TIME_BACKSTOP_CANDLES and close <= backstop_floor
        if backstop_time_ready and rsi <= BACKSTOP_RSI_MAXIMUM:
            return self._tier_buy(
                close,
                f"Tier {self.tiers_filled + 1}: scheduled accumulation buy after "
                f"{TIME_BACKSTOP_CANDLES} candles with no purchase, still below the bear threshold "
                f"and RSI <= {BACKSTOP_RSI_MAXIMUM}.",
            )
        if backstop_time_ready:
            return ClaudeDecision(
                action="WAIT",
                reason=f"Time backstop eligible but RSI {rsi:.1f} is still above {BACKSTOP_RSI_MAXIMUM}; waiting for a softer oversold read.",
            )

        if not depth_reached:
            return ClaudeDecision(
                action="WAIT",
                reason=f"Price has not reached tier {self.tiers_filled + 1} (-{depth_percent}% below the 365-day high).",
            )
        return ClaudeDecision(action="WAIT", reason="Depth reached but no oversold/panic confirmation yet.")

    def _tier_buy(self, close: Decimal, reason: str) -> ClaudeDecision:
        fraction = TIER_FRACTIONS_OF_REMAINING[self.tiers_filled]
        self.tiers_filled += 1
        self.candles_since_last_buy = 0
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
                thesis=f"{symbol} cannot evaluate the bear accumulator without close and ATR data.",
                triggers=("Collect enough candle history before evaluating.",),
                invalidation=("No simulation without RSI, Bollinger, ATR, and a 365-day price history.",),
                reasons=("Missing shortest-timeframe candle signals.",),
            )
        return self._decision(
            symbol=symbol,
            user_label=user_label,
            verdict="BEAR ACCUMULATION SCANNING",
            score=summary.score,
            risk_level="Extreme",
            thesis=f"{symbol} bear accumulator is scanning for confirmed deep discounts against its 365-day high.",
            triggers=(
                "Nine tiers (5%-15% of capital, deepest largest) fire at -10% down to -90% below the 365-day high.",
                "Each depth buy needs RSI/lower-band oversold plus a volume-surge or extreme-RSI panic confirmation.",
                f"A scheduled buy fires every {TIME_BACKSTOP_CANDLES} candles with no purchase if still >=10% below "
                f"the high and RSI <= {BACKSTOP_RSI_MAXIMUM}.",
            ),
            invalidation=(
                f"No sell condition except average entry reaching {SELL_MULTIPLE}x; then everything sells and a new cycle begins.",
                "No stop-loss by design: this is a maximum-conviction, unbounded-risk accumulation bet.",
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
