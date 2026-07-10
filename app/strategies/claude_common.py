"""Shared incremental math for Claude deterministic 1-minute strategies.

These helpers keep O(1) per-candle state so year-long 1-minute backtests stay
fast. Dry-run advisory use only. No AI calls, no exchange access, no orders.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
import math


@dataclass(frozen=True)
class ClaudeDecision:
    action: str
    reason: str
    price: Decimal | None = None
    fraction: Decimal | None = None
    """For SIMULATED_BUY: fraction of currently available USDT to spend.

    None means spend it all (the pre-existing all-in behavior).
    """


class RunningEma:
    """Incremental EMA seeded with an SMA of the first `period` values."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.multiplier = Decimal("2") / Decimal(period + 1)
        self._seed: list[Decimal] = []
        self.value: Decimal | None = None
        self.previous: Decimal | None = None

    def update(self, close: Decimal) -> Decimal | None:
        self.previous = self.value
        if self.value is None:
            self._seed.append(close)
            if len(self._seed) >= self.period:
                self.value = sum(self._seed, Decimal("0")) / Decimal(self.period)
                self._seed.clear()
            return self.value
        self.value = (close - self.value) * self.multiplier + self.value
        return self.value


class WilderAtr:
    """Incremental Wilder-smoothed ATR."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._seed: list[Decimal] = []
        self.previous_close: Decimal | None = None
        self.value: Decimal | None = None

    def update(self, high: Decimal, low: Decimal, close: Decimal) -> Decimal | None:
        if self.previous_close is None:
            true_range = high - low
        else:
            true_range = max(
                high - low,
                abs(high - self.previous_close),
                abs(low - self.previous_close),
            )
        self.previous_close = close
        if self.value is None:
            self._seed.append(true_range)
            if len(self._seed) >= self.period:
                self.value = sum(self._seed, Decimal("0")) / Decimal(self.period)
                self._seed.clear()
            return self.value
        self.value = (self.value * Decimal(self.period - 1) + true_range) / Decimal(self.period)
        return self.value


class RollingStats:
    """Rolling mean/stddev over a fixed window with O(1) updates."""

    def __init__(self, window: int) -> None:
        self.window = window
        self._values: deque[Decimal] = deque()
        self._sum = Decimal("0")
        self._sum_squares = Decimal("0")

    def push(self, value: Decimal) -> None:
        self._values.append(value)
        self._sum += value
        self._sum_squares += value * value
        if len(self._values) > self.window:
            removed = self._values.popleft()
            self._sum -= removed
            self._sum_squares -= removed * removed

    @property
    def is_full(self) -> bool:
        return len(self._values) >= self.window

    def z_score(self, value: Decimal) -> Decimal | None:
        if not self.is_full:
            return None
        count = Decimal(len(self._values))
        mean = self._sum / count
        variance = (self._sum_squares / count) - (mean * mean)
        if variance <= 0:
            return None
        stddev = Decimal(str(math.sqrt(float(variance))))
        if stddev == 0:
            return None
        return (value - mean) / stddev

    def mean(self) -> Decimal | None:
        if not self._values:
            return None
        return self._sum / Decimal(len(self._values))

    def stddev(self) -> Decimal | None:
        if not self._values:
            return None
        count = Decimal(len(self._values))
        mean = self._sum / count
        variance = (self._sum_squares / count) - (mean * mean)
        if variance <= 0:
            return Decimal("0")
        return Decimal(str(math.sqrt(float(variance))))


class RunningRsi:
    """Incremental Wilder RSI."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self.previous_close: Decimal | None = None
        self._seed_gains: list[Decimal] = []
        self._seed_losses: list[Decimal] = []
        self._average_gain: Decimal | None = None
        self._average_loss: Decimal | None = None
        self.value: Decimal | None = None

    def update(self, close: Decimal) -> Decimal | None:
        if self.previous_close is None:
            self.previous_close = close
            return None
        change = close - self.previous_close
        self.previous_close = close
        gain = change if change > 0 else Decimal("0")
        loss = -change if change < 0 else Decimal("0")
        if self._average_gain is None or self._average_loss is None:
            self._seed_gains.append(gain)
            self._seed_losses.append(loss)
            if len(self._seed_gains) < self.period:
                return None
            self._average_gain = sum(self._seed_gains, Decimal("0")) / Decimal(self.period)
            self._average_loss = sum(self._seed_losses, Decimal("0")) / Decimal(self.period)
            self._seed_gains.clear()
            self._seed_losses.clear()
        else:
            self._average_gain = (self._average_gain * Decimal(self.period - 1) + gain) / Decimal(self.period)
            self._average_loss = (self._average_loss * Decimal(self.period - 1) + loss) / Decimal(self.period)
        if self._average_loss == 0:
            self.value = Decimal("100")
        else:
            relative_strength = self._average_gain / self._average_loss
            self.value = Decimal("100") - (Decimal("100") / (Decimal("1") + relative_strength))
        return self.value


class RollingSum:
    """Rolling sum over a fixed window with O(1) updates."""

    def __init__(self, window: int) -> None:
        self.window = window
        self._values: deque[int] = deque()
        self.total = 0

    def push(self, value: int) -> None:
        self._values.append(value)
        self.total += value
        if len(self._values) > self.window:
            self.total -= self._values.popleft()


class RollingFraction:
    """Rolling fraction of truthy pushes over a fixed window.

    Used to answer "what fraction of the last N candles were X" (e.g. what
    fraction of the trailing two years were spent in a BEAR regime) with
    O(1) updates instead of rescanning history on every tick.
    """

    def __init__(self, window: int) -> None:
        self.window = window
        self._values: deque[bool] = deque()
        self._true_count = 0

    def push(self, flag: bool) -> None:
        self._values.append(flag)
        if flag:
            self._true_count += 1
        if len(self._values) > self.window:
            removed = self._values.popleft()
            if removed:
                self._true_count -= 1

    @property
    def fraction(self) -> Decimal | None:
        if not self._values:
            return None
        return Decimal(self._true_count) / Decimal(len(self._values))


class DonchianRegime:
    """Donchian-style market regime from new-high vs new-low frequency.

    Counts how often price sets a new rolling-window high vs a new rolling-
    window low. A coin printing mostly new highs is in a BULL regime, mostly
    new lows BEAR, otherwise NEUTRAL. Validated on the 1-year 1h dataset:
    the high/low event ratio ranked all five watchlist coins in exact order
    of yearly performance.
    """

    def __init__(self, level_window: int = 720, event_window: int = 720) -> None:
        self._highs = RollingMax(level_window)
        self._lows = RollingMax(level_window)
        self._high_events = RollingSum(event_window)
        self._low_events = RollingSum(event_window)
        self.state: str | None = None
        self.score: Decimal | None = None

    MIN_EVENTS = 5
    BULL_THRESHOLD = Decimal("0.65")
    BEAR_THRESHOLD = Decimal("0.35")

    def update(self, high: Decimal, low: Decimal) -> str | None:
        previous_high = self._highs.maximum if self._highs.is_full else None
        previous_low = self._lows.maximum if self._lows.is_full else None
        new_high = previous_high is not None and high > previous_high
        new_low = previous_low is not None and -low > previous_low
        self._highs.push(high)
        self._lows.push(-low)
        if previous_high is None:
            self.state = None
            self.score = None
            return None
        self._high_events.push(1 if new_high else 0)
        self._low_events.push(1 if new_low else 0)
        highs = self._high_events.total
        lows = self._low_events.total
        if highs + lows < self.MIN_EVENTS:
            self.state = "NEUTRAL"
            self.score = None
            return self.state
        self.score = Decimal(highs) / Decimal(highs + lows)
        if self.score >= self.BULL_THRESHOLD:
            self.state = "BULL"
        elif self.score <= self.BEAR_THRESHOLD:
            self.state = "BEAR"
        else:
            self.state = "NEUTRAL"
        return self.state


class RollingMax:
    """Rolling maximum over a fixed window using a monotonic deque."""

    def __init__(self, window: int) -> None:
        self.window = window
        self._entries: deque[tuple[int, Decimal]] = deque()
        self._count = 0

    def push(self, value: Decimal) -> None:
        while self._entries and self._entries[-1][1] <= value:
            self._entries.pop()
        self._entries.append((self._count, value))
        self._count += 1
        expire_before = self._count - self.window
        while self._entries and self._entries[0][0] < expire_before:
            self._entries.popleft()

    @property
    def is_full(self) -> bool:
        return self._count >= self.window

    @property
    def maximum(self) -> Decimal | None:
        if not self._entries:
            return None
        return self._entries[0][1]

    @property
    def maximum_age(self) -> int | None:
        """Candles since the current maximum was set."""
        if not self._entries:
            return None
        return self._count - 1 - self._entries[0][0]


class RollingVwap:
    """Rolling volume-weighted average price and deviation bands.

    Crypto trades 24/7 with no session open, so this uses a fixed rolling
    window (e.g. 24 candles on 1h data) instead of a session reset -- the
    practical analogue of "session VWAP" for a market with no natural
    session boundary. Typical price is (high + low + close) / 3.
    """

    def __init__(self, window: int) -> None:
        self.window = window
        self._entries: deque[tuple[Decimal, Decimal]] = deque()
        self._sum_pv = Decimal("0")
        self._sum_v = Decimal("0")
        self._sum_pv2 = Decimal("0")

    def update(self, *, high: Decimal, low: Decimal, close: Decimal, volume: Decimal) -> Decimal | None:
        typical_price = (high + low + close) / Decimal("3")
        self._entries.append((typical_price, volume))
        self._sum_pv += typical_price * volume
        self._sum_v += volume
        self._sum_pv2 += volume * typical_price * typical_price
        if len(self._entries) > self.window:
            old_price, old_volume = self._entries.popleft()
            self._sum_pv -= old_price * old_volume
            self._sum_v -= old_volume
            self._sum_pv2 -= old_volume * old_price * old_price
        return self.vwap

    @property
    def is_full(self) -> bool:
        return len(self._entries) >= self.window

    @property
    def vwap(self) -> Decimal | None:
        if self._sum_v <= 0:
            return None
        return self._sum_pv / self._sum_v

    @property
    def deviation(self) -> Decimal | None:
        """Volume-weighted standard deviation of typical price from VWAP."""

        if self._sum_v <= 0:
            return None
        mean = self._sum_pv / self._sum_v
        mean_of_squares = self._sum_pv2 / self._sum_v
        variance = mean_of_squares - (mean * mean)
        if variance <= 0:
            return Decimal("0")
        return Decimal(str(math.sqrt(float(variance))))


class SwingPointDetector:
    """Confirms swing highs and swing lows using a symmetric fractal window.

    A candle `window` positions back is confirmed as a swing high once
    `window` further candles have all had a lower high (and the `window`
    candles before it already did, by construction). This is how swing
    points are actually read on a chart -- a peak is only known once price
    has moved away from it -- so confirmation always lags by `window`
    candles.
    """

    def __init__(self, window: int) -> None:
        self.window = window
        self._highs: deque[Decimal] = deque()
        self._lows: deque[Decimal] = deque()

    def update(self, *, high: Decimal, low: Decimal) -> tuple[Decimal | None, Decimal | None]:
        """Returns (confirmed_swing_high, confirmed_swing_low) for this tick, or (None, None)."""

        self._highs.append(high)
        self._lows.append(low)
        max_length = (2 * self.window) + 1
        if len(self._highs) > max_length:
            self._highs.popleft()
            self._lows.popleft()

        if len(self._highs) < max_length:
            return None, None

        mid = self.window
        candidate_high = self._highs[mid]
        candidate_low = self._lows[mid]
        is_swing_high = all(
            self._highs[i] <= candidate_high for i in range(max_length) if i != mid
        )
        is_swing_low = all(
            self._lows[i] >= candidate_low for i in range(max_length) if i != mid
        )
        return (candidate_high if is_swing_high else None), (candidate_low if is_swing_low else None)
