"""Read-only signal watcher for local public candle data.

This process compares deterministic technical signals and optionally sends
Telegram alerts. It uses stored public candles only and must not place orders.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from app.candle_store import DEFAULT_CANDLE_DB_PATH
from app.config import PUBLIC_MARKET_WATCHLIST
from app.dashboard import CHART_CANDLE_LIMIT, MULTI_TIMEFRAME_SUMMARY_INTERVALS
from app.health import SIGNAL_WATCHER_HEALTH_PATH, write_error_heartbeat, write_success_heartbeat
from app.indicators import build_indicator_snapshot
from app.logger import append_market_price_log, current_timestamp
from app.qualifying_readings import evaluate_qualifying_readings, format_qualifying_readings
from app.signals import (
    MultiTimeframeSignalSummary,
    TechnicalSignalGuide,
    build_multi_timeframe_signal_summary,
    build_technical_signal_guide,
)
from app.telegram_notifier import (
    TelegramSendError,
    load_telegram_config_from_env,
    send_telegram_message,
)


SIGNAL_STATE_PATH = Path("data/signal_state.json")
SIGNAL_LOG_PREFIX = "SIGNAL"
STANDING_REPORT_LOG_PREFIX = "STANDING"
WATCHED_TIMEFRAME_INTERVALS = ("4h", "1d")

# Enrichment reads a wider set of timeframes than the "did something change"
# comparison above, since a qualifying reading (RSI extreme, Bollinger band
# touch, squeeze) is worth reporting even on a timeframe whose overall
# signal didn't change. Enrichment only fires for symbols that already
# triggered an alert from WATCHED_TIMEFRAME_INTERVALS above.
ENRICHMENT_TIMEFRAME_INTERVALS = ("1h", "4h", "1d")

# The watcher's shortest tracked candle is 4h, so checks are aligned to real
# UTC 4-hour boundaries (00:00, 04:00, 08:00, 12:00, 16:00, 20:00) plus a
# small buffer, instead of a fixed sleep interval. A fixed interval drifts
# relative to actual candle closes depending on when the process started; a
# wall-clock-aligned check always reads a complete, just-closed candle.
CANDLE_ALIGNMENT_HOURS = 4
CANDLE_CLOSE_BUFFER_SECONDS = 10


def run_signal_watch_once(
    *,
    state_path: Path = SIGNAL_STATE_PATH,
    send_telegram: bool = True,
) -> int:
    """Compare current signals to previous local state and alert on changes."""

    current_state, guides_by_symbol = build_signal_state()
    previous_state = _read_signal_state(state_path)
    alert_lines, triggered_symbols = _build_alert_lines(previous_state, current_state)
    _write_signal_state(state_path, current_state)

    if not previous_state:
        message = "Signal watcher baseline initialized. No Telegram alert sent."
        print(message)
        append_market_price_log([f"{current_timestamp()} {SIGNAL_LOG_PREFIX}: {message}"])
        return 0

    if not alert_lines:
        message = "Signal watcher checked: no meaningful signal changes."
        print(message)
        append_market_price_log([f"{current_timestamp()} {SIGNAL_LOG_PREFIX}: {message}"])
        return 0

    enrichment_lines = _build_enrichment_lines(triggered_symbols, guides_by_symbol)
    all_lines = list(alert_lines) + list(enrichment_lines)

    timestamp = current_timestamp()
    log_lines = [f"{timestamp} {SIGNAL_LOG_PREFIX}: {line}" for line in all_lines]
    for line in log_lines:
        print(line)
    append_market_price_log(log_lines)

    if send_telegram:
        _send_signal_alert(all_lines)

    return 0


def run_standing_report_once(*, send_telegram: bool = True) -> int:
    """Check every watchlist symbol and report only currently-qualifying readings.

    Unlike run_signal_watch_once, this does not depend on anything having
    changed. It always logs locally, but stays silent on Telegram when no
    symbol currently qualifies, to avoid unconditional recurring noise.
    """

    _, guides_by_symbol = build_signal_state()
    lines: list[str] = []
    for symbol in PUBLIC_MARKET_WATCHLIST:
        guides_by_interval = guides_by_symbol.get(symbol, {})
        for interval in ENRICHMENT_TIMEFRAME_INTERVALS:
            guide = guides_by_interval.get(interval)
            if guide is None:
                continue
            readings = evaluate_qualifying_readings(symbol=symbol, interval=interval, guide=guide)
            lines.extend(format_qualifying_readings(readings))

    if not lines:
        message = "Standing report checked all watchlist symbols: nothing currently qualifies."
        print(message)
        append_market_price_log([f"{current_timestamp()} {STANDING_REPORT_LOG_PREFIX}: {message}"])
        return 0

    timestamp = current_timestamp()
    log_lines = [f"{timestamp} {STANDING_REPORT_LOG_PREFIX}: {line}" for line in lines]
    for line in log_lines:
        print(line)
    append_market_price_log(log_lines)

    if send_telegram:
        _send_standing_report(lines)

    return 0


def _seconds_until_next_candle_close(
    *,
    interval_hours: int = CANDLE_ALIGNMENT_HOURS,
    buffer_seconds: int = CANDLE_CLOSE_BUFFER_SECONDS,
    now: datetime | None = None,
) -> float:
    """Seconds until buffer_seconds after the next UTC interval_hours boundary.

    Unlike a fixed sleep interval, this always lands shortly after a real
    candle close (e.g. 00:00, 04:00, 08:00 UTC for a 4h interval),
    regardless of when the process started.
    """

    current = now or datetime.now(timezone.utc)
    boundary_hour = (current.hour // interval_hours) * interval_hours
    candidate = current.replace(hour=boundary_hour, minute=0, second=0, microsecond=0)
    candidate += timedelta(seconds=buffer_seconds)
    while candidate <= current:
        candidate += timedelta(hours=interval_hours)
    return (candidate - current).total_seconds()


def run_signal_watch_loop(
    *,
    state_path: Path = SIGNAL_STATE_PATH,
    send_telegram: bool = True,
) -> int:
    """Run the signal watcher continuously until Ctrl+C.

    Checks are aligned to real UTC candle-close boundaries (see
    CANDLE_ALIGNMENT_HOURS and CANDLE_CLOSE_BUFFER_SECONDS) rather than a
    fixed sleep interval, so each check reads a complete, just-closed candle.
    """

    heartbeat_interval_seconds = CANDLE_ALIGNMENT_HOURS * 3600 + CANDLE_CLOSE_BUFFER_SECONDS
    print("CoinPilot signal watcher started.")
    print("Safety: public candle data only. No API key. No account access. No orders.")
    print(
        f"Aligned to real UTC {CANDLE_ALIGNMENT_HOURS}-hour candle closes "
        f"(+{CANDLE_CLOSE_BUFFER_SECONDS}s buffer)."
    )
    try:
        while True:
            time.sleep(_seconds_until_next_candle_close())
            try:
                run_signal_watch_once(
                    state_path=state_path,
                    send_telegram=send_telegram,
                )
                run_standing_report_once(send_telegram=send_telegram)
                write_success_heartbeat(
                    path=SIGNAL_WATCHER_HEALTH_PATH,
                    service="signal_watcher",
                    interval_seconds=heartbeat_interval_seconds,
                    details={"symbols": list(PUBLIC_MARKET_WATCHLIST)},
                )
            except Exception as exc:  # noqa: BLE001 - keep watcher alive.
                message = f"{type(exc).__name__}: {exc}"
                print(f"Signal watcher error: {message}")
                write_error_heartbeat(
                    path=SIGNAL_WATCHER_HEALTH_PATH,
                    service="signal_watcher",
                    interval_seconds=heartbeat_interval_seconds,
                    error_message=message,
                )
    except KeyboardInterrupt:
        print("Stopping CoinPilot signal watcher.")
        return 0


def build_signal_state() -> tuple[dict[str, Any], dict[str, dict[str, TechnicalSignalGuide]]]:
    """Build serializable signal state for all watchlist symbols.

    Also returns the full per-symbol, per-interval guides so callers can
    enrich alerts with qualifying-reading checks without recomputing
    indicators a second time.
    """

    state: dict[str, Any] = {"symbols": {}}
    guides_by_symbol: dict[str, dict[str, TechnicalSignalGuide]] = {}
    for symbol in PUBLIC_MARKET_WATCHLIST:
        guides_by_interval = _build_guides_for_symbol(symbol)
        if not guides_by_interval:
            continue
        guides_by_symbol[symbol] = guides_by_interval
        summary = build_multi_timeframe_signal_summary(
            symbol=symbol,
            guides_by_interval=guides_by_interval,
        )
        state["symbols"][symbol] = {
            "overall": _summary_state(summary),
            "timeframes": {
                interval: _guide_state(guide)
                for interval, guide in guides_by_interval.items()
                if interval in WATCHED_TIMEFRAME_INTERVALS
            },
        }
    return state, guides_by_symbol


def _build_guides_for_symbol(symbol: str) -> dict[str, TechnicalSignalGuide]:
    guides = {}
    for interval in MULTI_TIMEFRAME_SUMMARY_INTERVALS:
        candles = _load_candles(symbol=symbol, interval=interval)
        if not candles:
            continue
        highs = tuple(candle["high"] for candle in candles)
        lows = tuple(candle["low"] for candle in candles)
        closes = tuple(candle["close"] for candle in candles)
        volumes = tuple(candle["volume"] for candle in candles)
        snapshot = build_indicator_snapshot(
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
        )
        guides[interval] = build_technical_signal_guide(
            symbol=symbol,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            snapshot=snapshot,
        )
    return guides


def _load_candles(*, symbol: str, interval: str) -> tuple[dict[str, Decimal], ...]:
    if not DEFAULT_CANDLE_DB_PATH.exists():
        return ()
    try:
        connection = sqlite3.connect(DEFAULT_CANDLE_DB_PATH)
        rows = connection.execute(
            """
            SELECT high, low, close, volume
            FROM candles
            WHERE symbol = ? AND interval = ?
            ORDER BY open_time_ms DESC
            LIMIT ?
            """,
            (symbol, interval, CHART_CANDLE_LIMIT),
        ).fetchall()
    except sqlite3.Error:
        return ()
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass

    return tuple(
        {"high": Decimal(row[0]), "low": Decimal(row[1]), "close": Decimal(row[2]), "volume": Decimal(row[3])}
        for row in reversed(rows)
    )


def _summary_state(summary: MultiTimeframeSignalSummary) -> dict[str, Any]:
    return {
        "overall": summary.overall,
        "bias": summary.bias,
        "score": summary.score,
        "alignment": summary.alignment,
        "higher_timeframe_bias": summary.higher_timeframe_bias,
        "short_term_pressure": summary.short_term_pressure,
        "final_decision": summary.final_decision,
    }


def _guide_state(guide: TechnicalSignalGuide) -> dict[str, Any]:
    return {
        "signal": guide.signal,
        "bias": guide.bias,
        "score": guide.score,
        "market_type": guide.market_type,
        "trade_quality": guide.trade_quality,
        "final_decision": guide.final_decision,
    }


def _build_alert_lines(
    previous_state: dict[str, Any],
    current_state: dict[str, Any],
) -> tuple[list[str], set[str]]:
    """Return alert lines plus the set of symbols that triggered at least one."""

    lines: list[str] = []
    triggered_symbols: set[str] = set()
    previous_symbols = previous_state.get("symbols", {}) if isinstance(previous_state, dict) else {}
    current_symbols = current_state.get("symbols", {})
    if not isinstance(previous_symbols, dict) or not isinstance(current_symbols, dict):
        return lines, triggered_symbols

    for symbol, current_symbol_state in current_symbols.items():
        previous_symbol_state = previous_symbols.get(symbol, {})
        if not isinstance(previous_symbol_state, dict):
            continue
        overall_lines = _overall_alert_lines(symbol, previous_symbol_state, current_symbol_state)
        timeframe_lines = _timeframe_alert_lines(symbol, previous_symbol_state, current_symbol_state)
        if overall_lines or timeframe_lines:
            triggered_symbols.add(symbol)
        lines.extend(overall_lines)
        lines.extend(timeframe_lines)
    return lines, triggered_symbols


def _build_enrichment_lines(
    triggered_symbols: set[str],
    guides_by_symbol: dict[str, dict[str, TechnicalSignalGuide]],
) -> tuple[str, ...]:
    """Append qualifying-reading lines only for symbols that already alerted.

    A symbol only gets enrichment if WATCHED_TIMEFRAME_INTERVALS already
    triggered an alert for it this cycle; enrichment itself never fires a
    message on its own.
    """

    lines: list[str] = []
    for symbol in sorted(triggered_symbols):
        guides_by_interval = guides_by_symbol.get(symbol, {})
        for interval in ENRICHMENT_TIMEFRAME_INTERVALS:
            guide = guides_by_interval.get(interval)
            if guide is None:
                continue
            readings = evaluate_qualifying_readings(symbol=symbol, interval=interval, guide=guide)
            lines.extend(format_qualifying_readings(readings))
    return tuple(lines)


def _overall_alert_lines(
    symbol: str,
    previous_symbol_state: dict[str, Any],
    current_symbol_state: dict[str, Any],
) -> list[str]:
    previous = previous_symbol_state.get("overall", {})
    current = current_symbol_state.get("overall", {})
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return []

    watched_fields = ("overall", "bias", "alignment", "higher_timeframe_bias")
    changes = _field_changes(previous, current, watched_fields)
    if not changes:
        return []
    return [
        (
            f"{symbol} Overall changed: {', '.join(changes)}. "
            f"Score {current.get('score', 'n/a')}/100. {current.get('final_decision', '')}"
        )
    ]


def _timeframe_alert_lines(
    symbol: str,
    previous_symbol_state: dict[str, Any],
    current_symbol_state: dict[str, Any],
) -> list[str]:
    previous_timeframes = previous_symbol_state.get("timeframes", {})
    current_timeframes = current_symbol_state.get("timeframes", {})
    if not isinstance(previous_timeframes, dict) or not isinstance(current_timeframes, dict):
        return []

    lines = []
    for interval in WATCHED_TIMEFRAME_INTERVALS:
        previous = previous_timeframes.get(interval, {})
        current = current_timeframes.get(interval, {})
        if not isinstance(previous, dict) or not isinstance(current, dict):
            continue
        changes = _field_changes(previous, current, ("signal", "bias", "market_type"))
        if changes:
            lines.append(
                (
                    f"{symbol} {interval} changed: {', '.join(changes)}. "
                    f"Score {current.get('score', 'n/a')}/100. {current.get('final_decision', '')}"
                )
            )
    return lines


def _field_changes(
    previous: dict[str, Any],
    current: dict[str, Any],
    fields: tuple[str, ...],
) -> list[str]:
    changes = []
    for field in fields:
        previous_value = previous.get(field)
        current_value = current.get(field)
        if previous_value != current_value:
            changes.append(f"{field} {previous_value} -> {current_value}")
    return changes


def _send_signal_alert(alert_lines: list[str]) -> None:
    config = load_telegram_config_from_env()
    if config is None:
        print("Telegram signal alert not sent because Telegram env vars are missing.")
        return

    message = "\n".join(["CoinPilot Signal Alert", "", *alert_lines, "", "Advisory only. No automatic trade."])
    try:
        send_telegram_message(message, config)
    except TelegramSendError as exc:
        print(f"Telegram signal alert failed: {exc}")


def _send_standing_report(lines: list[str]) -> None:
    config = load_telegram_config_from_env()
    if config is None:
        print("Telegram standing report not sent because Telegram env vars are missing.")
        return

    message = "\n".join(["CoinPilot Standing Report", "", *lines, "", "Advisory only. No automatic trade."])
    try:
        send_telegram_message(message, config)
    except TelegramSendError as exc:
        print(f"Telegram standing report failed: {exc}")


def _read_signal_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _write_signal_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": current_timestamp(), **state}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
