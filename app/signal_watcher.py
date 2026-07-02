"""Read-only signal watcher for local public candle data.

This process compares deterministic technical signals and optionally sends
Telegram alerts. It uses stored public candles only and must not place orders.
"""

from __future__ import annotations

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
WATCHED_TIMEFRAME_INTERVALS = ("4h", "1d")


def run_signal_watch_once(
    *,
    state_path: Path = SIGNAL_STATE_PATH,
    send_telegram: bool = True,
) -> int:
    """Compare current signals to previous local state and alert on changes."""

    current_state = build_signal_state()
    previous_state = _read_signal_state(state_path)
    alert_lines = _build_alert_lines(previous_state, current_state)
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

    timestamp = current_timestamp()
    log_lines = [f"{timestamp} {SIGNAL_LOG_PREFIX}: {line}" for line in alert_lines]
    for line in log_lines:
        print(line)
    append_market_price_log(log_lines)

    if send_telegram:
        _send_signal_alert(alert_lines)

    return 0


def run_signal_watch_loop(
    *,
    interval_seconds: int,
    state_path: Path = SIGNAL_STATE_PATH,
    send_telegram: bool = True,
) -> int:
    """Run the signal watcher continuously until Ctrl+C."""

    print("CoinPilot signal watcher started.")
    print("Safety: public candle data only. No API key. No account access. No orders.")
    print(f"Interval: {interval_seconds} seconds")
    try:
        while True:
            try:
                run_signal_watch_once(
                    state_path=state_path,
                    send_telegram=send_telegram,
                )
                write_success_heartbeat(
                    path=SIGNAL_WATCHER_HEALTH_PATH,
                    service="signal_watcher",
                    interval_seconds=interval_seconds,
                    details={"symbols": list(PUBLIC_MARKET_WATCHLIST)},
                )
            except Exception as exc:  # noqa: BLE001 - keep watcher alive.
                message = f"{type(exc).__name__}: {exc}"
                print(f"Signal watcher error: {message}")
                write_error_heartbeat(
                    path=SIGNAL_WATCHER_HEALTH_PATH,
                    service="signal_watcher",
                    interval_seconds=interval_seconds,
                    error_message=message,
                )
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("Stopping CoinPilot signal watcher.")
        return 0


def build_signal_state() -> dict[str, Any]:
    """Build serializable signal state for all watchlist symbols."""

    state: dict[str, Any] = {"symbols": {}}
    for symbol in PUBLIC_MARKET_WATCHLIST:
        guides_by_interval = _build_guides_for_symbol(symbol)
        if not guides_by_interval:
            continue
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
    return state


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


def _build_alert_lines(previous_state: dict[str, Any], current_state: dict[str, Any]) -> list[str]:
    lines = []
    previous_symbols = previous_state.get("symbols", {}) if isinstance(previous_state, dict) else {}
    current_symbols = current_state.get("symbols", {})
    if not isinstance(previous_symbols, dict) or not isinstance(current_symbols, dict):
        return lines

    for symbol, current_symbol_state in current_symbols.items():
        previous_symbol_state = previous_symbols.get(symbol, {})
        if not isinstance(previous_symbol_state, dict):
            continue
        lines.extend(_overall_alert_lines(symbol, previous_symbol_state, current_symbol_state))
        lines.extend(_timeframe_alert_lines(symbol, previous_symbol_state, current_symbol_state))
    return lines


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
