"""Claude Modified Martingale RSI.

A 28-level, linear-lot Martingale/DCA ladder gated by a 4H RSI(14) < 50
guardrail on every entry (initial and safety levels), with a single global
take-profit (default +4.5%, tuned from the original spec's 2.5% -- see
TAKE_PROFIT_PERCENT below) that liquidates the entire basket. No stop
loss -- the 28-level ladder is the strategy's only downside control,
exactly as specified.

Bull-capture re-entry: after a take-profit exit, the very next candle is
checked once. If it closes green with a body at least
`bull_reentry_min_body_percent` of price (default 0.3%), the strategy
re-enters immediately (a fresh Level 1 buy) to keep riding a still-strong
uptrend instead of idling. If that next candle is red, or green but too
small, the strategy falls back to the normal behavior: wait for RSI to
drop back below the entry threshold. Set
`bull_reentry_min_body_percent=None` to disable this check entirely.

This module provides two things:

1. `ModifiedMartingaleDCA` -- a standalone, exchange-agnostic reference
   implementation using pandas for RSI, suitable for lifting into a
   Backtrader `Strategy.next()` loop, a CCXT-driven live loop (call
   `on_bar` once per newly closed 4H candle), or a plain pandas backtest
   via `run(df)`. Pandas is imported lazily inside the methods that need
   it, so importing this module does not require pandas to be installed
   (this project has zero runtime dependencies otherwise).

2. `ClaudeModifiedMartingaleRSI` -- the same trading rules adapted to this
   project's existing Decimal-based, O(1)-per-tick backtest/dashboard
   interface (`on_candle_tick`, `evaluate`, `reset`), reusing the shared
   `RunningRsi` helper instead of pandas so it runs through
   `app/backtesting.py` and the live dashboard like every other
   `claude_*` strategy.

Both share the same constants (`TOTAL_CAPITAL_POOL`, `LOT_SEQUENCE`,
`CAPITAL_UTILIZATION_RATIO`, RSI/step/take-profit thresholds) so their
math stays in sync.

Capital sizing scales to whatever pool you actually give it: each
instance derives its own base lot cost as
`(total_capital_usd * CAPITAL_UTILIZATION_RATIO) / 210` (210 being the
sum of the 28-level lot sequence), instead of assuming a fixed $2,000
pool. At the spec's original $2,000 this works out to exactly $9.50/lot;
at any other pool size, e.g. $1,000, it's $4.75/lot -- same 1,1,2,2,...
progression, correctly proportioned. `run_backtests(...)` passes its
`starting_usdt` straight through to this class's constructor, so
whatever capital you backtest with is the capital the ladder actually
targets, and 28 filled levels always deploys ~99.75% of it (the
remaining 0.25% is the same small safety buffer the original $9.50/$2,000
spec left unspent, not a bug).

Dry-run only, spot/long-only. It must not execute live orders, call
exchange clients, or access Binance order endpoints.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide
from app.strategies.claude_common import ClaudeDecision, RunningRsi
from app.strategies.helpers import shortest_timeframe_guide, summary_reasons
from app.strategies.types import StrategyDecision, StrategyDefinition


TOTAL_CAPITAL_POOL = Decimal("2000")
MAX_LEVELS = 28
BASE_LOT_COST = Decimal("9.50")
# Fraction of any given capital pool the 28-level ladder targets deploying if
# every level fires. Derived from the original spec (9.50 * 210 = 1995 of a
# 2000 pool) and held constant so a $1,000 backtest deploys ~$997.50, a
# $2,000 backtest ~$1,995, etc. -- always "almost all", never all of it, by
# design (a small buffer against rounding).
CAPITAL_UTILIZATION_RATIO = Decimal("1995") / Decimal("2000")
RSI_PERIOD = 14
RSI_ENTRY_MAX = Decimal("50")
STEP_DROP_PERCENT = Decimal("2.0")
# Raised from the original spec's 2.5% after a TP sweep across 0.5%-6.5% on
# 4h/4yr backtest data (all 5 watchlist coins): the 3.5%-5.0% zone averaged
# ~20 points higher total return than 2.5%, though no single value in that
# zone is individually reliable (this ladder frequently ends a backtest
# window still holding an open position, so results are sensitive to
# exactly which candle the window happens to end on). 4.5% was chosen as a
# middle-of-the-zone value rather than the single highest (noisiest) point.
TAKE_PROFIT_PERCENT = Decimal("4.5")
# After a take-profit exit, if the very next candle closes green with a body
# at least this % of price, treat it as bullish continuation and re-enter
# immediately (bypassing the RSI<50 idle gate) instead of waiting for RSI to
# fall back below the entry threshold. Set to None to disable this check
# entirely and always fall back to the standard RSI<50 idle wait.
BULL_REENTRY_MIN_BODY_PERCENT = Decimal("0.3")


def _build_lot_sequence(max_levels: int) -> tuple[int, ...]:
    """1, 1, 2, 2, 3, 3, ... -- each increment repeated exactly twice."""

    sequence: list[int] = []
    unit = 1
    while len(sequence) < max_levels:
        sequence.append(unit)
        sequence.append(unit)
        unit += 1
    return tuple(sequence[:max_levels])


LOT_SEQUENCE = _build_lot_sequence(MAX_LEVELS)
assert sum(LOT_SEQUENCE) == 210
assert sum(LOT_SEQUENCE) * BASE_LOT_COST == Decimal("1995.00")


def compute_validation_table(starting_price: Decimal = Decimal("100")) -> list[dict[str, Decimal | int]]:
    """Simulate all 28 levels firing back-to-back, ignoring the RSI gate.

    Level 1 buys at `starting_price`. Every following level's asset price is
    exactly STEP_DROP_PERCENT below the *previous level's break-even price*
    -- the same trigger rule `ClaudeModifiedMartingaleRSI` and
    `ModifiedMartingaleDCA` use live -- so this table is a faithful math
    validation of the real trigger/sizing logic, not just a flat 2% per-step
    decay of the raw price.
    """

    rows: list[dict[str, Decimal | int]] = []
    total_invested = Decimal("0")
    total_units_held = Decimal("0")
    breakeven = starting_price
    asset_price = starting_price

    for level in range(1, MAX_LEVELS + 1):
        if level == 1:
            price_drop_percent = Decimal("0")
            asset_price = starting_price
        else:
            price_drop_percent = -STEP_DROP_PERCENT
            asset_price = breakeven * (Decimal("1") - STEP_DROP_PERCENT / Decimal("100"))

        lots = LOT_SEQUENCE[level - 1]
        cost = lots * BASE_LOT_COST
        units_bought = cost / asset_price
        total_invested += cost
        total_units_held += units_bought
        breakeven = total_invested / total_units_held
        take_profit_target = breakeven * (Decimal("1") + TAKE_PROFIT_PERCENT / Decimal("100"))

        rows.append(
            {
                "level": level,
                "price_drop_percent": price_drop_percent,
                "asset_price": asset_price,
                "lots_purchased": lots,
                "cost_of_purchase": cost,
                "total_lots_held": sum(LOT_SEQUENCE[:level]),
                "total_invested": total_invested,
                "breakeven_price": breakeven,
                "take_profit_target": take_profit_target,
            }
        )

    return rows


def format_validation_table_markdown(rows: list[dict[str, Decimal | int]]) -> str:
    header = (
        "| Level | Price Drop % | Asset Price | Lots Purchased | Cost of Purchase "
        f"| Total Lots Held | Total Invested | Break-even Price | +{TAKE_PROFIT_PERCENT}% TP Target |"
    )
    divider = "|---|---|---|---|---|---|---|---|---|"
    lines = [header, divider]
    for row in rows:
        lines.append(
            f"| {row['level']} "
            f"| {row['price_drop_percent']:.2f}% "
            f"| ${row['asset_price']:.4f} "
            f"| {row['lots_purchased']} "
            f"| ${row['cost_of_purchase']:.2f} "
            f"| {row['total_lots_held']} "
            f"| ${row['total_invested']:.2f} "
            f"| ${row['breakeven_price']:.4f} "
            f"| ${row['take_profit_target']:.4f} |"
        )
    return "\n".join(lines)


class ModifiedMartingaleDCA:
    """Standalone, exchange-agnostic reference implementation.

    Feed it bars one at a time via `on_bar` (Backtrader `next()` loop or a
    CCXT live loop calling once per newly closed 4H candle), or hand it a
    full OHLC history via `run(df)` for a plain pandas backtest.
    """

    TOTAL_CAPITAL_USD = float(TOTAL_CAPITAL_POOL)
    MAX_LEVELS = MAX_LEVELS
    LOT_SEQUENCE = tuple(int(lots) for lots in LOT_SEQUENCE)
    RSI_PERIOD = RSI_PERIOD
    RSI_ENTRY_THRESHOLD = float(RSI_ENTRY_MAX)
    STEP_DROP_PERCENT = float(STEP_DROP_PERCENT)
    TAKE_PROFIT_PERCENT = float(TAKE_PROFIT_PERCENT)

    def __init__(
        self,
        total_capital_usd: float = TOTAL_CAPITAL_USD,
        max_levels: int = MAX_LEVELS,
        rsi_period: int = RSI_PERIOD,
        rsi_entry_threshold: float = float(RSI_ENTRY_MAX),
        step_drop_percent: float = float(STEP_DROP_PERCENT),
        take_profit_percent: float = float(TAKE_PROFIT_PERCENT),
        bull_reentry_min_body_percent: float | None = float(BULL_REENTRY_MIN_BODY_PERCENT),
    ) -> None:
        self.total_capital_usd = total_capital_usd
        self.max_levels = max_levels
        self.lot_sequence = tuple(int(lots) for lots in _build_lot_sequence(max_levels))
        self.rsi_period = rsi_period
        self.rsi_entry_threshold = rsi_entry_threshold
        self.step_drop_percent = step_drop_percent
        self.take_profit_percent = take_profit_percent
        self.bull_reentry_min_body_percent = bull_reentry_min_body_percent
        self.base_lot_cost_usd = (total_capital_usd * float(CAPITAL_UTILIZATION_RATIO)) / sum(self.lot_sequence)
        self.reset()

    def reset(self) -> None:
        self.level = 0
        self.total_invested = 0.0
        self.total_units_held = 0.0
        self.average_price: float | None = None
        self.trade_log: list[dict[str, Any]] = []
        self._awaiting_bull_reentry_check = False

    @staticmethod
    def calculate_rsi(closes: Any, period: int = RSI_PERIOD) -> Any:
        """Wilder's RSI over a pandas Series of closes, vectorized.

        Uses the same recurrence as Wilder's original formula (equivalent
        to an EWMA with alpha = 1/period), matching this project's live
        `RunningRsi` helper tick-for-tick after warmup.
        """

        import pandas as pd  # lazy import: keep this module importable without pandas installed

        delta = closes.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        relative_strength = avg_gain / avg_loss.replace(0, pd.NA)
        rsi = 100 - (100 / (1 + relative_strength))
        return rsi.fillna(100)

    def on_bar(self, *, timestamp: Any, open_price: float, close: float, rsi: float) -> dict[str, Any] | None:
        """Process one already-closed 4H bar. Returns a trade record, or None."""

        if self._awaiting_bull_reentry_check:
            self._awaiting_bull_reentry_check = False
            if self.bull_reentry_min_body_percent is not None and close > 0:
                body_percent = ((close - open_price) / close) * 100
                if close > open_price and body_percent >= self.bull_reentry_min_body_percent:
                    return self._buy(
                        timestamp=timestamp,
                        close=close,
                        rsi=rsi,
                        context="Bull-capture re-entry (strong green follow-through after TP exit). ",
                    )
            # Not a strong green follow-through -- fall through to the normal idle/RSI check below.

        if self.level > 0:
            take_profit_price = self.average_price * (1 + self.take_profit_percent / 100)
            if close >= take_profit_price:
                self._awaiting_bull_reentry_check = True
                return self._liquidate_basket(timestamp=timestamp, close=close)

            if self.level >= self.max_levels:
                return None  # Ladder exhausted; hold for take-profit only, no stop loss.

            trigger_price = self.average_price * (1 - self.step_drop_percent / 100)
            if close > trigger_price:
                return None
            if rsi >= self.rsi_entry_threshold:
                return None  # 2% drop hit but RSI too high; re-check next bar.
            return self._buy(timestamp=timestamp, close=close, rsi=rsi)

        if rsi >= self.rsi_entry_threshold:
            return None
        return self._buy(timestamp=timestamp, close=close, rsi=rsi)

    def _buy(self, *, timestamp: Any, close: float, rsi: float, context: str = "") -> dict[str, Any]:
        next_level = self.level + 1
        lots = self.lot_sequence[next_level - 1]
        cost = lots * self.base_lot_cost_usd
        units_bought = cost / close
        self.level = next_level
        self.total_invested += cost
        self.total_units_held += units_bought
        self.average_price = self.total_invested / self.total_units_held
        trade = {
            "timestamp": timestamp,
            "action": "BUY",
            "level": self.level,
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

    def _liquidate_basket(self, *, timestamp: Any, close: float) -> dict[str, Any]:
        proceeds = self.total_units_held * close
        profit = proceeds - self.total_invested
        trade = {
            "timestamp": timestamp,
            "action": "SELL_BASKET",
            "level": self.level,
            "price": close,
            "total_invested": self.total_invested,
            "proceeds": proceeds,
            "profit": profit,
            "average_price": self.average_price,
        }
        self.trade_log.append(trade)
        self.reset()
        return trade

    def run(self, df: Any) -> Any:
        """Run across a full 4H OHLC history. `df` needs 'open' and 'close' columns."""

        import pandas as pd

        df = df.copy()
        df["rsi"] = self.calculate_rsi(df["close"], period=self.rsi_period)
        for timestamp, row in df.iterrows():
            if pd.isna(row["rsi"]):
                continue
            self.on_bar(
                timestamp=timestamp,
                open_price=float(row["open"]),
                close=float(row["close"]),
                rsi=float(row["rsi"]),
            )
        return pd.DataFrame(self.trade_log)


class ClaudeModifiedMartingaleRSI:
    definition = StrategyDefinition(
        slug="claude_modified_martingale_rsi",
        name="Claude Modified Martingale RSI",
        style="dry-run: 28-level linear-lot Martingale/DCA gated by RSI<50, basket take-profit, bull re-entry",
        description=(
            "Buys an initial lot only when RSI is below the entry threshold "
            "(default 50). Adds a new safety level (linear lot sizing "
            "1,1,2,2,...,14,14 across 28 levels, scaled to whatever capital "
            "pool it's given) each time price closes below the running "
            "average entry price by the step-drop threshold (default 2%), "
            "but only while RSI stays below the entry threshold -- a drop "
            "with RSI at or above threshold is skipped until RSI falls back "
            "under it. Liquidates the entire basket at the take-profit "
            "threshold (default +4.5%) above the average entry price. If "
            "the very next candle after that exit closes green with a body "
            "at least a configurable % of price (default 0.3%), re-enters "
            "immediately to keep riding the trend instead of waiting for "
            "RSI; otherwise falls back to the normal RSI<threshold idle "
            "wait. No stop loss; the level ladder is the only downside "
            "control."
        ),
    )

    def __init__(
        self,
        total_capital_usd: Decimal = TOTAL_CAPITAL_POOL,
        max_levels: int = MAX_LEVELS,
        rsi_period: int = RSI_PERIOD,
        rsi_entry_max: Decimal = RSI_ENTRY_MAX,
        step_drop_percent: Decimal = STEP_DROP_PERCENT,
        take_profit_percent: Decimal = TAKE_PROFIT_PERCENT,
        bull_reentry_min_body_percent: Decimal | None = BULL_REENTRY_MIN_BODY_PERCENT,
    ) -> None:
        self.total_capital_usd = Decimal(total_capital_usd)
        self.max_levels = max_levels
        self.lot_sequence = _build_lot_sequence(max_levels)
        self.rsi_period = rsi_period
        self.rsi_entry_max = Decimal(rsi_entry_max)
        self.step_drop_percent = Decimal(step_drop_percent)
        self.take_profit_percent = Decimal(take_profit_percent)
        self.bull_reentry_min_body_percent = (
            Decimal(bull_reentry_min_body_percent) if bull_reentry_min_body_percent is not None else None
        )
        self.base_lot_cost = (self.total_capital_usd * CAPITAL_UTILIZATION_RATIO) / Decimal(sum(self.lot_sequence))
        self.reset()

    def reset(self) -> None:
        self._rsi = RunningRsi(self.rsi_period)
        self.level = 0
        self.total_invested = Decimal("0")
        self.total_base_held = Decimal("0")
        self.average_price = Decimal("0")
        self._awaiting_bull_reentry_check = False

    def on_candle_tick(self, candle: dict[str, Decimal]) -> ClaudeDecision:
        close = candle["close"]
        open_price = candle["open"]
        rsi = self._rsi.update(close)
        if rsi is None:
            return ClaudeDecision(action="WAIT", reason=f"Warming up {self.rsi_period}-period RSI.")

        if self._awaiting_bull_reentry_check:
            self._awaiting_bull_reentry_check = False
            if self.bull_reentry_min_body_percent is not None and close > 0:
                body_percent = ((close - open_price) / close) * Decimal("100")
                if close > open_price and body_percent >= self.bull_reentry_min_body_percent:
                    return self._buy(
                        close,
                        rsi,
                        context=(
                            f"Bull-capture re-entry: candle after take-profit closed strong green "
                            f"(body {body_percent:.2f}% >= {self.bull_reentry_min_body_percent}%). "
                        ),
                    )
            # Not a strong green follow-through -- fall through to the normal idle/RSI check below.

        if self.level > 0:
            take_profit_price = self.average_price * (Decimal("1") + self.take_profit_percent / Decimal("100"))
            if close >= take_profit_price:
                reason = (
                    f"Basket take-profit: close {close} >= TP {take_profit_price:.6f} "
                    f"(avg entry {self.average_price:.6f}, level {self.level})."
                )
                self._reset_position()
                self._awaiting_bull_reentry_check = True
                return ClaudeDecision(action="SIMULATED_SELL", reason=reason, price=close)

            if self.level >= self.max_levels:
                return ClaudeDecision(
                    action="HOLD",
                    reason=f"All {self.max_levels} safety levels are filled; holding for take-profit only.",
                )

            trigger_price = self.average_price * (Decimal("1") - self.step_drop_percent / Decimal("100"))
            if close > trigger_price:
                return ClaudeDecision(
                    action="HOLD",
                    reason=f"Level {self.level}/{self.max_levels}; waiting for a {self.step_drop_percent}% drop below avg entry.",
                )
            if rsi >= self.rsi_entry_max:
                return ClaudeDecision(
                    action="HOLD",
                    reason=(
                        f"Level {self.level}/{self.max_levels}; {self.step_drop_percent}% drop trigger hit but "
                        f"RSI {rsi:.1f} >= {self.rsi_entry_max} -- pausing to avoid a high-momentum knife."
                    ),
                )
            return self._buy(close, rsi)

        if rsi >= self.rsi_entry_max:
            return ClaudeDecision(action="WAIT", reason=f"Idle; RSI {rsi:.1f} >= {self.rsi_entry_max}, no entry.")
        return self._buy(close, rsi)

    def _buy(self, close: Decimal, rsi: Decimal, *, context: str = "") -> ClaudeDecision:
        next_level = self.level + 1
        lots = self.lot_sequence[next_level - 1]
        lot_cost = lots * self.base_lot_cost
        remaining_capital = self.total_capital_usd - self.total_invested
        if remaining_capital <= 0:
            return ClaudeDecision(action="HOLD", reason="Capital pool exhausted; holding for take-profit only.")
        fraction = lot_cost / remaining_capital
        if fraction > 1:
            fraction = Decimal("1")
        self.level = next_level
        return ClaudeDecision(
            action="SIMULATED_BUY",
            reason=f"{context}Level {self.level}/{self.max_levels}: buying {lots} lot(s) (${lot_cost:.4f}) at RSI {rsi:.1f}.",
            price=close,
            fraction=fraction,
        )

    def record_fill(self, *, usdt_spent: Decimal, base_bought: Decimal) -> None:
        self.total_invested += usdt_spent
        self.total_base_held += base_bought
        if self.total_base_held > 0:
            self.average_price = self.total_invested / self.total_base_held

    def _reset_position(self) -> None:
        self.level = 0
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
        if guide is None or guide.current_price is None or guide.rsi14 is None:
            return self._decision(
                symbol=symbol,
                user_label=user_label,
                verdict="BLOCKED_BY_RISK",
                score=0,
                risk_level="High",
                thesis=f"{symbol} cannot evaluate the modified Martingale RSI ladder without RSI data.",
                triggers=("Collect enough 4H candle history for a 14-period RSI.",),
                invalidation=("No simulation without a confirmed RSI reading.",),
                reasons=("Missing RSI or current price.",),
            )
        ready = guide.rsi14 < self.rsi_entry_max
        verdict = "MARTINGALE RSI ENTRY READY" if ready else "MARTINGALE RSI WAITING"
        thesis = (
            f"{symbol} 4H RSI is {guide.rsi14:.1f}, "
            + (
                f"below {self.rsi_entry_max} -- an initial or next-level buy is eligible."
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
                f"Initial entry: 4H RSI below {self.rsi_entry_max} while idle.",
                f"Next level: price closes {self.step_drop_percent}% below the running average entry price, "
                f"and 4H RSI is below {self.rsi_entry_max} at that close.",
                (
                    f"Bull-capture re-entry: the candle right after a take-profit exit closes green with a "
                    f"body >= {self.bull_reentry_min_body_percent}% of price."
                    if self.bull_reentry_min_body_percent is not None
                    else "Bull-capture re-entry is disabled; always waits for RSI after an exit."
                ),
            ),
            invalidation=(
                f"Global take-profit: entire basket liquidates at +{self.take_profit_percent}% above the average entry price.",
                f"No stop loss; caps out at {self.max_levels} safety levels (${self.total_capital_usd} capital pool).",
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


if __name__ == "__main__":
    print(format_validation_table_markdown(compute_validation_table()))
