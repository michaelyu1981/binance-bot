"""Qualifying-reading checks for Telegram enrichment and the standing report.

Evaluates already-computed technical signal guides against fixed extreme
thresholds (RSI overbought/oversold, price at a Bollinger band, a confirmed
Bollinger squeeze) so alerts only mention conditions that actually qualify.
Uses local public candle data only. It must not place orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.logger import format_price_usd
from app.signals import TechnicalSignalGuide


RSI_OVERBOUGHT = Decimal("80")
RSI_OVERSOLD = Decimal("20")
BOLLINGER_TOUCH_LOCATIONS = ("Above Upper Band", "Below Lower Band")
SQUEEZE_STATES = ("Yes",)


@dataclass(frozen=True)
class QualifyingReading:
    symbol: str
    interval: str
    label: str


def evaluate_qualifying_readings(
    *,
    symbol: str,
    interval: str,
    guide: TechnicalSignalGuide,
) -> tuple[QualifyingReading, ...]:
    """Return only the readings that currently cross an extreme threshold."""

    readings: list[QualifyingReading] = []

    price_text = f" (price {format_price_usd(guide.current_price)})" if guide.current_price is not None else ""

    if guide.rsi14 is not None:
        if guide.rsi14 >= RSI_OVERBOUGHT:
            readings.append(
                QualifyingReading(
                    symbol=symbol,
                    interval=interval,
                    label=f"{symbol} {interval} RSI {guide.rsi14:.1f} Overbought (>= {RSI_OVERBOUGHT}){price_text}",
                )
            )
        elif guide.rsi14 <= RSI_OVERSOLD:
            readings.append(
                QualifyingReading(
                    symbol=symbol,
                    interval=interval,
                    label=f"{symbol} {interval} RSI {guide.rsi14:.1f} Oversold (<= {RSI_OVERSOLD}){price_text}",
                )
            )

    if guide.bollinger_price_location in BOLLINGER_TOUCH_LOCATIONS:
        readings.append(
            QualifyingReading(
                symbol=symbol,
                interval=interval,
                label=f"{symbol} {interval} {guide.bollinger_price_location}{price_text}",
            )
        )

    if guide.bollinger_squeeze in SQUEEZE_STATES:
        width = guide.bollinger_band_width_percent
        width_text = f" (width {width:.2f}%)" if width is not None else ""
        readings.append(
            QualifyingReading(
                symbol=symbol,
                interval=interval,
                label=f"{symbol} {interval} Bollinger squeeze{width_text} - watch for a breakout{price_text}",
            )
        )
        if guide.bollinger_upper is not None and guide.bollinger_lower is not None and guide.current_price is not None:
            readings.append(
                QualifyingReading(
                    symbol=symbol,
                    interval=interval,
                    label=(
                        f"{symbol} {interval} squeeze range: high {format_price_usd(guide.bollinger_upper)} / "
                        f"low {format_price_usd(guide.bollinger_lower)} / current {format_price_usd(guide.current_price)}"
                    ),
                )
            )

    return tuple(readings)


def format_qualifying_readings(readings: tuple[QualifyingReading, ...]) -> tuple[str, ...]:
    return tuple(reading.label for reading in readings)
