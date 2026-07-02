"""Deterministic technical signal guide for public candle data.

This module uses fixed rules and templates only. It does not call AI services,
does not access Binance account data, and must not place or prepare orders.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.indicators import (
    IndicatorSnapshot,
    calculate_bollinger_band_series,
    calculate_macd_series,
)


RSI_BULLISH_THRESHOLD = Decimal("55")
RSI_BEARISH_THRESHOLD = Decimal("45")
RSI_RECOVERY_THRESHOLD = Decimal("50")
ATR_LOW_THRESHOLD_PERCENT = Decimal("1.5")
ATR_HIGH_THRESHOLD_PERCENT = Decimal("3.5")
BOLLINGER_SQUEEZE_THRESHOLD_PERCENT = Decimal("4")
BOLLINGER_FLAT_THRESHOLD_PERCENT = Decimal("0.25")
SUPPORT_RESISTANCE_LOOKBACK_CANDLES = 20

SCORE_WEIGHTS = {
    "trend": 30,
    "momentum": 25,
    "volatility": 15,
    "bollinger": 10,
    "structure": 10,
    "volume": 10,
}

BUY_SIGNAL_THRESHOLD = 70
STRONG_BUY_SIGNAL_THRESHOLD = 85
SELL_SIGNAL_THRESHOLD = 70
STRONG_SELL_SIGNAL_THRESHOLD = 85
AVOID_SIGNAL_THRESHOLD = 35


@dataclass(frozen=True)
class ScoreBreakdown:
    trend: int
    momentum: int
    volatility: int
    bollinger: int
    structure: int
    volume: int | None

    @property
    def total(self) -> int:
        return (
            self.trend
            + self.momentum
            + self.volatility
            + self.bollinger
            + self.structure
            + (self.volume if self.volume is not None else 0)
        )


@dataclass(frozen=True)
class TechnicalSignalGuide:
    symbol: str
    signal: str
    bias: str
    score: int
    market_type: str
    trade_quality: str
    action: str
    plain_english: str
    final_decision: str
    current_price: Decimal | None
    sma50: Decimal | None
    ema20: Decimal | None
    distance_from_sma50_percent: Decimal | None
    price_vs_sma50: str
    price_vs_ema20: str
    trend_status: str
    rsi14: Decimal | None
    rsi_status: str
    macd: Decimal | None
    macd_signal: Decimal | None
    macd_histogram: Decimal | None
    macd_status: str
    macd_histogram_status: str
    atr14: Decimal | None
    atr_percent: Decimal | None
    atr_status: str
    conservative_stop_guide: Decimal | None
    wide_stop_guide: Decimal | None
    bollinger_upper: Decimal | None
    bollinger_middle: Decimal | None
    bollinger_lower: Decimal | None
    bollinger_band_width_percent: Decimal | None
    bollinger_band_width_status: str
    bollinger_price_location: str
    bollinger_squeeze: str
    bollinger_reversal_risk: str
    nearest_support: Decimal | None
    nearest_resistance: Decimal | None
    breakout_level: Decimal | None
    breakdown_level: Decimal | None
    bullish_trigger: str
    bearish_trigger: str
    waiting_for: tuple[str, ...]
    reasons: tuple[str, ...]
    risk_guide: tuple[str, ...]
    score_breakdown: ScoreBreakdown
    missing_data: tuple[str, ...]
    volume_vs_average_percent: Decimal | None


def build_technical_signal_guide(
    *,
    symbol: str,
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    volumes: Sequence[Decimal],
    snapshot: IndicatorSnapshot,
) -> TechnicalSignalGuide:
    """Build a rule-based technical signal guide from public candle data."""

    current_price = closes[-1] if closes else None
    missing_data = _missing_data(snapshot)
    distance_from_sma50 = _ratio_percent_delta(current_price, snapshot.sma)
    band_width = _bollinger_band_width(snapshot)
    band_width_status = _bollinger_band_width_status(closes)
    atr_status = _atr_status(snapshot.atr_percent)
    price_vs_sma = _price_vs_average(current_price, snapshot.sma)
    price_vs_ema = _price_vs_average(current_price, snapshot.ema)
    trend_status = _trend_status(current_price, snapshot.ema, snapshot.sma)
    rsi_status = _rsi_status(snapshot.rsi)
    macd_status = _macd_status(snapshot.macd, snapshot.macd_signal)
    macd_histogram_status = _macd_histogram_status(snapshot.macd_histogram)
    price_location = _bollinger_price_location(current_price, snapshot)
    squeeze = _bollinger_squeeze(snapshot.atr_percent, band_width)
    reversal_risk = _bollinger_reversal_risk(price_location)
    support, resistance = _support_resistance(highs, lows)
    macd_histogram_improving = _macd_histogram_improving(closes)

    score_breakdown = _score_breakdown(
        current_price=current_price,
        snapshot=snapshot,
        atr_status=atr_status,
        band_width=band_width,
        support=support,
        resistance=resistance,
    )
    score = score_breakdown.total
    bias = _bias(current_price, snapshot)
    market_type = _market_type(
        current_price=current_price,
        snapshot=snapshot,
        atr_status=atr_status,
        band_width=band_width,
        squeeze=squeeze,
    )
    signal = _signal(
        score=score,
        bias=bias,
        market_type=market_type,
        current_price=current_price,
        support=support,
        resistance=resistance,
        snapshot=snapshot,
        macd_histogram_improving=macd_histogram_improving,
        missing_data=missing_data,
    )
    trade_quality = _trade_quality(signal, score, market_type, missing_data)
    action = _action(signal, bias)
    waiting_for = _waiting_for(signal, bias)
    reasons = _reasons(
        current_price=current_price,
        snapshot=snapshot,
        trend_status=trend_status,
        macd_status=macd_status,
        atr_status=atr_status,
        missing_data=missing_data,
    )
    plain_english = _plain_english(
        symbol=symbol,
        signal=signal,
        bias=bias,
        market_type=market_type,
        atr_status=atr_status,
    )

    return TechnicalSignalGuide(
        symbol=symbol,
        signal=signal,
        bias=bias,
        score=score,
        market_type=market_type,
        trade_quality=trade_quality,
        action=action,
        plain_english=plain_english,
        final_decision=_final_decision(signal, bias, market_type),
        current_price=current_price,
        sma50=snapshot.sma,
        ema20=snapshot.ema,
        distance_from_sma50_percent=distance_from_sma50,
        price_vs_sma50=price_vs_sma,
        price_vs_ema20=price_vs_ema,
        trend_status=trend_status,
        rsi14=snapshot.rsi,
        rsi_status=rsi_status,
        macd=snapshot.macd,
        macd_signal=snapshot.macd_signal,
        macd_histogram=snapshot.macd_histogram,
        macd_status=macd_status,
        macd_histogram_status=macd_histogram_status,
        atr14=snapshot.atr,
        atr_percent=snapshot.atr_percent,
        atr_status=atr_status,
        conservative_stop_guide=_multiple(snapshot.atr, Decimal("1.5")),
        wide_stop_guide=_multiple(snapshot.atr, Decimal("2.0")),
        bollinger_upper=snapshot.bollinger_upper,
        bollinger_middle=snapshot.bollinger_middle,
        bollinger_lower=snapshot.bollinger_lower,
        bollinger_band_width_percent=band_width,
        bollinger_band_width_status=band_width_status,
        bollinger_price_location=price_location,
        bollinger_squeeze=squeeze,
        bollinger_reversal_risk=reversal_risk,
        nearest_support=support,
        nearest_resistance=resistance,
        breakout_level=resistance,
        breakdown_level=support,
        bullish_trigger=_bullish_trigger(resistance),
        bearish_trigger=_bearish_trigger(support),
        waiting_for=waiting_for,
        reasons=reasons,
        risk_guide=_risk_guide(atr_status, squeeze),
        score_breakdown=score_breakdown,
        missing_data=missing_data,
        volume_vs_average_percent=snapshot.volume_ratio,
    )


def _missing_data(snapshot: IndicatorSnapshot) -> tuple[str, ...]:
    missing = []
    if snapshot.sma is None:
        missing.append("SMA50")
    if snapshot.ema is None:
        missing.append("EMA20")
    if snapshot.rsi is None:
        missing.append("RSI14")
    if snapshot.macd is None or snapshot.macd_signal is None or snapshot.macd_histogram is None:
        missing.append("MACD")
    if snapshot.atr is None or snapshot.atr_percent is None:
        missing.append("ATR14")
    if (
        snapshot.bollinger_upper is None
        or snapshot.bollinger_middle is None
        or snapshot.bollinger_lower is None
    ):
        missing.append("Bollinger Bands")
    if snapshot.volume is None or snapshot.average_volume is None or snapshot.volume_ratio is None:
        missing.append("Volume average")
    return tuple(missing)


def _score_breakdown(
    *,
    current_price: Decimal | None,
    snapshot: IndicatorSnapshot,
    atr_status: str,
    band_width: Decimal | None,
    support: Decimal | None,
    resistance: Decimal | None,
) -> ScoreBreakdown:
    trend = 0
    if current_price is not None and snapshot.sma is not None:
        trend += 10
    if current_price is not None and snapshot.ema is not None:
        trend += 8
    if snapshot.ema is not None and snapshot.sma is not None:
        trend += 12 if snapshot.ema != snapshot.sma else 6

    momentum = 0
    if snapshot.rsi is not None:
        if snapshot.rsi > RSI_BULLISH_THRESHOLD or snapshot.rsi < RSI_BEARISH_THRESHOLD:
            momentum += 10
        elif Decimal("45") <= snapshot.rsi <= Decimal("55"):
            momentum += 5
        else:
            momentum += 7
    if snapshot.macd is not None and snapshot.macd_signal is not None:
        momentum += 10 if snapshot.macd != snapshot.macd_signal else 5
    if snapshot.macd_histogram is not None:
        momentum += 5 if snapshot.macd_histogram != 0 else 2

    volatility = 0
    if atr_status == "Normal":
        volatility = 12
    elif atr_status == "Low":
        volatility = 8
    elif atr_status == "High":
        volatility = 5

    bollinger = 0
    if band_width is not None:
        bollinger += 5
        if band_width <= BOLLINGER_SQUEEZE_THRESHOLD_PERCENT:
            bollinger += 2
        else:
            bollinger += 4
    if snapshot.bollinger_percent_b is not None:
        bollinger += 3

    structure = 0
    if current_price is not None and support is not None and resistance is not None:
        structure = 5
        range_size = resistance - support
        if range_size > 0:
            position = (current_price - support) / range_size
            if position <= Decimal("0.25") or position >= Decimal("0.75"):
                structure += 3
            else:
                structure += 1

    volume = None
    if snapshot.volume_ratio is not None:
        if snapshot.volume_ratio >= Decimal("150"):
            volume = 10
        elif snapshot.volume_ratio >= Decimal("100"):
            volume = 7
        elif snapshot.volume_ratio >= Decimal("70"):
            volume = 4
        else:
            volume = 2

    return ScoreBreakdown(
        trend=min(trend, SCORE_WEIGHTS["trend"]),
        momentum=min(momentum, SCORE_WEIGHTS["momentum"]),
        volatility=min(volatility, SCORE_WEIGHTS["volatility"]),
        bollinger=min(bollinger, SCORE_WEIGHTS["bollinger"]),
        structure=min(structure, SCORE_WEIGHTS["structure"]),
        volume=volume,
    )


def _bias(current_price: Decimal | None, snapshot: IndicatorSnapshot) -> str:
    bullish = 0
    bearish = 0
    if current_price is not None and snapshot.sma is not None:
        bullish += int(current_price > snapshot.sma)
        bearish += int(current_price < snapshot.sma)
    if current_price is not None and snapshot.ema is not None:
        bullish += int(current_price > snapshot.ema)
        bearish += int(current_price < snapshot.ema)
    if snapshot.ema is not None and snapshot.sma is not None:
        bullish += int(snapshot.ema > snapshot.sma)
        bearish += int(snapshot.ema < snapshot.sma)
    if snapshot.rsi is not None:
        bullish += int(snapshot.rsi > RSI_BULLISH_THRESHOLD)
        bearish += int(snapshot.rsi < RSI_BEARISH_THRESHOLD)
    if snapshot.macd is not None and snapshot.macd_signal is not None:
        bullish += int(snapshot.macd > snapshot.macd_signal)
        bearish += int(snapshot.macd < snapshot.macd_signal)

    difference = bullish - bearish
    if difference >= 4:
        return "Strong Bullish"
    if difference == 3:
        return "Bullish"
    if difference in (1, 2):
        return "Slight Bullish"
    if difference <= -4:
        return "Strong Bearish"
    if difference == -3:
        return "Bearish"
    if difference in (-1, -2):
        return "Slight Bearish"
    return "Neutral"


def _market_type(
    *,
    current_price: Decimal | None,
    snapshot: IndicatorSnapshot,
    atr_status: str,
    band_width: Decimal | None,
    squeeze: str,
) -> str:
    if squeeze == "Yes":
        return "Volatility Squeeze"
    if _conflicting_indicators(current_price, snapshot):
        return "Choppy / Avoid"
    if (
        current_price is not None
        and snapshot.sma is not None
        and snapshot.ema is not None
        and snapshot.rsi is not None
        and current_price < snapshot.sma
        and snapshot.ema < snapshot.sma
        and snapshot.rsi < RSI_BEARISH_THRESHOLD
        and atr_status == "Low"
    ):
        return "Weak Downtrend / Low Volatility"
    if (
        current_price is not None
        and snapshot.sma is not None
        and snapshot.ema is not None
        and snapshot.rsi is not None
        and current_price > snapshot.sma
        and snapshot.ema > snapshot.sma
        and snapshot.rsi > RSI_BULLISH_THRESHOLD
    ):
        return "Trending Up"
    if (
        current_price is not None
        and snapshot.sma is not None
        and snapshot.ema is not None
        and snapshot.rsi is not None
        and current_price < snapshot.sma
        and snapshot.ema < snapshot.sma
        and snapshot.rsi < RSI_BEARISH_THRESHOLD
    ):
        return "Trending Down"
    if (
        snapshot.rsi is not None
        and Decimal("45") <= snapshot.rsi <= Decimal("55")
        and band_width is not None
        and band_width <= BOLLINGER_SQUEEZE_THRESHOLD_PERCENT
    ):
        return "Sideways / Low Volatility"
    if current_price is not None and snapshot.sma is not None and snapshot.ema is not None:
        if current_price > snapshot.sma and snapshot.ema > snapshot.sma:
            return "Weak Uptrend"
        if current_price < snapshot.sma and snapshot.ema < snapshot.sma:
            return "Weak Downtrend"
    if atr_status == "Low":
        return "Low Volatility"
    return "Sideways"


def _signal(
    *,
    score: int,
    bias: str,
    market_type: str,
    current_price: Decimal | None,
    support: Decimal | None,
    resistance: Decimal | None,
    snapshot: IndicatorSnapshot,
    macd_histogram_improving: bool | None,
    missing_data: tuple[str, ...],
) -> str:
    if missing_data and len(missing_data) >= 4:
        return "AVOID"
    if market_type == "Choppy / Avoid" or score < AVOID_SIGNAL_THRESHOLD:
        return "AVOID"
    if market_type in ("Volatility Squeeze", "Sideways / Low Volatility"):
        return "WAIT"

    bullish_breakout = (
        current_price is not None
        and resistance is not None
        and snapshot.rsi is not None
        and snapshot.macd is not None
        and snapshot.macd_signal is not None
        and current_price > resistance
        and snapshot.rsi > RSI_BULLISH_THRESHOLD
        and snapshot.macd > snapshot.macd_signal
        and macd_histogram_improving is True
    )
    bearish_breakdown = (
        current_price is not None
        and support is not None
        and snapshot.rsi is not None
        and snapshot.macd is not None
        and snapshot.macd_signal is not None
        and current_price < support
        and snapshot.rsi < RSI_BEARISH_THRESHOLD
        and snapshot.macd < snapshot.macd_signal
        and macd_histogram_improving is False
    )

    if "Bullish" in bias and bullish_breakout and score >= STRONG_BUY_SIGNAL_THRESHOLD:
        return "STRONG BUY"
    if "Bullish" in bias and score >= BUY_SIGNAL_THRESHOLD:
        return "BUY"
    if "Bearish" in bias and bearish_breakdown and score >= STRONG_SELL_SIGNAL_THRESHOLD:
        return "STRONG SELL"
    if "Bearish" in bias and bearish_breakdown and score >= SELL_SIGNAL_THRESHOLD:
        return "SELL"
    return "WAIT"


def _trade_quality(
    signal: str,
    score: int,
    market_type: str,
    missing_data: tuple[str, ...],
) -> str:
    if signal == "AVOID" or market_type == "Choppy / Avoid":
        return "Avoid"
    if signal == "WAIT":
        return "No Clean Setup"
    if missing_data:
        return "No Clean Setup"
    if score >= 85:
        return "A+ Setup"
    if score >= 70:
        return "Good Setup"
    if score >= 55:
        return "Average Setup"
    if score >= 40:
        return "Weak Setup"
    return "No Clean Setup"


def _action(signal: str, bias: str) -> str:
    if signal in ("STRONG BUY", "BUY"):
        return "Watch bullish confirmation"
    if signal in ("STRONG SELL", "SELL"):
        return "Watch bearish confirmation"
    if signal == "AVOID":
        return "Avoid until conditions improve"
    if "Bullish" in bias:
        return "Wait for bullish confirmation"
    if "Bearish" in bias:
        return "Wait for confirmation"
    return "Wait for range breakout"


def _waiting_for(signal: str, bias: str) -> tuple[str, ...]:
    if signal != "WAIT":
        return (
            "Confirm price holds the trigger level after candle close.",
            "Avoid action if momentum or volume confirmation disappears.",
        )
    if "Bearish" in bias:
        return (
            "Bullish confirmation if price closes above SMA50 and RSI recovers above 50.",
            "Bearish confirmation if price breaks support and MACD continues lower.",
        )
    if "Bullish" in bias:
        return (
            "Bullish confirmation if price breaks resistance with momentum confirmation.",
            "Bearish warning if price loses SMA50 or breaks support.",
        )
    return (
        "Wait for price to break out of the current range.",
        "Avoid trading while indicators conflict.",
    )


def _plain_english(
    *,
    symbol: str,
    signal: str,
    bias: str,
    market_type: str,
    atr_status: str,
) -> str:
    if market_type == "Volatility Squeeze":
        return (
            f"{symbol} is in a low-volatility squeeze. Avoid guessing direction. "
            "Wait for a confirmed breakout or breakdown."
        )
    if signal == "WAIT" and bias == "Slight Bearish" and atr_status == "Low":
        return (
            f"{symbol} is slightly bearish, but volatility is compressed. "
            "This is not a clean entry yet. Wait for either recovery above SMA50 "
            "or breakdown below support."
        )
    if signal == "WAIT" and "Bearish" in bias:
        return (
            f"{symbol} is showing weakness, but there is no clean entry yet. "
            "Price is below key moving averages and momentum is bearish. "
            "Wait for confirmation before taking action."
        )
    if signal == "WAIT" and "Bullish" in bias:
        return (
            f"{symbol} is showing improving conditions, but confirmation is incomplete. "
            "Wait for resistance break or stronger momentum before taking action."
        )
    if signal == "AVOID":
        return f"{symbol} has unclear or incomplete conditions. Avoid forcing a trade."
    return f"{symbol} has a rule-based {signal} condition, but risk controls still apply."


def _final_decision(signal: str, bias: str, market_type: str) -> str:
    if signal == "AVOID":
        return "AVOID. Market is choppy, incomplete, or indicators conflict."
    if signal == "WAIT":
        if market_type == "Volatility Squeeze":
            return "WAIT. Volatility is compressed; wait for confirmed direction."
        if "Bearish" in bias:
            return "WAIT. Trend is weak and confirmation is incomplete."
        if "Bullish" in bias:
            return "WAIT. Bullish setup is forming, but needs confirmation."
        return "WAIT. No clean trade setup yet."
    return f"{signal}. Rule-based setup detected; confirm risk and structure first."


def _reasons(
    *,
    current_price: Decimal | None,
    snapshot: IndicatorSnapshot,
    trend_status: str,
    macd_status: str,
    atr_status: str,
    missing_data: tuple[str, ...],
) -> tuple[str, ...]:
    reasons = []
    if snapshot.sma is not None and current_price is not None:
        relation = "below" if current_price < snapshot.sma else "above"
        reasons.append(f"Price is {relation} SMA50.")
    reasons.append(f"Trend status is {trend_status}.")
    reasons.append(f"MACD momentum is {macd_status.lower()}.")
    reasons.append(f"ATR risk is {atr_status.lower()}.")
    if missing_data:
        reasons.append("Insufficient candle history for full confirmation.")
    return tuple(reasons[:5])


def _risk_guide(atr_status: str, squeeze: str) -> tuple[str, ...]:
    guide = [
        "ATR measures volatility, not direction.",
        "Low ATR means volatility is compressed. It does not automatically mean safe trade.",
    ]
    if squeeze in ("Yes", "Possible"):
        guide.append("Low ATR plus low Bollinger Band Width may indicate a possible volatility squeeze.")
    guide.append("Suggested stop planning can use 1.5x ATR to 2.0x ATR, but final stop should be based on structure/invalidation.")
    if atr_status == "High":
        guide.append("High ATR means wider movement; reduce size or wait if risk is too wide.")
    return tuple(guide)


def _price_vs_average(price: Decimal | None, average: Decimal | None) -> str:
    if price is None or average is None:
        return "Unavailable"
    if price > average:
        return "Above"
    if price < average:
        return "Below"
    return "At"


def _trend_status(price: Decimal | None, ema: Decimal | None, sma: Decimal | None) -> str:
    if price is None or ema is None or sma is None:
        return "Unavailable"
    if price > ema > sma:
        return "Bullish"
    if price < ema < sma:
        return "Bearish"
    return "Mixed"


def _rsi_status(rsi: Decimal | None) -> str:
    if rsi is None:
        return "Unavailable"
    if rsi >= Decimal("70"):
        return "Overbought"
    if rsi > RSI_BULLISH_THRESHOLD:
        return "Bullish"
    if rsi <= Decimal("30"):
        return "Oversold"
    if rsi < RSI_BEARISH_THRESHOLD:
        return "Neutral-bearish"
    return "Neutral"


def _macd_status(macd: Decimal | None, signal: Decimal | None) -> str:
    if macd is None or signal is None:
        return "Unavailable"
    if macd > signal:
        return "Bullish"
    if macd < signal:
        return "Bearish"
    return "Neutral"


def _macd_histogram_status(histogram: Decimal | None) -> str:
    if histogram is None:
        return "Unavailable"
    if histogram > 0:
        return "Positive"
    if histogram < 0:
        return "Negative"
    return "Flat"


def _atr_status(atr_percent: Decimal | None) -> str:
    if atr_percent is None:
        return "Unavailable"
    if atr_percent < ATR_LOW_THRESHOLD_PERCENT:
        return "Low"
    if atr_percent > ATR_HIGH_THRESHOLD_PERCENT:
        return "High"
    return "Normal"


def _bollinger_price_location(price: Decimal | None, snapshot: IndicatorSnapshot) -> str:
    if (
        price is None
        or snapshot.bollinger_upper is None
        or snapshot.bollinger_middle is None
        or snapshot.bollinger_lower is None
    ):
        return "Unavailable"
    if price > snapshot.bollinger_upper:
        return "Above Upper Band"
    if price < snapshot.bollinger_lower:
        return "Below Lower Band"
    upper_mid = (snapshot.bollinger_upper + snapshot.bollinger_middle) / Decimal("2")
    lower_mid = (snapshot.bollinger_lower + snapshot.bollinger_middle) / Decimal("2")
    if price >= upper_mid:
        return "Upper Half"
    if price <= lower_mid:
        return "Lower Half"
    return "Near Middle Band"


def _bollinger_band_width(snapshot: IndicatorSnapshot) -> Decimal | None:
    if (
        snapshot.bollinger_upper is None
        or snapshot.bollinger_middle is None
        or snapshot.bollinger_lower is None
        or snapshot.bollinger_middle == 0
    ):
        return None
    return ((snapshot.bollinger_upper - snapshot.bollinger_lower) / snapshot.bollinger_middle) * Decimal("100")


def _bollinger_band_width_status(closes: Sequence[Decimal]) -> str:
    band_series = calculate_bollinger_band_series(closes)
    widths = []
    for upper, middle, lower in band_series:
        if upper is None or middle is None or lower is None or middle == 0:
            continue
        widths.append(((upper - lower) / middle) * Decimal("100"))
    if len(widths) < 2:
        return "Unavailable"
    latest = widths[-1]
    previous = widths[-2]
    change = latest - previous
    if abs(change) <= BOLLINGER_FLAT_THRESHOLD_PERCENT:
        return "Flat"
    if change > 0:
        return "Expanding"
    return "Contracting"


def _bollinger_squeeze(atr_percent: Decimal | None, band_width: Decimal | None) -> str:
    if atr_percent is None or band_width is None:
        return "Unavailable"
    if band_width <= BOLLINGER_SQUEEZE_THRESHOLD_PERCENT and atr_percent < ATR_LOW_THRESHOLD_PERCENT:
        return "Yes"
    if band_width <= BOLLINGER_SQUEEZE_THRESHOLD_PERCENT * Decimal("1.5"):
        return "Possible"
    return "No"


def _bollinger_reversal_risk(price_location: str) -> str:
    if price_location in ("Above Upper Band", "Below Lower Band"):
        return "High"
    if price_location in ("Upper Half", "Lower Half"):
        return "Medium"
    if price_location == "Unavailable":
        return "Unavailable"
    return "Low"


def _support_resistance(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
) -> tuple[Decimal | None, Decimal | None]:
    if not highs or not lows:
        return None, None
    recent_highs = highs[-SUPPORT_RESISTANCE_LOOKBACK_CANDLES:]
    recent_lows = lows[-SUPPORT_RESISTANCE_LOOKBACK_CANDLES:]
    return min(recent_lows), max(recent_highs)


def _bullish_trigger(resistance: Decimal | None) -> str:
    if resistance is None:
        return "Unavailable until support/resistance is available."
    return (
        "Price closes above SMA50 and Bollinger middle band with RSI above 50; "
        f"stronger trigger above resistance {resistance} with RSI above 55 and improving MACD histogram."
    )


def _bearish_trigger(support: Decimal | None) -> str:
    if support is None:
        return "Unavailable until support/resistance is available."
    return (
        f"Price breaks support {support} and MACD histogram continues lower; "
        "stronger trigger if price closes below support with RSI below 45 and bearish MACD."
    )


def _macd_histogram_improving(closes: Sequence[Decimal]) -> bool | None:
    macd_values = calculate_macd_series(closes)
    histograms = [histogram for _, _, histogram in macd_values if histogram is not None]
    if len(histograms) < 2:
        return None
    return histograms[-1] > histograms[-2]


def _conflicting_indicators(price: Decimal | None, snapshot: IndicatorSnapshot) -> bool:
    bullish = 0
    bearish = 0
    if price is not None and snapshot.sma is not None:
        bullish += int(price > snapshot.sma)
        bearish += int(price < snapshot.sma)
    if snapshot.rsi is not None:
        bullish += int(snapshot.rsi > RSI_BULLISH_THRESHOLD)
        bearish += int(snapshot.rsi < RSI_BEARISH_THRESHOLD)
    if snapshot.macd is not None and snapshot.macd_signal is not None:
        bullish += int(snapshot.macd > snapshot.macd_signal)
        bearish += int(snapshot.macd < snapshot.macd_signal)
    return bullish >= 2 and bearish >= 2


def _ratio_percent_delta(value: Decimal | None, base: Decimal | None) -> Decimal | None:
    if value is None or base is None or base == 0:
        return None
    return ((value - base) / base) * Decimal("100")


def _multiple(value: Decimal | None, multiplier: Decimal) -> Decimal | None:
    if value is None:
        return None
    return value * multiplier
