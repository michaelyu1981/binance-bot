"""Technical indicators calculated from stored public candle data.

These calculations are advisory display helpers only. They do not access
Binance account data and must not place or prepare orders.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


RSI_PERIOD = 14
BOLLINGER_PERIOD = 20
BOLLINGER_STDDEV_MULTIPLIER = Decimal("2")
SMA_PERIOD = 50
EMA_PERIOD = 20
MACD_FAST_PERIOD = 12
MACD_SLOW_PERIOD = 26
MACD_SIGNAL_PERIOD = 9
VOLUME_AVERAGE_PERIOD = 20
ATR_PERIOD = 14


@dataclass(frozen=True)
class IndicatorSnapshot:
    """Latest indicator values for one symbol and interval."""

    rsi: Decimal | None
    bollinger_upper: Decimal | None
    bollinger_middle: Decimal | None
    bollinger_lower: Decimal | None
    bollinger_percent_b: Decimal | None
    sma: Decimal | None
    ema: Decimal | None
    macd: Decimal | None
    macd_signal: Decimal | None
    macd_histogram: Decimal | None
    volume: Decimal | None
    average_volume: Decimal | None
    volume_ratio: Decimal | None
    atr: Decimal | None
    atr_percent: Decimal | None


def build_indicator_snapshot(
    *,
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    volumes: Sequence[Decimal],
) -> IndicatorSnapshot:
    """Calculate the dashboard indicators from OHLCV candle values."""

    close_values = tuple(closes)
    high_values = tuple(highs)
    low_values = tuple(lows)
    volume_values = tuple(volumes)
    volume = volume_values[-1] if volume_values else None
    average_volume = calculate_sma(volume_values, VOLUME_AVERAGE_PERIOD)
    volume_ratio = _ratio_percent(volume, average_volume)
    atr = calculate_atr(highs=high_values, lows=low_values, closes=close_values)
    return IndicatorSnapshot(
        rsi=calculate_rsi(close_values),
        bollinger_upper=_bollinger_value(close_values, "upper"),
        bollinger_middle=_bollinger_value(close_values, "middle"),
        bollinger_lower=_bollinger_value(close_values, "lower"),
        bollinger_percent_b=_bollinger_percent_b(close_values),
        sma=calculate_sma(close_values, SMA_PERIOD),
        ema=calculate_ema(close_values, EMA_PERIOD),
        macd=_macd_value(close_values, "macd"),
        macd_signal=_macd_value(close_values, "signal"),
        macd_histogram=_macd_value(close_values, "histogram"),
        volume=volume,
        average_volume=average_volume,
        volume_ratio=volume_ratio,
        atr=atr,
        atr_percent=_ratio_percent(atr, close_values[-1] if close_values else None),
    )


def calculate_rsi(closes: Sequence[Decimal], period: int = RSI_PERIOD) -> Decimal | None:
    """Return a simple latest RSI value."""

    if len(closes) < period + 1:
        return None

    gains: list[Decimal] = []
    losses: list[Decimal] = []
    recent_closes = closes[-(period + 1) :]
    for previous, current in zip(recent_closes, recent_closes[1:]):
        change = current - previous
        if change >= 0:
            gains.append(change)
            losses.append(Decimal("0"))
        else:
            gains.append(Decimal("0"))
            losses.append(abs(change))

    average_gain = sum(gains, Decimal("0")) / Decimal(period)
    average_loss = sum(losses, Decimal("0")) / Decimal(period)
    if average_loss == 0:
        return Decimal("100")

    relative_strength = average_gain / average_loss
    return Decimal("100") - (Decimal("100") / (Decimal("1") + relative_strength))


def calculate_sma(closes: Sequence[Decimal], period: int) -> Decimal | None:
    """Return the latest simple moving average."""

    if len(closes) < period:
        return None
    values = closes[-period:]
    return sum(values, Decimal("0")) / Decimal(period)


def calculate_ema(closes: Sequence[Decimal], period: int) -> Decimal | None:
    """Return the latest exponential moving average."""

    values = _ema_series(closes, period)
    if not values:
        return None
    return values[-1]


def calculate_bollinger_bands(
    closes: Sequence[Decimal],
    period: int = BOLLINGER_PERIOD,
    stddev_multiplier: Decimal = BOLLINGER_STDDEV_MULTIPLIER,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Return latest upper, middle, and lower Bollinger Bands."""

    if len(closes) < period:
        return None

    values = closes[-period:]
    middle = sum(values, Decimal("0")) / Decimal(period)
    variance = sum((value - middle) ** 2 for value in values) / Decimal(period)
    stddev = variance.sqrt()
    upper = middle + (stddev * stddev_multiplier)
    lower = middle - (stddev * stddev_multiplier)
    return upper, middle, lower


def calculate_bollinger_band_series(
    closes: Sequence[Decimal],
    period: int = BOLLINGER_PERIOD,
    stddev_multiplier: Decimal = BOLLINGER_STDDEV_MULTIPLIER,
) -> tuple[tuple[Decimal | None, Decimal | None, Decimal | None], ...]:
    """Return upper, middle, lower Bollinger values aligned to close prices."""

    values: list[tuple[Decimal | None, Decimal | None, Decimal | None]] = []
    for index in range(len(closes)):
        if index + 1 < period:
            values.append((None, None, None))
            continue
        bands = calculate_bollinger_bands(
            closes[: index + 1],
            period=period,
            stddev_multiplier=stddev_multiplier,
        )
        values.append(bands if bands is not None else (None, None, None))
    return tuple(values)


def calculate_macd(
    closes: Sequence[Decimal],
    *,
    fast_period: int = MACD_FAST_PERIOD,
    slow_period: int = MACD_SLOW_PERIOD,
    signal_period: int = MACD_SIGNAL_PERIOD,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Return latest MACD line, signal line, and histogram."""

    if len(closes) < slow_period + signal_period:
        return None

    fast_ema_by_index = dict(_ema_series_with_index(closes, fast_period))
    slow_ema_by_index = dict(_ema_series_with_index(closes, slow_period))
    macd_series = tuple(
        fast_ema_by_index[index] - slow_ema
        for index, slow_ema in sorted(slow_ema_by_index.items())
        if index in fast_ema_by_index
    )
    signal_series = _ema_series(macd_series, signal_period)
    if not signal_series:
        return None

    macd = macd_series[-1]
    signal = signal_series[-1]
    return macd, signal, macd - signal


def calculate_atr(
    *,
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    period: int = ATR_PERIOD,
) -> Decimal | None:
    """Return latest average true range."""

    if len(highs) != len(lows) or len(lows) != len(closes):
        return None
    if len(closes) < period + 1:
        return None

    true_ranges: list[Decimal] = []
    start_index = len(closes) - period
    for index in range(start_index, len(closes)):
        high = highs[index]
        low = lows[index]
        previous_close = closes[index - 1]
        true_ranges.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )

    return sum(true_ranges, Decimal("0")) / Decimal(period)


def describe_rsi(value: Decimal | None) -> str:
    if value is None:
        return "Not enough data"
    if value >= Decimal("70"):
        return "Overbought"
    if value <= Decimal("30"):
        return "Oversold"
    return "Neutral"


def describe_bollinger(value: Decimal | None) -> str:
    if value is None:
        return "Not enough data"
    if value >= Decimal("100"):
        return "Above upper band"
    if value <= Decimal("0"):
        return "Below lower band"
    return "Inside bands"


def describe_macd(macd: Decimal | None, signal: Decimal | None) -> str:
    if macd is None or signal is None:
        return "Not enough data"
    if macd > signal:
        return "Bullish"
    if macd < signal:
        return "Bearish"
    return "Neutral"


def describe_average_position(close: Decimal | None, average: Decimal | None) -> str:
    if close is None or average is None:
        return "Not enough data"
    if close > average:
        return "Price above average"
    if close < average:
        return "Price below average"
    return "At average"


def describe_volume(volume_ratio: Decimal | None) -> str:
    if volume_ratio is None:
        return "Not enough data"
    if volume_ratio >= Decimal("150"):
        return "High confirmation"
    if volume_ratio >= Decimal("100"):
        return "Above average"
    return "Below average"


def describe_atr(atr_percent: Decimal | None) -> str:
    if atr_percent is None:
        return "Not enough data"
    if atr_percent >= Decimal("5"):
        return "High risk range"
    if atr_percent >= Decimal("2"):
        return "Medium risk range"
    return "Lower risk range"


def _ema_series(values: Sequence[Decimal], period: int) -> tuple[Decimal, ...]:
    if len(values) < period:
        return ()

    multiplier = Decimal("2") / Decimal(period + 1)
    ema = sum(values[:period], Decimal("0")) / Decimal(period)
    series = [ema]
    for value in values[period:]:
        ema = ((value - ema) * multiplier) + ema
        series.append(ema)
    return tuple(series)


def _ema_series_with_index(values: Sequence[Decimal], period: int) -> tuple[tuple[int, Decimal], ...]:
    series = _ema_series(values, period)
    first_index = period - 1
    return tuple((first_index + offset, value) for offset, value in enumerate(series))


def _bollinger_value(closes: Sequence[Decimal], name: str) -> Decimal | None:
    bands = calculate_bollinger_bands(closes)
    if bands is None:
        return None
    upper, middle, lower = bands
    if name == "upper":
        return upper
    if name == "middle":
        return middle
    return lower


def _bollinger_percent_b(closes: Sequence[Decimal]) -> Decimal | None:
    bands = calculate_bollinger_bands(closes)
    if bands is None:
        return None
    upper, _, lower = bands
    spread = upper - lower
    if spread == 0:
        return None
    return ((closes[-1] - lower) / spread) * Decimal("100")


def _macd_value(closes: Sequence[Decimal], name: str) -> Decimal | None:
    values = calculate_macd(closes)
    if values is None:
        return None
    macd, signal, histogram = values
    if name == "macd":
        return macd
    if name == "signal":
        return signal
    return histogram


def _ratio_percent(value: Decimal | None, base: Decimal | None) -> Decimal | None:
    if value is None or base is None or base == 0:
        return None
    return (value / base) * Decimal("100")
