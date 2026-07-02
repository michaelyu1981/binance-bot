"""Modular advisory-board views for deterministic CoinPilot signals.

These advisors are fixed-template commentary only. They do not call AI models,
do not use Binance account data, and must not place or prepare orders.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.signals import MultiTimeframeSignalSummary, TechnicalSignalGuide


MAX_ADVISORY_WORDS = 250


@dataclass(frozen=True)
class AdvisoryBot:
    slug: str
    name: str
    lens: str
    style: str


@dataclass(frozen=True)
class AdvisoryOpinion:
    bot: AdvisoryBot
    verdict: str
    confidence: int
    outlook: str


ADVISORY_BOTS = (
    AdvisoryBot(
        slug="michael_burry",
        name="Michael Burry-style",
        lens="Contrarian macro/value risk analyst",
        style="skeptical",
    ),
    AdvisoryBot(
        slug="ed_seykota",
        name="Ed Seykota-style",
        lens="Mechanical trend follower",
        style="trend",
    ),
    AdvisoryBot(
        slug="william_oneil",
        name="William O'Neil-style",
        lens="Breakout and volume specialist",
        style="breakout",
    ),
    AdvisoryBot(
        slug="mark_minervini",
        name="Mark Minervini-style",
        lens="High-velocity swing trade risk manager",
        style="swing",
    ),
    AdvisoryBot(
        slug="stanley_druckenmiller",
        name="Stanley Druckenmiller-style",
        lens="Macro trend and asymmetric risk analyst",
        style="macro",
    ),
    AdvisoryBot(
        slug="linda_raschke",
        name="Linda Raschke-style",
        lens="Short-term tactical trader",
        style="tactical",
    ),
    AdvisoryBot(
        slug="jim_simons",
        name="Jim Simons-style",
        lens="Quantitative evidence and regime analyst",
        style="quant",
    ),
)


def advisory_bot_by_slug(slug: str) -> AdvisoryBot:
    for bot in ADVISORY_BOTS:
        if bot.slug == slug:
            return bot
    return ADVISORY_BOTS[0]


def build_advisory_opinion(
    *,
    bot: AdvisoryBot,
    summary: MultiTimeframeSignalSummary,
    guides_by_interval: dict[str, TechnicalSignalGuide],
) -> AdvisoryOpinion:
    """Build one fixed-template advisory opinion."""

    guide_1d = guides_by_interval.get("1d")
    guide_4h = guides_by_interval.get("4h")
    guide_1h = guides_by_interval.get("1h")
    guide_15m = guides_by_interval.get("15m")
    confidence = _confidence(summary)
    verdict = _verdict(summary)
    outlook = _outlook_for_style(
        bot=bot,
        summary=summary,
        guide_1d=guide_1d,
        guide_4h=guide_4h,
        guide_1h=guide_1h,
        guide_15m=guide_15m,
    )
    return AdvisoryOpinion(
        bot=bot,
        verdict=verdict,
        confidence=confidence,
        outlook=_limit_words(outlook, MAX_ADVISORY_WORDS),
    )


def build_advisory_opinions(
    *,
    summary: MultiTimeframeSignalSummary,
    guides_by_interval: dict[str, TechnicalSignalGuide],
) -> tuple[AdvisoryOpinion, ...]:
    return tuple(
        build_advisory_opinion(
            bot=bot,
            summary=summary,
            guides_by_interval=guides_by_interval,
        )
        for bot in ADVISORY_BOTS
    )


def build_consensus_summary(opinions: tuple[AdvisoryOpinion, ...]) -> str:
    if not opinions:
        return "No advisory opinions are available."
    verdict_counts: dict[str, int] = {}
    for opinion in opinions:
        verdict_counts[opinion.verdict] = verdict_counts.get(opinion.verdict, 0) + 1
    leading_verdict = sorted(verdict_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    average_confidence = round(sum(opinion.confidence for opinion in opinions) / len(opinions))
    return (
        f"Consensus: {leading_verdict}. Average confidence {average_confidence}/100. "
        "This is advisory commentary only; the deterministic signal engine remains the source of truth."
    )


def _outlook_for_style(
    *,
    bot: AdvisoryBot,
    summary: MultiTimeframeSignalSummary,
    guide_1d: TechnicalSignalGuide | None,
    guide_4h: TechnicalSignalGuide | None,
    guide_1h: TechnicalSignalGuide | None,
    guide_15m: TechnicalSignalGuide | None,
) -> str:
    if bot.style == "skeptical":
        return _skeptical_outlook(summary, guide_1d, guide_4h)
    if bot.style == "trend":
        return _trend_outlook(summary, guide_1d, guide_4h)
    if bot.style == "breakout":
        return _breakout_outlook(summary, guide_4h)
    if bot.style == "swing":
        return _swing_outlook(summary, guide_4h, guide_1h)
    if bot.style == "macro":
        return _macro_outlook(summary, guide_1d, guide_4h)
    if bot.style == "tactical":
        return _tactical_outlook(summary, guide_1h, guide_15m)
    return _quant_outlook(summary, guide_1d, guide_4h, guide_1h, guide_15m)


def _skeptical_outlook(
    summary: MultiTimeframeSignalSummary,
    guide_1d: TechnicalSignalGuide | None,
    guide_4h: TechnicalSignalGuide | None,
) -> str:
    return (
        f"{summary.symbol}: I would be cautious. Overall is {summary.overall} with "
        f"{summary.alignment}. The higher-timeframe bias is {summary.higher_timeframe_bias}, "
        f"so I want proof before trusting a lower-timeframe move. 1D status is "
        f"{_guide_status(guide_1d)} and 4H status is {_guide_status(guide_4h)}. "
        "If support breaks, risk can expand quickly; if price recovers, I still want "
        "confirmation rather than narrative. No automatic trade."
    )


def _trend_outlook(
    summary: MultiTimeframeSignalSummary,
    guide_1d: TechnicalSignalGuide | None,
    guide_4h: TechnicalSignalGuide | None,
) -> str:
    return (
        f"{summary.symbol}: The trend lens says {summary.overall}. I care most about "
        f"1D and 4H alignment. 1D is {_guide_status(guide_1d)}; 4H is {_guide_status(guide_4h)}. "
        f"Alignment is {summary.alignment}. If 1D and 4H disagree, I stay with WAIT. "
        "If both agree and risk is measurable, the setup can move to watchlist status. "
        "Risk control wins over opinion."
    )


def _breakout_outlook(
    summary: MultiTimeframeSignalSummary,
    guide_4h: TechnicalSignalGuide | None,
) -> str:
    resistance = _price_or_unavailable(guide_4h.nearest_resistance if guide_4h else None)
    support = _price_or_unavailable(guide_4h.nearest_support if guide_4h else None)
    return (
        f"{summary.symbol}: I am watching structure. Overall is {summary.overall}; "
        f"4H is {_guide_status(guide_4h)}. Breakout level is near {resistance}, while "
        f"breakdown risk starts near {support}. A clean upside case needs price above "
        "resistance with momentum and volume confirmation. Without that, it is only a watch."
    )


def _swing_outlook(
    summary: MultiTimeframeSignalSummary,
    guide_4h: TechnicalSignalGuide | None,
    guide_1h: TechnicalSignalGuide | None,
) -> str:
    return (
        f"{summary.symbol}: For swing timing, 4H matters first and 1H refines entry. "
        f"Overall is {summary.overall}; 4H is {_guide_status(guide_4h)} and 1H is "
        f"{_guide_status(guide_1h)}. I would avoid chasing if 1H is extended against "
        "unclear 4H structure. The useful setup is a controlled pullback or confirmed "
        "breakout with a clear invalidation level."
    )


def _macro_outlook(
    summary: MultiTimeframeSignalSummary,
    guide_1d: TechnicalSignalGuide | None,
    guide_4h: TechnicalSignalGuide | None,
) -> str:
    return (
        f"{summary.symbol}: The bigger picture is {summary.higher_timeframe_bias}. "
        f"Overall summary is {summary.overall} with score {summary.score}/100. "
        f"1D reads {_guide_status(guide_1d)} and 4H reads {_guide_status(guide_4h)}. "
        "I want asymmetric reward relative to structure. If higher timeframes conflict, "
        "the correct macro action is patience."
    )


def _tactical_outlook(
    summary: MultiTimeframeSignalSummary,
    guide_1h: TechnicalSignalGuide | None,
    guide_15m: TechnicalSignalGuide | None,
) -> str:
    return (
        f"{summary.symbol}: Tactically, short-term pressure is {summary.short_term_pressure}. "
        f"1H is {_guide_status(guide_1h)} and 15M is {_guide_status(guide_15m)}. "
        "Short-term signals are useful for timing, not for overriding 1D or 4H. "
        "If the lower timeframe moves without higher-timeframe support, I treat it as noise."
    )


def _quant_outlook(
    summary: MultiTimeframeSignalSummary,
    guide_1d: TechnicalSignalGuide | None,
    guide_4h: TechnicalSignalGuide | None,
    guide_1h: TechnicalSignalGuide | None,
    guide_15m: TechnicalSignalGuide | None,
) -> str:
    available = sum(guide is not None for guide in (guide_1d, guide_4h, guide_1h, guide_15m))
    return (
        f"{summary.symbol}: Evidence count is {available}/4 timeframes. Overall is "
        f"{summary.overall}, bias {summary.bias}, score {summary.score}/100, alignment "
        f"{summary.alignment}. I prefer decisions when multiple independent timeframe "
        "states agree. Missing or conflicting regimes reduce confidence. This is an "
        "advisory read, not an execution signal."
    )


def _confidence(summary: MultiTimeframeSignalSummary) -> int:
    confidence = summary.score
    if summary.alignment in ("Higher Timeframe Conflict", "Mixed"):
        confidence -= 20
    if summary.missing_intervals:
        confidence -= 10
    return max(0, min(100, confidence))


def _verdict(summary: MultiTimeframeSignalSummary) -> str:
    if summary.overall == "BUY WATCH":
        return "BULLISH WATCH"
    if summary.overall == "SELL WATCH":
        return "BEARISH WATCH"
    if summary.overall == "AVOID":
        return "NO TRADE"
    return "WAIT"


def _guide_status(guide: TechnicalSignalGuide | None) -> str:
    if guide is None:
        return "unavailable"
    return f"{guide.signal}, {guide.bias}, {guide.market_type}"


def _price_or_unavailable(value: object) -> str:
    if value is None:
        return "unavailable"
    return str(value)


def _limit_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."
