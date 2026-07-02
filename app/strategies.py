"""Deterministic advisory algorithms for CoinPilot.

These algorithms are fixed-rule strategy lenses only. They do not call AI,
do not access Binance account data, and must not place or prepare orders.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide


@dataclass(frozen=True)
class StrategyDefinition:
    slug: str
    name: str
    style: str
    description: str


@dataclass(frozen=True)
class StrategyDecision:
    strategy: StrategyDefinition
    user_label: str
    symbol: str
    verdict: str
    score: int
    risk_level: str
    mode: str
    thesis: str
    triggers: tuple[str, ...]
    invalidation: tuple[str, ...]
    reasons: tuple[str, ...]


STRATEGIES = (
    StrategyDefinition(
        slug="coinpilot_trend_guard_v1",
        name="CoinPilot Trend Guard v1",
        style="Multi-timeframe trend confirmation",
        description="Waits for 1D and 4H alignment before treating a setup as actionable.",
    ),
    StrategyDefinition(
        slug="coinpilot_breakout_scout_v1",
        name="CoinPilot Breakout Scout v1",
        style="Volatility squeeze and resistance breakout watch",
        description="Looks for compressed volatility and a clear breakout level before acting.",
    ),
    StrategyDefinition(
        slug="coinpilot_reclaim_v1",
        name="CoinPilot Reclaim v1",
        style="Pullback recovery and moving-average reclaim",
        description="Watches weak or oversold markets for recovery above key averages.",
    ),
    StrategyDefinition(
        slug="coinpilot_no_martingale_guard_v1",
        name="CoinPilot No-Martingale Guard v1",
        style="Risk-control guardrail",
        description="Rejects averaging-down or doubling-down logic until trend and risk improve.",
    ),
)


def strategy_by_slug(slug: str) -> StrategyDefinition:
    for strategy in STRATEGIES:
        if strategy.slug == slug:
            return strategy
    return STRATEGIES[0]


def build_strategy_decisions(
    *,
    symbol: str,
    summary: MultiTimeframeSignalSummary,
    guides_by_interval: dict[str, TechnicalSignalGuide],
    user_label: str = "",
) -> tuple[StrategyDecision, ...]:
    """Evaluate every deterministic strategy for one symbol."""

    label = _clean_user_label(user_label)
    return tuple(
        build_strategy_decision(
            strategy=strategy,
            symbol=symbol,
            summary=summary,
            guides_by_interval=guides_by_interval,
            user_label=label,
        )
        for strategy in STRATEGIES
    )


def build_strategy_decision(
    *,
    strategy: StrategyDefinition,
    symbol: str,
    summary: MultiTimeframeSignalSummary,
    guides_by_interval: dict[str, TechnicalSignalGuide],
    user_label: str = "",
) -> StrategyDecision:
    """Evaluate one deterministic strategy for one symbol."""

    label = _clean_user_label(user_label)
    if strategy.slug == "coinpilot_breakout_scout_v1":
        return _breakout_scout(strategy, symbol, summary, guides_by_interval, label)
    if strategy.slug == "coinpilot_reclaim_v1":
        return _reclaim(strategy, symbol, summary, guides_by_interval, label)
    if strategy.slug == "coinpilot_no_martingale_guard_v1":
        return _no_martingale_guard(strategy, symbol, summary, guides_by_interval, label)
    return _trend_guard(strategy, symbol, summary, guides_by_interval, label)


def _trend_guard(
    strategy: StrategyDefinition,
    symbol: str,
    summary: MultiTimeframeSignalSummary,
    guides: dict[str, TechnicalSignalGuide],
    label: str,
) -> StrategyDecision:
    guide_1d = guides.get("1d")
    guide_4h = guides.get("4h")
    aligned_bullish = _is_bullish(guide_1d) and _is_bullish(guide_4h)
    aligned_bearish = _is_bearish(guide_1d) and _is_bearish(guide_4h)
    if aligned_bullish and summary.score >= 65:
        verdict = "BUY WATCH"
        thesis = f"{symbol} has higher-timeframe bullish alignment, but CoinPilot remains advisory only."
        triggers = (
            "Wait for price to hold above 4H support.",
            "Prefer entries only after momentum stays bullish on 4H and 1D.",
        )
        invalidation = ("Stand down if 4H closes below support or the 1D bias turns bearish.",)
        risk_level = "Medium"
    elif aligned_bearish:
        verdict = "RISK-OFF"
        thesis = f"{symbol} has higher-timeframe bearish alignment, so trend-following entries are not clean."
        triggers = ("Wait for reclaim above SMA50 or a new bullish 4H structure.",)
        invalidation = ("Avoid long bias while 1D and 4H remain bearish.",)
        risk_level = "High"
    else:
        verdict = "WAIT"
        thesis = f"{symbol} does not have clean 1D and 4H alignment yet."
        triggers = ("Wait for 1D and 4H bias to agree before treating the setup as tradable.",)
        invalidation = ("Avoid forcing trades while timeframes conflict.",)
        risk_level = "Medium"
    return StrategyDecision(
        strategy=strategy,
        user_label=label,
        symbol=symbol,
        verdict=verdict,
        score=summary.score,
        risk_level=risk_level,
        mode="Advisory only",
        thesis=thesis,
        triggers=triggers,
        invalidation=invalidation,
        reasons=_summary_reasons(summary, guide_1d, guide_4h),
    )


def _breakout_scout(
    strategy: StrategyDefinition,
    symbol: str,
    summary: MultiTimeframeSignalSummary,
    guides: dict[str, TechnicalSignalGuide],
    label: str,
) -> StrategyDecision:
    guide_4h = guides.get("4h")
    guide_1h = guides.get("1h")
    squeeze = any(
        guide is not None and guide.bollinger_squeeze in ("Yes", "Possible")
        for guide in (guide_4h, guide_1h)
    )
    bullish_pressure = _is_bullish(guide_4h) or _is_bullish(guide_1h)
    if squeeze and bullish_pressure:
        verdict = "BREAKOUT WATCH"
        score = min(100, summary.score + 8)
        thesis = f"{symbol} has compression with bullish pressure; wait for a confirmed break, not a guess."
        triggers = (
            _breakout_trigger(guide_4h),
            "Confirm with RSI above 55 and MACD histogram improving.",
        )
        invalidation = ("Cancel the setup if price rejects resistance and loses the Bollinger middle band.",)
        risk_level = "Medium"
    elif squeeze:
        verdict = "WAIT"
        score = summary.score
        thesis = f"{symbol} may be compressing, but direction is not confirmed."
        triggers = ("Wait for a clean close outside the range with momentum confirmation.",)
        invalidation = ("Do not trade the squeeze before direction is confirmed.",)
        risk_level = "Medium"
    else:
        verdict = "NO BREAKOUT SETUP"
        score = max(0, summary.score - 8)
        thesis = f"{symbol} is not showing a clean volatility squeeze breakout setup."
        triggers = ("Wait for Bollinger squeeze or a clean resistance break.",)
        invalidation = ("Ignore breakout logic while volatility and structure are ordinary.",)
        risk_level = "Low"
    return StrategyDecision(
        strategy=strategy,
        user_label=label,
        symbol=symbol,
        verdict=verdict,
        score=score,
        risk_level=risk_level,
        mode="Advisory only",
        thesis=thesis,
        triggers=triggers,
        invalidation=invalidation,
        reasons=_summary_reasons(summary, guide_4h, guide_1h),
    )


def _reclaim(
    strategy: StrategyDefinition,
    symbol: str,
    summary: MultiTimeframeSignalSummary,
    guides: dict[str, TechnicalSignalGuide],
    label: str,
) -> StrategyDecision:
    guide_4h = guides.get("4h")
    guide_1h = guides.get("1h")
    weak_but_recoverable = any(
        guide is not None
        and guide.rsi14 is not None
        and guide.rsi14 < 50
        and guide.price_vs_ema20 == "Below"
        for guide in (guide_4h, guide_1h)
    )
    if weak_but_recoverable and "Bearish" not in summary.bias:
        verdict = "RECLAIM WATCH"
        thesis = f"{symbol} is weak short term, but not fully bearish across the summary."
        triggers = (
            "Wait for close back above EMA20.",
            "Then require RSI recovery above 50 before treating it as a recovery setup.",
        )
        invalidation = ("Cancel if price breaks support before reclaiming EMA20.",)
        risk_level = "Medium"
    elif weak_but_recoverable:
        verdict = "WAIT"
        thesis = f"{symbol} is weak and needs proof of recovery before any bullish interpretation."
        triggers = ("Wait for EMA20 reclaim plus RSI above 50.",)
        invalidation = ("Avoid catching the dip while higher timeframe bias remains bearish.",)
        risk_level = "High"
    else:
        verdict = "NO RECLAIM SETUP"
        thesis = f"{symbol} is not currently matching the reclaim setup rules."
        triggers = ("Wait for a pullback, stabilization, and reclaim of EMA20.",)
        invalidation = ("Do not use reclaim logic when price is already extended or structure is unclear.",)
        risk_level = "Low"
    return StrategyDecision(
        strategy=strategy,
        user_label=label,
        symbol=symbol,
        verdict=verdict,
        score=summary.score,
        risk_level=risk_level,
        mode="Advisory only",
        thesis=thesis,
        triggers=triggers,
        invalidation=invalidation,
        reasons=_summary_reasons(summary, guide_4h, guide_1h),
    )


def _no_martingale_guard(
    strategy: StrategyDefinition,
    symbol: str,
    summary: MultiTimeframeSignalSummary,
    guides: dict[str, TechnicalSignalGuide],
    label: str,
) -> StrategyDecision:
    guide_4h = guides.get("4h")
    guide_1d = guides.get("1d")
    dangerous = (
        summary.overall in ("SELL", "STRONG SELL", "AVOID")
        or summary.bias in ("Bearish", "Strong Bearish")
        or _is_bearish(guide_4h)
    )
    if dangerous:
        verdict = "DO NOT AVERAGE DOWN"
        risk_level = "High"
        thesis = f"{symbol} fails the risk guard. Doubling down or martingale-style sizing is blocked."
        triggers = ("Reduce risk first; wait for trend recovery and support confirmation.",)
        invalidation = ("No martingale logic is allowed by this project safety policy.",)
    else:
        verdict = "RISK CHECK PASS"
        risk_level = "Medium"
        thesis = f"{symbol} does not trigger the martingale risk block, but this is still not permission to trade."
        triggers = ("Use fixed risk only in future dry-run simulations.",)
        invalidation = ("Block the setup if the 4H or 1D trend turns bearish.",)
    return StrategyDecision(
        strategy=strategy,
        user_label=label,
        symbol=symbol,
        verdict=verdict,
        score=summary.score,
        risk_level=risk_level,
        mode="Advisory only",
        thesis=thesis,
        triggers=triggers,
        invalidation=invalidation,
        reasons=_summary_reasons(summary, guide_1d, guide_4h),
    )


def _summary_reasons(
    summary: MultiTimeframeSignalSummary,
    first: TechnicalSignalGuide | None,
    second: TechnicalSignalGuide | None,
) -> tuple[str, ...]:
    reasons = [
        f"Overall summary: {summary.overall}, {summary.bias}, score {summary.score}/100.",
        f"Alignment: {summary.alignment}.",
    ]
    for guide in (first, second):
        if guide is not None:
            reasons.append(
                f"{guide.symbol} {guide.signal} on {guide.market_type}; RSI {guide.rsi14 or 'unavailable'}."
            )
    return tuple(reasons)


def _breakout_trigger(guide: TechnicalSignalGuide | None) -> str:
    if guide is None or guide.nearest_resistance is None:
        return "Wait for a close above nearest resistance."
    return f"Wait for a close above resistance near {guide.nearest_resistance}."


def _is_bullish(guide: TechnicalSignalGuide | None) -> bool:
    return guide is not None and "Bullish" in guide.bias and guide.signal != "AVOID"


def _is_bearish(guide: TechnicalSignalGuide | None) -> bool:
    return guide is not None and "Bearish" in guide.bias


def _clean_user_label(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    return cleaned[:60]
