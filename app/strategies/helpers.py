"""Shared deterministic helpers for strategy modules."""

from __future__ import annotations

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide


def clean_user_label(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    return cleaned[:60]


def summary_reasons(
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


def breakout_trigger(guide: TechnicalSignalGuide | None) -> str:
    if guide is None or guide.nearest_resistance is None:
        return "Wait for a close above nearest resistance."
    return f"Wait for a close above resistance near {guide.nearest_resistance}."


def is_bullish(guide: TechnicalSignalGuide | None) -> bool:
    return guide is not None and "Bullish" in guide.bias and guide.signal != "AVOID"


def is_bearish(guide: TechnicalSignalGuide | None) -> bool:
    return guide is not None and "Bearish" in guide.bias


def shortest_timeframe_guide(
    guides: dict[str, TechnicalSignalGuide],
) -> TechnicalSignalGuide | None:
    for interval in ("1m", "5m", "15m", "1h", "4h", "1d"):
        guide = guides.get(interval)
        if guide is not None:
            return guide
    return None
