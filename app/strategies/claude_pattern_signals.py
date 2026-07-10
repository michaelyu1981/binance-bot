"""Deterministic chart-pattern detection: inverse head-and-shoulders and
false-breakout (bear-trap) recognition, built on confirmed swing points.

Long-only patterns only, matching this project's spot-only constraint: a
bearish (topping) head-and-shoulders is tracked only as a reason to avoid a
new entry, never as a short signal. All detection is retrospective by
design -- a swing point is only confirmed once price has moved away from
it, the same way a human reads a chart. Dry-run only. It must not execute
live orders, call exchange clients, or access Binance order endpoints.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal

from app.strategies.claude_common import SwingPointDetector


SWING_WINDOW_CANDLES = 3
SHOULDER_SYMMETRY_TOLERANCE = Decimal("0.15")  # shoulders within 15% of each other
FALSE_BREAKOUT_LOOKBACK_CANDLES = 10
FALSE_BREAKOUT_RECLAIM_CANDLES = 5


@dataclass(frozen=True)
class PatternSignal:
    name: str
    bullish: bool
    detail: str


class InverseHeadAndShouldersDetector:
    """Tracks the last three confirmed swing lows for an inverse H&S (bottom).

    Confirms a bullish reversal when: the middle low (the head) is lower
    than both flanking lows (the shoulders), the shoulders are roughly
    symmetric, and price closes back above the neckline (the higher of the
    two swing highs between the shoulders and the head) after the right
    shoulder forms.
    """

    def __init__(self, swing_window: int = SWING_WINDOW_CANDLES) -> None:
        self._swings = SwingPointDetector(swing_window)
        self._recent_lows: deque[Decimal] = deque(maxlen=3)
        self._recent_highs_between: deque[Decimal] = deque(maxlen=2)
        self._pending_neckline: Decimal | None = None
        self._awaiting_breakout = False
        self._current_high_since_last_low = Decimal("0")

    def update(self, *, high: Decimal, low: Decimal, close: Decimal) -> PatternSignal | None:
        swing_high, swing_low = self._swings.update(high=high, low=low)

        if swing_high is not None:
            self._current_high_since_last_low = max(self._current_high_since_last_low, swing_high)

        if swing_low is not None:
            if self._recent_lows:
                self._recent_highs_between.append(self._current_high_since_last_low)
            self._current_high_since_last_low = Decimal("0")
            self._recent_lows.append(swing_low)

            if len(self._recent_lows) == 3 and len(self._recent_highs_between) == 2:
                left, head, right = self._recent_lows
                if head < left and head < right:
                    shoulder_diff = abs(left - right) / head if head > 0 else Decimal("1")
                    if shoulder_diff <= SHOULDER_SYMMETRY_TOLERANCE:
                        self._pending_neckline = max(self._recent_highs_between)
                        self._awaiting_breakout = True

        if self._awaiting_breakout and self._pending_neckline is not None and close > self._pending_neckline:
            neckline = self._pending_neckline
            self._awaiting_breakout = False
            self._pending_neckline = None
            return PatternSignal(
                name="inverse_head_and_shoulders",
                bullish=True,
                detail=f"Inverse head-and-shoulders confirmed: close {close} broke the {neckline} neckline.",
            )
        return None


class FalseBreakoutDetector:
    """Detects a false breakdown (a 'bear trap' / Wyckoff spring).

    Price breaks below a recent swing low, then reclaims it within a short
    window -- a classic sign that the breakdown had no real conviction
    behind it and often precedes a reversal up.
    """

    def __init__(self, swing_window: int = SWING_WINDOW_CANDLES) -> None:
        self._swings = SwingPointDetector(swing_window)
        self._last_swing_low: Decimal | None = None
        self._broke_below_at: int | None = None
        self._candle_index = -1

    def update(self, *, high: Decimal, low: Decimal, close: Decimal) -> PatternSignal | None:
        self._candle_index += 1
        _, swing_low = self._swings.update(high=high, low=low)
        if swing_low is not None:
            self._last_swing_low = swing_low
            self._broke_below_at = None

        if self._last_swing_low is None:
            return None

        if self._broke_below_at is None:
            if low < self._last_swing_low:
                self._broke_below_at = self._candle_index
            return None

        candles_since_break = self._candle_index - self._broke_below_at
        if candles_since_break > FALSE_BREAKOUT_RECLAIM_CANDLES:
            self._broke_below_at = None
            return None

        if close > self._last_swing_low:
            level = self._last_swing_low
            self._broke_below_at = None
            self._last_swing_low = None
            return PatternSignal(
                name="false_breakdown",
                bullish=True,
                detail=f"False breakdown: price broke below {level} and reclaimed it within {candles_since_break} candles.",
            )
        return None
