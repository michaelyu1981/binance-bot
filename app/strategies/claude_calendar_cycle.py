"""Calendar and cycle scoring: documented behavioral-finance calendar effects
blended with empirically measured volume seasonality from this project's own
2-year candle history.

Ghost Month dates come from the Chinese lunar calendar (the 7th lunar
month); academic research on Asian equity markets documents lower trading
volume and mixed volatility/return effects during this period, plausible
here given crypto's heavy Asian retail participation. The hour-of-day and
day-of-week favorability multipliers below were measured directly from this
project's own 2-year, 5-symbol, 1h candle history (see the session notes),
not assumed. Dry-run only, advisory scoring -- it must not place orders.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal


# Ghost Month = the 7th lunar month. Dates shift on the Gregorian calendar
# each year; this table needs a manual entry added for years beyond it.
# Source: Chinese lunar calendar / Hungry Ghost Festival calendar listings.
GHOST_MONTH_RANGES = (
    (date(2024, 8, 4), date(2024, 9, 2)),
    (date(2025, 8, 13), date(2025, 9, 10)),
    (date(2026, 8, 13), date(2026, 9, 10)),
)

# Measured on this project's own 2-year, 5-symbol, 1h candle history:
# average quote-volume ratio to that symbol's own mean, by UTC hour. Peaks
# 13:00-17:00 UTC (US session open), troughs 22:00-05:00 UTC.
HOUR_VOLUME_RATIO = {
    0: Decimal("1.007"), 1: Decimal("0.971"), 2: Decimal("0.901"), 3: Decimal("0.832"),
    4: Decimal("0.805"), 5: Decimal("0.786"), 6: Decimal("0.872"), 7: Decimal("0.938"),
    8: Decimal("0.939"), 9: Decimal("0.902"), 10: Decimal("0.888"), 11: Decimal("0.915"),
    12: Decimal("1.033"), 13: Decimal("1.273"), 14: Decimal("1.558"), 15: Decimal("1.503"),
    16: Decimal("1.346"), 17: Decimal("1.220"), 18: Decimal("1.040"), 19: Decimal("0.944"),
    20: Decimal("0.909"), 21: Decimal("0.845"), 22: Decimal("0.806"), 23: Decimal("0.766"),
}

# Measured the same way, by UTC day of week (0=Monday .. 6=Sunday). Monday
# is strongest; weekend volume is genuinely lower even in a 24/7 market.
DAY_OF_WEEK_VOLUME_RATIO = {
    0: Decimal("1.198"), 1: Decimal("1.095"), 2: Decimal("0.996"), 3: Decimal("1.013"),
    4: Decimal("1.073"), 5: Decimal("0.766"), 6: Decimal("0.859"),
}


def is_ghost_month(moment: datetime) -> bool:
    moment_date = moment.astimezone(timezone.utc).date()
    return any(start <= moment_date <= end for start, end in GHOST_MONTH_RANGES)


def calendar_cycle_score(open_time_ms: int) -> Decimal:
    """A -2..+2 favorability score blending hour-of-day, day-of-week, and Ghost Month.

    Positive means historically higher participation / more conviction
    behind a move; negative means thinner, less reliable conditions.
    """

    moment = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
    hour_ratio = HOUR_VOLUME_RATIO[moment.hour]
    day_ratio = DAY_OF_WEEK_VOLUME_RATIO[moment.weekday()]

    score = Decimal("0")
    score += (hour_ratio - Decimal("1")) * Decimal("2")  # scale the ~0.77-1.56 range to roughly -0.5..+1.1
    score += (day_ratio - Decimal("1")) * Decimal("2")   # scale the ~0.77-1.20 range to roughly -0.5..+0.4
    if is_ghost_month(moment):
        score -= Decimal("1")
    return score
