"""Claude Modified Martingale ATR.

A Martingale/DCA ladder derived from `claude_modified_martingale_rsi.py`,
with two structural changes:

1. **ATR-based safety-order spacing** instead of a fixed percentage step.
   Each safety order fires when price closes below
   `average_entry_price - (ATR(14) * atr_multiplier)` (default multiplier
   2.2, chosen after sweeping 2.0-3.0 on 4h/4yr backtest data across all 5
   watchlist coins) -- a volatility-adaptive distance instead of a flat
   2%. No RSI gate applies to safety orders in this version (unlike the
   RSI sibling, which requires RSI<50 on every level); only the very
   first entry of a cycle requires RSI(14) < 50.

2. **Shorter, steeper ladder.** 1 Base Order + 6 Safety Orders (7 fills
   total), with a custom non-linear lot sequence: 1, 1, 2, 2, 4, 4, 8
   (doubling every two layers instead of the RSI sibling's 28-level linear
   1,1,2,2,...,14,14 ramp).

Everything else carries over unchanged: a single global take-profit at
+2.5% above the position's average cost (closes the entire basket and
resets), no stop loss, and a bull-capture re-entry rule -- but here it is
keyed off RSI, not candle color: if a cycle closes in profit *and* RSI(14)
is still below 50 at that same exit, the next tick fires a fresh Base
Order immediately rather than waiting to re-check RSI. (Because this
project's backtest/live tick interface returns one decision per candle, a
"close and immediately re-buy on the same tick" is represented as: check
RSI once at the exit tick, latch the result, and act on it unconditionally
on the very next tick -- so a later RSI uptick between those two ticks
can't cancel a re-entry that was already earned at the moment of exit.)

Naming note: the request that shaped this file calls the position's
average cost a "VWAP" (volume-weighted average price). It is not a true
volume-weighted market indicator (this project already has one of those:
`RollingVwap` in `claude_common.py`, which weights by traded volume). What
a DCA bot actually tracks is the *cost basis* -- total dollars invested
divided by total units held -- so that is what this module computes and
calls `average_price`, matching the RSI sibling's terminology.

This module provides the same two-tier structure as the RSI sibling:

1. `ModifiedMartingaleATR` -- a standalone, pandas-based reference
   implementation (RSI and ATR both vectorized) for a Backtrader/CCXT/
   plain-pandas backtest. Pandas is imported lazily so this module stays
   importable without pandas installed.

2. `ClaudeModifiedMartingaleATR` -- the same rules adapted to this
   project's Decimal-based `on_candle_tick`/`evaluate`/`reset` interface,
   reusing the shared `RunningRsi` and `WilderAtr` helpers, registered so
   it runs through `app/backtesting.py` and the live dashboard like every
   other `claude_*` strategy.

Capital sizing follows the same pattern as the RSI sibling: each instance
derives its own base lot cost from whatever `total_capital_usd` it's
given (default $2,000), targeting ~99.75% deployment if all 7 layers
fire, instead of assuming a fixed pool.

Dry-run only, spot/long-only. It must not execute live orders, call
exchange clients, or access Binance order endpoints.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.claude_common import ClaudeDecision, RunningRsi, WilderAtr
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


TOTAL_CAPITAL_POOL = Decimal("2000")
MAX_SAFETY_ORDERS = 6
LOT_SEQUENCE = (1, 1, 2, 2, 4, 4, 8)  # Layer 0 (Base Order) through Layer 6 (6th Safety Order)
TOTAL_LAYERS = len(LOT_SEQUENCE)
# Fraction of any given capital pool the ladder targets deploying if every
# layer fires, matching the RSI sibling's small rounding-safety buffer.
CAPITAL_UTILIZATION_RATIO = Decimal("1995") / Decimal("2000")
RSI_PERIOD = 14
RSI_ENTRY_MAX = Decimal("50")
ATR_PERIOD = 14
# Chosen after sweeping 2.0-3.0 on 4h/4yr backtest data across all 5
# watchlist coins ($1,000/coin): 2.2 gave the highest total return
# (+102.42% vs 2.0's +95.42%), though the surrounding points (2.1, 2.3)
# were both lower (~75-79%), so this is a single-point improvement within
# a noisy region rather than a confirmed smooth optimum -- picked
# deliberately after that tradeoff was made explicit.
ATR_MULTIPLIER = Decimal("2.2")
TAKE_PROFIT_PERCENT = Decimal("2.5")

assert sum(LOT_SEQUENCE) == 22


class ModifiedMartingaleATR:
    """Standalone, exchange-agnostic reference implementation.

    Feed it bars one at a time via `on_bar` (Backtrader `next()` loop or a
    CCXT live loop calling once per newly closed candle), or hand it a
    full OHLC history via `run(df)` for a plain pandas backtest.
    """

    TOTAL_CAPITAL_USD = float(TOTAL_CAPITAL_POOL)
    LOT_SEQUENCE = LOT_SEQUENCE
    RSI_PERIOD = RSI_PERIOD
    ATR_PERIOD = ATR_PERIOD

    def __init__(
        self,
        total_capital_usd: float = TOTAL_CAPITAL_USD,
        rsi_period: int = RSI_PERIOD,
        rsi_entry_threshold: float = float(RSI_ENTRY_MAX),
        atr_period: int = ATR_PERIOD,
        atr_multiplier: float = float(ATR_MULTIPLIER),
        take_profit_percent: float = float(TAKE_PROFIT_PERCENT),
    ) -> None:
        self.total_capital_usd = total_capital_usd
        self.rsi_period = rsi_period
        self.rsi_entry_threshold = rsi_entry_threshold
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.take_profit_percent = take_profit_percent
        self.base_lot_cost_usd = (total_capital_usd * float(CAPITAL_UTILIZATION_RATIO)) / sum(self.LOT_SEQUENCE)
        self.reset()

    def reset(self) -> None:
        self.layer = 0
        self.total_invested = 0.0
        self.total_units_held = 0.0
        self.average_price: float | None = None
        self.trade_log: list[dict[str, Any]] = []
        self._awaiting_bull_reentry = False

    @staticmethod
    def calculate_rsi(closes: Any, period: int = RSI_PERIOD) -> Any:
        """Wilder's RSI over a pandas Series of closes, vectorized."""

        import pandas as pd  # lazy import: keep this module importable without pandas installed

        delta = closes.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        relative_strength = avg_gain / avg_loss.replace(0, pd.NA)
        rsi = 100 - (100 / (1 + relative_strength))
        return rsi.fillna(100)

    @staticmethod
    def calculate_atr(high: Any, low: Any, close: Any, period: int = ATR_PERIOD) -> Any:
        """Wilder's ATR over pandas Series of high/low/close, vectorized."""

        import pandas as pd

        previous_close = close.shift(1)
        true_range = pd.concat(
            [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
        ).max(axis=1)
        return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    def on_bar(self, *, timestamp: Any, close: float, rsi: float, atr: float) -> dict[str, Any] | None:
        """Process one already-closed bar. Returns a trade record, or None."""

        if self._awaiting_bull_reentry:
            self._awaiting_bull_reentry = False
            return self._buy(
                timestamp=timestamp, close=close, rsi=rsi, context="Bull-capture re-entry (RSI<50 at last exit). "
            )

        if self.layer > 0:
            take_profit_price = self.average_price * (1 + self.take_profit_percent / 100)
            if close >= take_profit_price:
                return self._liquidate_basket(timestamp=timestamp, close=close, rsi=rsi)

            if self.layer >= TOTAL_LAYERS:
                return None  # Ladder exhausted; hold for take-profit only, no stop loss.

            trigger_price = self.average_price - (atr * self.atr_multiplier)
            if close > trigger_price:
                return None
            return self._buy(timestamp=timestamp, close=close, rsi=rsi)

        if rsi >= self.rsi_entry_threshold:
            return None
        return self._buy(timestamp=timestamp, close=close, rsi=rsi)

    def _buy(self, *, timestamp: Any, close: float, rsi: float, context: str = "") -> dict[str, Any]:
        lots = self.LOT_SEQUENCE[self.layer]
        cost = lots * self.base_lot_cost_usd
        units_bought = cost / close
        self.layer += 1
        self.total_invested += cost
        self.total_units_held += units_bought
        self.average_price = self.total_invested / self.total_units_held
        trade = {
            "timestamp": timestamp,
            "action": "BUY",
            "layer": self.layer,
            "price": close,
            "rsi": rsi,
            "lots": lots,
            "cost": cost,
            "total_invested": self.total_invested,
            "average_price": self.average_price,
            "take_profit_target": self.average_price * (1 + self.take_profit_percent / 100),
            "note": context,
        }
        self.trade_log.append(trade)
        return trade

    def _liquidate_basket(self, *, timestamp: Any, close: float, rsi: float) -> dict[str, Any]:
        proceeds = self.total_units_held * close
        profit = proceeds - self.total_invested
        trade = {
            "timestamp": timestamp,
            "action": "SELL_BASKET",
            "layer": self.layer,
            "price": close,
            "total_invested": self.total_invested,
            "proceeds": proceeds,
            "profit": profit,
            "average_price": self.average_price,
        }
        self.trade_log.append(trade)
        self.reset()
        if rsi < self.rsi_entry_threshold:
            self._awaiting_bull_reentry = True
        return trade

    def run(self, df: Any) -> Any:
        """Run across a full OHLC history. `df` needs 'high', 'low', and 'close' columns."""

        import pandas as pd

        df = df.copy()
        df["rsi"] = self.calculate_rsi(df["close"], period=self.rsi_period)
        df["atr"] = self.calculate_atr(df["high"], df["low"], df["close"], period=self.atr_period)
        for timestamp, row in df.iterrows():
            if pd.isna(row["rsi"]) or pd.isna(row["atr"]):
                continue
            self.on_bar(timestamp=timestamp, close=float(row["close"]), rsi=float(row["rsi"]), atr=float(row["atr"]))
        return pd.DataFrame(self.trade_log)


class ClaudeModifiedMartingaleATR:
    definition = StrategyDefinition(
        slug="claude_modified_martingale_atr",
        name="Claude Modified Martingale ATR",
        style="dry-run: 7-layer (1 BO + 6 SO) Martingale/DCA, ATR-spaced safety orders, RSI bull-capture",
        description=(
            "Buys a Base Order only when RSI(14) is below 50. Adds up to 6 "
            "Safety Orders (lot sizing 1,1,2,2,4,4,8 across 7 layers, "
            "scaled to whatever capital pool it's given) each time price "
            "closes below the running average entry price minus ATR(14) x "
            "atr_multiplier (default 2.2) -- a volatility-adaptive step "
            "instead of a fixed percentage, and with no RSI gate on the "
            "safety orders themselves. Liquidates the entire basket at "
            "+2.5% above the average entry price. Bull-capture: if RSI(14) "
            "is still below 50 at the moment of that exit, immediately "
            "fires a new Base Order on the next tick instead of waiting. "
            "No stop loss; the 7-layer ladder is the only downside control."
        ),
    )

    def __init__(
        self,
        total_capital_usd: Decimal = TOTAL_CAPITAL_POOL,
        rsi_period: int = RSI_PERIOD,
        rsi_entry_max: Decimal = RSI_ENTRY_MAX,
        atr_period: int = ATR_PERIOD,
        atr_multiplier: Decimal = ATR_MULTIPLIER,
        take_profit_percent: Decimal = TAKE_PROFIT_PERCENT,
    ) -> None:
        self.total_capital_usd = Decimal(total_capital_usd)
        self.rsi_period = rsi_period
        self.rsi_entry_max = Decimal(rsi_entry_max)
        self.atr_period = atr_period
        self.atr_multiplier = Decimal(atr_multiplier)
        self.take_profit_percent = Decimal(take_profit_percent)
        self.base_lot_cost = (self.total_capital_usd * CAPITAL_UTILIZATION_RATIO) / Decimal(sum(LOT_SEQUENCE))
        self.reset()

    def reset(self) -> None:
        self._rsi = RunningRsi(self.rsi_period)
        self._atr = WilderAtr(self.atr_period)
        self.layer = 0
        self.total_invested = Decimal("0")
        self.total_base_held = Decimal("0")
        self.average_price = Decimal("0")
        self._awaiting_bull_reentry = False

    def on_candle_tick(self, candle: dict[str, Decimal]) -> ClaudeDecision:
        high = candle["high"]
        low = candle["low"]
        close = candle["close"]
        rsi = self._rsi.update(close)
        atr = self._atr.update(high, low, close)
        if rsi is None or atr is None:
            return ClaudeDecision(action="WAIT", reason=f"Warming up {self.rsi_period}-period RSI and ATR.")

        if self._awaiting_bull_reentry:
            self._awaiting_bull_reentry = False
            return self._buy(
                close, rsi, context="Bull-capture re-entry (RSI was below entry threshold at last exit). "
            )

        if self.layer > 0:
            take_profit_price = self.average_price * (Decimal("1") + self.take_profit_percent / Decimal("100"))
            if close >= take_profit_price:
                reason = (
                    f"Basket take-profit: close {close} >= TP {take_profit_price:.6f} "
                    f"(avg entry {self.average_price:.6f}, layer {self.layer})."
                )
                bull_capture_armed = rsi < self.rsi_entry_max
                self._reset_position()
                if bull_capture_armed:
                    self._awaiting_bull_reentry = True
                    reason += f" RSI {rsi:.1f} < {self.rsi_entry_max}: bull-capture armed for next tick."
                return ClaudeDecision(action="SIMULATED_SELL", reason=reason, price=close)

            if self.layer >= TOTAL_LAYERS:
                return ClaudeDecision(
                    action="HOLD",
                    reason=f"All {TOTAL_LAYERS} layers (1 BO + {MAX_SAFETY_ORDERS} SO) are filled; holding for take-profit only.",
                )

            trigger_price = self.average_price - (atr * self.atr_multiplier)
            if close > trigger_price:
                return ClaudeDecision(
                    action="HOLD",
                    reason=(
                        f"Layer {self.layer}/{TOTAL_LAYERS - 1} SO; waiting for close <= avg entry "
                        f"({self.average_price:.6f}) - {self.atr_multiplier}xATR ({atr:.6f}) = {trigger_price:.6f}."
                    ),
                )
            return self._buy(close, rsi)

        if rsi >= self.rsi_entry_max:
            return ClaudeDecision(action="WAIT", reason=f"Idle; RSI {rsi:.1f} >= {self.rsi_entry_max}, no entry.")
        return self._buy(close, rsi)

    def _buy(self, close: Decimal, rsi: Decimal, *, context: str = "") -> ClaudeDecision:
        lots = LOT_SEQUENCE[self.layer]
        lot_cost = lots * self.base_lot_cost
        remaining_capital = self.total_capital_usd - self.total_invested
        if remaining_capital <= 0:
            return ClaudeDecision(action="HOLD", reason="Capital pool exhausted; holding for take-profit only.")
        fraction = lot_cost / remaining_capital
        if fraction > 1:
            fraction = Decimal("1")
        layer_label = "BO" if self.layer == 0 else f"SO{self.layer}"
        self.layer += 1
        return ClaudeDecision(
            action="SIMULATED_BUY",
            reason=(
                f"{context}Layer {self.layer}/{TOTAL_LAYERS} ({layer_label}): buying {lots} lot(s) "
                f"(${lot_cost:.4f}) at RSI {rsi:.1f}."
            ),
            price=close,
            fraction=fraction,
        )

    def record_fill(self, *, usdt_spent: Decimal, base_bought: Decimal) -> None:
        self.total_invested += usdt_spent
        self.total_base_held += base_bought
        if self.total_base_held > 0:
            self.average_price = self.total_invested / self.total_base_held

    def _reset_position(self) -> None:
        self.layer = 0
        self.total_invested = Decimal("0")
        self.total_base_held = Decimal("0")
        self.average_price = Decimal("0")

    def evaluate(
        self,
        *,
        symbol: str,
        summary: MultiTimeframeSignalSummary,
        guides_by_interval: dict[str, TechnicalSignalGuide],
        user_label: str,
    ) -> StrategyDecision:
        guide = shortest_timeframe_guide(guides_by_interval)
        if guide is None or guide.current_price is None or guide.rsi14 is None or guide.atr14 is None:
            return self._decision(
                symbol=symbol,
                user_label=user_label,
                verdict="BLOCKED_BY_RISK",
                score=0,
                risk_level="High",
                thesis=f"{symbol} cannot evaluate the ATR Martingale ladder without RSI and ATR data.",
                triggers=("Collect enough candle history for RSI(14) and ATR(14).",),
                invalidation=("No simulation without confirmed RSI and ATR readings.",),
                reasons=("Missing RSI, ATR, or current price.",),
            )
        ready = guide.rsi14 < self.rsi_entry_max
        verdict = "MARTINGALE ATR ENTRY READY" if ready else "MARTINGALE ATR WAITING"
        thesis = (
            f"{symbol} RSI is {guide.rsi14:.1f}, "
            + (
                f"below {self.rsi_entry_max} -- a Base Order is eligible."
                if ready
                else f"at or above {self.rsi_entry_max} -- idle."
            )
        )
        return self._decision(
            symbol=symbol,
            user_label=user_label,
            verdict=verdict,
            score=summary.score,
            risk_level="High",
            thesis=thesis,
            triggers=(
                f"Base Order: RSI below {self.rsi_entry_max} while idle.",
                f"Safety Order: price closes below (average entry price - {self.atr_multiplier}xATR14), "
                "no RSI gate on safety orders.",
                "Bull-capture: RSI still below entry threshold at the moment of a take-profit exit "
                "fires a fresh Base Order on the next tick.",
            ),
            invalidation=(
                f"Global take-profit: entire basket liquidates at +{self.take_profit_percent}% above the average entry price.",
                f"No stop loss; caps out at {TOTAL_LAYERS} layers (1 BO + {MAX_SAFETY_ORDERS} SO, "
                f"${self.total_capital_usd} capital pool).",
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
