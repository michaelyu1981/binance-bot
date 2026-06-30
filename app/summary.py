"""Summary reporting for public market price logs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

from app.alerts import format_signed_percent
from app.logger import (
    MARKET_PRICE_LOG_DIR,
    MARKET_PRICE_LOG_PATH,
    daily_market_price_log_path,
    current_timestamp,
)


DEFAULT_SUMMARY_HOURS = 24

_PRICE_LINE_RE = re.compile(
    r"^(?P<timestamp>\S+)\s+(?P<symbol>[A-Z0-9]+):\s+(?P<price>[0-9]+(?:\.[0-9]+)?)$"
)
_ALERT_LINE_RE = re.compile(r"^(?P<timestamp>\S+)\s+ALERT\s+")


@dataclass(frozen=True)
class PriceLogEntry:
    """One public market price entry parsed from the runtime log."""

    timestamp: datetime
    raw_timestamp: str
    symbol: str
    price: Decimal
    raw_line: str


@dataclass(frozen=True)
class AlertLogEntry:
    """One local alert line parsed from the runtime log."""

    timestamp: datetime
    raw_line: str


@dataclass(frozen=True)
class SymbolSummary:
    """Summary values for one symbol in the selected period."""

    symbol: str
    first_price: Decimal
    last_price: Decimal
    latest_price: Decimal
    change_percent: Decimal


@dataclass(frozen=True)
class MarketSummary:
    """Built summary report data."""

    report_timestamp: str
    summary_hours: int
    price_entries: tuple[PriceLogEntry, ...]
    alert_entries: tuple[AlertLogEntry, ...]
    symbol_summaries: tuple[SymbolSummary, ...]
    biggest_mover: SymbolSummary | None
    log_paths: tuple[Path, ...]
    message: str | None = None


def build_market_summary(
    *,
    log_path: Path | None = None,
    summary_hours: int = DEFAULT_SUMMARY_HOURS,
) -> MarketSummary:
    """Build a market summary from local log files only."""

    report_timestamp = current_timestamp()
    if summary_hours <= 0:
        raise ValueError("summary_hours must be positive.")

    report_datetime = datetime.fromisoformat(report_timestamp)
    cutoff_timestamp = report_datetime - timedelta(hours=summary_hours)
    log_paths = _summary_log_paths(
        start_datetime=cutoff_timestamp,
        end_datetime=report_datetime,
        log_path=log_path,
    )

    existing_log_paths = tuple(path for path in log_paths if path.exists())
    if not existing_log_paths:
        return _empty_summary(
            report_timestamp=report_timestamp,
            summary_hours=summary_hours,
            log_paths=log_paths,
            message=f"Log files not found: {_format_log_paths(log_paths)}",
        )

    raw_lines = _read_log_lines(existing_log_paths)
    if not raw_lines:
        return _empty_summary(
            report_timestamp=report_timestamp,
            summary_hours=summary_hours,
            log_paths=existing_log_paths,
            message=f"Log files are empty: {_format_log_paths(existing_log_paths)}",
        )

    price_entries, alert_entries = _parse_log_lines(raw_lines)
    if not price_entries and not alert_entries:
        return _empty_summary(
            report_timestamp=report_timestamp,
            summary_hours=summary_hours,
            log_paths=existing_log_paths,
            message="No parseable price or alert lines found in the log file.",
        )

    period_price_entries = tuple(
        entry for entry in price_entries if entry.timestamp >= cutoff_timestamp
    )
    period_alert_entries = tuple(
        entry for entry in alert_entries if entry.timestamp >= cutoff_timestamp
    )
    symbol_summaries = tuple(_build_symbol_summaries(period_price_entries))
    biggest_mover = max(
        symbol_summaries,
        key=lambda summary: abs(summary.change_percent),
        default=None,
    )

    return MarketSummary(
        report_timestamp=report_timestamp,
        summary_hours=summary_hours,
        price_entries=period_price_entries,
        alert_entries=period_alert_entries,
        symbol_summaries=symbol_summaries,
        biggest_mover=biggest_mover,
        log_paths=existing_log_paths,
    )


def format_market_summary(summary: MarketSummary) -> str:
    """Format a market summary for terminal or Telegram output."""

    lines = [
        "Binance public market summary",
        f"Report timestamp: {summary.report_timestamp}",
        f"Summary period: last {summary.summary_hours} hours",
        f"Log files: {_format_log_paths(summary.log_paths)}",
        "Safety: public market data only; no API key, no account access, no orders.",
    ]

    if summary.message is not None:
        lines.append(f"Note: {summary.message}")

    lines.append("")
    lines.append("Prices by symbol:")
    if summary.symbol_summaries:
        for symbol_summary in summary.symbol_summaries:
            lines.append(
                f"- {symbol_summary.symbol}: "
                f"latest {symbol_summary.latest_price}; "
                f"first {symbol_summary.first_price}; "
                f"last {symbol_summary.last_price}; "
                f"change {format_signed_percent(symbol_summary.change_percent)}"
            )
    else:
        lines.append("- No price lines found for this summary period.")

    lines.append("")
    if summary.biggest_mover is None:
        lines.append("Biggest mover: none")
    else:
        lines.append(
            "Biggest mover: "
            f"{summary.biggest_mover.symbol} "
            f"({format_signed_percent(summary.biggest_mover.change_percent)})"
        )

    lines.append(f"Total ALERT lines: {len(summary.alert_entries)}")
    if summary.alert_entries:
        lines.append(f"Last alert: {summary.alert_entries[-1].raw_line}")
    else:
        lines.append("Last alert: none")

    return "\n".join(lines)


def _empty_summary(
    *,
    report_timestamp: str,
    summary_hours: int,
    log_paths: tuple[Path, ...],
    message: str,
) -> MarketSummary:
    return MarketSummary(
        report_timestamp=report_timestamp,
        summary_hours=summary_hours,
        price_entries=(),
        alert_entries=(),
        symbol_summaries=(),
        biggest_mover=None,
        log_paths=log_paths,
        message=message,
    )


def _summary_log_paths(
    *,
    start_datetime: datetime,
    end_datetime: datetime,
    log_path: Path | None,
) -> tuple[Path, ...]:
    if log_path is not None:
        return (log_path,)

    paths: list[Path] = []
    current_date = start_datetime.date()
    while current_date <= end_datetime.date():
        paths.append(daily_market_price_log_path(current_date=current_date))
        current_date += timedelta(days=1)

    if MARKET_PRICE_LOG_PATH.exists():
        paths.append(MARKET_PRICE_LOG_PATH)

    return tuple(dict.fromkeys(paths))


def _read_log_lines(log_paths: tuple[Path, ...]) -> list[str]:
    raw_lines: list[str] = []
    for path in log_paths:
        raw_lines.extend(path.read_text(encoding="utf-8").splitlines())
    return raw_lines


def _format_log_paths(log_paths: tuple[Path, ...]) -> str:
    if not log_paths:
        return str(MARKET_PRICE_LOG_DIR)
    return ", ".join(str(path) for path in log_paths)


def _parse_log_lines(lines: list[str]) -> tuple[tuple[PriceLogEntry, ...], tuple[AlertLogEntry, ...]]:
    price_entries: list[PriceLogEntry] = []
    alert_entries: list[AlertLogEntry] = []

    for line in lines:
        price_match = _PRICE_LINE_RE.match(line)
        if price_match is not None:
            timestamp = _parse_timestamp(price_match.group("timestamp"))
            if timestamp is None:
                continue
            try:
                price = Decimal(price_match.group("price"))
            except InvalidOperation:
                continue
            price_entries.append(
                PriceLogEntry(
                    timestamp=timestamp,
                    raw_timestamp=price_match.group("timestamp"),
                    symbol=price_match.group("symbol"),
                    price=price,
                    raw_line=line,
                )
            )
            continue

        alert_match = _ALERT_LINE_RE.match(line)
        if alert_match is not None:
            timestamp = _parse_timestamp(alert_match.group("timestamp"))
            if timestamp is None:
                continue
            alert_entries.append(AlertLogEntry(timestamp=timestamp, raw_line=line))

    return tuple(price_entries), tuple(alert_entries)


def _build_symbol_summaries(entries: tuple[PriceLogEntry, ...]) -> list[SymbolSummary]:
    entries_by_symbol: dict[str, list[PriceLogEntry]] = {}
    for entry in entries:
        entries_by_symbol.setdefault(entry.symbol, []).append(entry)

    summaries: list[SymbolSummary] = []
    for symbol in sorted(entries_by_symbol):
        symbol_entries = sorted(entries_by_symbol[symbol], key=lambda entry: entry.timestamp)
        first_entry = symbol_entries[0]
        last_entry = symbol_entries[-1]
        change_percent = Decimal("0")
        if first_entry.price != 0:
            change_percent = ((last_entry.price - first_entry.price) / first_entry.price) * Decimal("100")

        summaries.append(
            SymbolSummary(
                symbol=symbol,
                first_price=first_entry.price,
                last_price=last_entry.price,
                latest_price=last_entry.price,
                change_percent=change_percent,
            )
        )

    return summaries


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
