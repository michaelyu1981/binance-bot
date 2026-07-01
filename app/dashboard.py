"""Local read-only dashboard for public market monitor logs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
from typing import Callable
from urllib.parse import parse_qs, urlparse

from app.health import (
    CANDLE_COLLECTOR_HEALTH_PATH,
    PRICE_MONITOR_HEALTH_PATH,
    ServiceHealth,
    read_service_health,
)
from app.summary import (
    DEFAULT_SUMMARY_HOURS,
    AlertLogEntry,
    MarketSummary,
    PriceLogEntry,
    build_market_summary,
)


DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 8765
RECENT_LOG_LINE_LIMIT = 300
EXPECTED_WATCH_INTERVAL_SECONDS = 300
STALE_AFTER_SECONDS = EXPECTED_WATCH_INTERVAL_SECONDS * 3
ALERT_SYMBOL_RE = re.compile(r"\s+ALERT\s+(?P<symbol>[A-Z0-9]+):")
ALERT_CHANGE_RE = re.compile(r":\s+(?P<change>[+-]?[0-9]+(?:\.[0-9]+)?)%")


@dataclass(frozen=True)
class TrendRow:
    symbol: str
    latest_price: Decimal
    one_hour_change: Decimal | None
    four_hour_change: Decimal | None
    twenty_four_hour_change: Decimal | None


@dataclass(frozen=True)
class AlertStats:
    total_today: int
    counts_by_symbol: tuple[tuple[str, int], ...]
    biggest_today: AlertLogEntry | None


@dataclass(frozen=True)
class LogCoverage:
    first_time: str
    latest_time: str
    duration: str
    price_cycles: int


def run_dashboard_server(
    *,
    host: str = DEFAULT_DASHBOARD_HOST,
    port: int = DEFAULT_DASHBOARD_PORT,
) -> None:
    """Run a local read-only dashboard server."""

    handler = _build_dashboard_handler()
    server = ThreadingHTTPServer((host, port), handler)
    print(f"CoinPilot dashboard running at http://{host}:{port}")
    print("Read-only dashboard. No API key. No account access. No orders.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping CoinPilot dashboard.")
    finally:
        server.server_close()


def _build_dashboard_handler() -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._serve_dashboard(include_body=True)

        def do_HEAD(self) -> None:
            self._serve_dashboard(include_body=False)

        def _serve_dashboard(self, *, include_body: bool) -> None:
            parsed_url = urlparse(self.path)
            if parsed_url.path not in ("/", "/index.html"):
                self.send_error(404, "Not found")
                return

            query = parse_qs(parsed_url.query)
            summary_hours = _parse_summary_hours(query.get("hours", ["24"])[0])
            summary = build_market_summary(summary_hours=summary_hours)
            body = render_dashboard(summary)
            encoded_body = body.encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded_body)))
            self.end_headers()
            if include_body:
                self.wfile.write(encoded_body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardHandler


def render_dashboard(summary: MarketSummary) -> str:
    """Render dashboard HTML from an already-built market summary."""

    report_datetime = _parse_datetime(summary.report_timestamp)
    biggest_mover = (
        "None"
        if summary.biggest_mover is None
        else (
            f"{summary.biggest_mover.symbol} "
            f"({_format_percent(summary.biggest_mover.change_percent)})"
        )
    )
    last_alert = summary.alert_entries[-1].raw_line if summary.alert_entries else "None"
    latest_timestamp = (
        max((entry.raw_timestamp for entry in summary.price_entries), default="No data")
    )
    health_status, last_update_age = _health_status(summary, report_datetime)
    alert_stats = _alert_stats(summary, report_datetime)
    trend_rows = _trend_rows(summary, report_datetime)
    log_coverage = _log_coverage(summary)
    price_monitor_health = read_service_health(
        PRICE_MONITOR_HEALTH_PATH,
        service="price_monitor",
    )
    candle_collector_health = read_service_health(
        CANDLE_COLLECTOR_HEALTH_PATH,
        service="candle_collector",
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="60">
  <title>CoinPilot Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --text: #1c2430;
      --muted: #647084;
      --line: #d9e0ea;
      --good: #087f5b;
      --bad: #c92a2a;
      --warn: #b7791f;
      --accent: #1f6feb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      padding: 24px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    h1, h2 {{ margin: 0; }}
    h1 {{ font-size: 24px; }}
    h2 {{ font-size: 16px; margin-bottom: 12px; }}
    main {{ padding: 24px; max-width: 1180px; margin: 0 auto; }}
    .muted {{ color: var(--muted); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    .metric {{ font-size: 22px; font-weight: 700; margin-top: 6px; }}
    .metric.compact {{
      font-size: 15px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }}
    .status-good {{ color: var(--good); }}
    .status-warn {{ color: var(--warn); }}
    .status-bad {{ color: var(--bad); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 8px; border-bottom: 1px solid var(--line); text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ color: var(--muted); font-weight: 600; }}
    .positive {{ color: var(--good); }}
    .negative {{ color: var(--bad); }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: #253044;
    }}
    .log-view {{
      max-height: 520px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      padding: 12px;
    }}
    .log-batch {{
      padding: 10px 0;
      border-top: 1px solid var(--line);
    }}
    .log-batch:first-child {{
      border-top: 0;
      padding-top: 0;
    }}
    .log-time {{
      color: var(--accent);
      font-weight: 700;
      margin-bottom: 6px;
    }}
    .log-line {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 13px;
      padding: 2px 0;
    }}
    .stack {{ display: grid; gap: 18px; }}
    @media (max-width: 760px) {{
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      main, header {{ padding: 16px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>CoinPilot</h1>
    <div class="muted">Read-only public Binance market monitor. No API key. No account access. No orders.</div>
  </header>
  <main>
    <section class="grid">
      {_metric_card("Report Time", summary.report_timestamp)}
      {_metric_card("Latest Price Time", latest_timestamp)}
      {_metric_card("Biggest Mover", biggest_mover)}
      {_metric_card("Alerts", str(len(summary.alert_entries)))}
    </section>
    <section class="grid">
      {_metric_card("Monitor Health", health_status)}
      {_metric_card("Last Update Age", last_update_age)}
      {_metric_card("Expected Interval", f"{EXPECTED_WATCH_INTERVAL_SECONDS} seconds")}
      {_metric_card("Timezone", "Philippine time UTC+8")}
    </section>
    <section class="grid">
      {_service_health_card("Price Monitor", price_monitor_health)}
      {_metric_card("Price Last Success", price_monitor_health.last_success or "None")}
      {_service_health_card("Candle Collector", candle_collector_health)}
      {_metric_card("Candle Last Success", candle_collector_health.last_success or "None")}
    </section>
    <section class="grid">
      {_metric_card("First Log Time", log_coverage.first_time)}
      {_metric_card("Latest Log Time", log_coverage.latest_time)}
      {_metric_card("Log Coverage", log_coverage.duration)}
      {_metric_card("Price Cycles", str(log_coverage.price_cycles))}
    </section>
    <section class="panel" style="margin-bottom:18px;">
      <h2>Prices</h2>
      {_render_price_table(summary)}
    </section>
    <section class="panel" style="margin-bottom:18px;">
      <h2>Trend</h2>
      {_render_trend_table(trend_rows)}
    </section>
    <section class="grid">
      {_metric_card("Alerts Today", str(alert_stats.total_today))}
      {_metric_card("Alert Symbols", _format_alert_symbol_counts(alert_stats))}
      {_metric_card("Biggest Alert Today", _format_biggest_alert(alert_stats))}
      {_metric_card("Trading Mode", "Disabled")}
    </section>
    <section class="panel" style="margin-bottom:18px;">
      <h2>Alert History</h2>
      {_render_alert_history(summary)}
    </section>
    <section class="grid">
      {_metric_card("Summary Window", f"{summary.summary_hours} hours")}
      {_metric_card("Log Files", str(len(summary.log_paths)))}
      {_metric_card("Last Alert", last_alert)}
      {_metric_card("Safety", "Public data only")}
    </section>
    <section class="panel">
      <h2>Recent Log Lines</h2>
      {_render_recent_logs(summary)}
    </section>
  </main>
</body>
</html>
"""


def _render_price_table(summary: MarketSummary) -> str:
    if not summary.symbol_summaries:
        message = summary.message or "No price data found for this summary period."
        return f"<p class=\"muted\">{escape(message)}</p>"

    rows = []
    for item in summary.symbol_summaries:
        change_class = "positive" if item.change_percent >= 0 else "negative"
        rows.append(
            "<tr>"
            f"<td>{escape(item.symbol)}</td>"
            f"<td>{escape(str(item.latest_price))}</td>"
            f"<td>{escape(str(item.first_price))}</td>"
            f"<td>{escape(str(item.last_price))}</td>"
            f"<td class=\"{change_class}\">{escape(_format_percent(item.change_percent))}</td>"
            "</tr>"
        )

    return (
        "<table>"
        "<thead><tr><th>Symbol</th><th>Latest</th><th>First</th><th>Last</th><th>Change</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _render_trend_table(rows: tuple[TrendRow, ...]) -> str:
    if not rows:
        return "<p class=\"muted\">No trend data found in the selected log window.</p>"

    table_rows = []
    for row in rows:
        table_rows.append(
            "<tr>"
            f"<td>{escape(row.symbol)}</td>"
            f"<td>{escape(str(row.latest_price))}</td>"
            f"<td class=\"{_percent_class(row.one_hour_change)}\">{escape(_format_optional_percent(row.one_hour_change))}</td>"
            f"<td class=\"{_percent_class(row.four_hour_change)}\">{escape(_format_optional_percent(row.four_hour_change))}</td>"
            f"<td class=\"{_percent_class(row.twenty_four_hour_change)}\">{escape(_format_optional_percent(row.twenty_four_hour_change))}</td>"
            "</tr>"
        )

    return (
        "<table>"
        "<thead><tr><th>Symbol</th><th>Latest</th><th>1h</th><th>4h</th><th>24h</th></tr></thead>"
        f"<tbody>{''.join(table_rows)}</tbody>"
        "</table>"
    )


def _render_alert_history(summary: MarketSummary) -> str:
    if not summary.alert_entries:
        return "<p class=\"muted\">No alerts in the selected log window.</p>"

    rows = []
    for entry in reversed(summary.alert_entries[-20:]):
        symbol = _alert_symbol(entry)
        rows.append(
            "<tr>"
            f"<td>{escape(entry.timestamp.isoformat())}</td>"
            f"<td>{escape(symbol or 'Unknown')}</td>"
            f"<td>{escape(entry.raw_line)}</td>"
            "</tr>"
        )

    return (
        "<table>"
        "<thead><tr><th>Time</th><th>Symbol</th><th>Alert</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _metric_card(label: str, value: str) -> str:
    metric_class = "metric compact" if len(value) > 26 else "metric"
    return (
        "<section class=\"panel\">"
        f"<div class=\"muted\">{escape(label)}</div>"
        f"<div class=\"{metric_class}\">{escape(value)}</div>"
        "</section>"
    )


def _service_health_card(label: str, health: ServiceHealth) -> str:
    health_class = _health_class(health.health)
    lines = [
        health.health,
        f"age: {_format_service_age(health.age_seconds)}",
    ]
    if health.last_error_message:
        lines.append(f"error: {health.last_error_message}")
    return (
        "<section class=\"panel\">"
        f"<div class=\"muted\">{escape(label)}</div>"
        f"<div class=\"metric compact {health_class}\">{escape(' | '.join(lines))}</div>"
        "</section>"
    )


def _render_recent_logs(summary: MarketSummary) -> str:
    entries = summary.price_entries[-RECENT_LOG_LINE_LIMIT:]
    if not entries and not summary.alert_entries:
        return "<p class=\"muted\">No recent log lines found.</p>"

    batches: dict[str, list[str]] = {}
    for entry in entries:
        batches.setdefault(entry.raw_timestamp, []).append(entry.raw_line)

    batch_html = []
    for timestamp, lines in batches.items():
        line_html = "".join(
            f"<div class=\"log-line\">{escape(_strip_timestamp(line, timestamp))}</div>"
            for line in lines
        )
        batch_html.append(
            "<section class=\"log-batch\">"
            f"<div class=\"log-time\">{escape(_format_philippine_time_label(timestamp))}</div>"
            f"{line_html}"
            "</section>"
        )

    alert_html = ""
    if summary.alert_entries:
        alerts = summary.alert_entries[-5:]
        alert_lines = "".join(
            f"<div class=\"log-line negative\">{escape(entry.raw_line)}</div>"
            for entry in alerts
        )
        alert_html = (
            "<section class=\"log-batch\">"
            "<div class=\"log-time\">Recent alerts</div>"
            f"{alert_lines}"
            "</section>"
        )

    return f"<div class=\"log-view\">{''.join(batch_html)}{alert_html}</div>"


def _strip_timestamp(line: str, timestamp: str) -> str:
    prefix = f"{timestamp} "
    if line.startswith(prefix):
        return line[len(prefix):]
    return line


def _format_philippine_time_label(timestamp: str) -> str:
    return f"{timestamp} = Philippine time"


def _health_status(
    summary: MarketSummary,
    report_datetime: datetime | None,
) -> tuple[str, str]:
    if not summary.price_entries:
        return "No price data", "No data"
    if report_datetime is None:
        return "Unknown", "Unknown"

    latest_entry = max(summary.price_entries, key=lambda entry: entry.timestamp)
    age_seconds = max(0, int((report_datetime - latest_entry.timestamp).total_seconds()))
    age_label = _format_duration(age_seconds)
    if age_seconds <= STALE_AFTER_SECONDS:
        return "OK", age_label
    return "STALE", age_label


def _log_coverage(summary: MarketSummary) -> LogCoverage:
    if not summary.price_entries:
        return LogCoverage(
            first_time="No data",
            latest_time="No data",
            duration="No data",
            price_cycles=0,
        )

    first_entry = min(summary.price_entries, key=lambda entry: entry.timestamp)
    latest_entry = max(summary.price_entries, key=lambda entry: entry.timestamp)
    duration_seconds = max(
        0,
        int((latest_entry.timestamp - first_entry.timestamp).total_seconds()),
    )
    price_cycles = len({entry.raw_timestamp for entry in summary.price_entries})
    return LogCoverage(
        first_time=_format_philippine_time_label(first_entry.raw_timestamp),
        latest_time=_format_philippine_time_label(latest_entry.raw_timestamp),
        duration=_format_duration(duration_seconds),
        price_cycles=price_cycles,
    )


def _trend_rows(
    summary: MarketSummary,
    report_datetime: datetime | None,
) -> tuple[TrendRow, ...]:
    if report_datetime is None:
        return ()

    entries_by_symbol: dict[str, list[PriceLogEntry]] = {}
    for entry in summary.price_entries:
        entries_by_symbol.setdefault(entry.symbol, []).append(entry)

    rows: list[TrendRow] = []
    for symbol in sorted(entries_by_symbol):
        symbol_entries = sorted(entries_by_symbol[symbol], key=lambda entry: entry.timestamp)
        latest_entry = symbol_entries[-1]
        rows.append(
            TrendRow(
                symbol=symbol,
                latest_price=latest_entry.price,
                one_hour_change=_window_change(symbol_entries, report_datetime, hours=1),
                four_hour_change=_window_change(symbol_entries, report_datetime, hours=4),
                twenty_four_hour_change=_window_change(symbol_entries, report_datetime, hours=24),
            )
        )
    return tuple(rows)


def _window_change(
    entries: list[PriceLogEntry],
    report_datetime: datetime,
    *,
    hours: int,
) -> Decimal | None:
    cutoff = report_datetime - timedelta(hours=hours)
    window_entries = [entry for entry in entries if entry.timestamp >= cutoff]
    if len(window_entries) < 2:
        return None

    first_entry = window_entries[0]
    last_entry = window_entries[-1]
    if first_entry.price == 0:
        return None
    return ((last_entry.price - first_entry.price) / first_entry.price) * Decimal("100")


def _alert_stats(
    summary: MarketSummary,
    report_datetime: datetime | None,
) -> AlertStats:
    if report_datetime is None:
        return AlertStats(total_today=0, counts_by_symbol=(), biggest_today=None)

    today_alerts = [
        entry
        for entry in summary.alert_entries
        if entry.timestamp.date() == report_datetime.date()
    ]
    counts: dict[str, int] = {}
    for entry in today_alerts:
        symbol = _alert_symbol(entry)
        if symbol is None:
            continue
        counts[symbol] = counts.get(symbol, 0) + 1

    biggest_alert = max(
        today_alerts,
        key=lambda entry: abs(_alert_change(entry) or Decimal("0")),
        default=None,
    )
    return AlertStats(
        total_today=len(today_alerts),
        counts_by_symbol=tuple(sorted(counts.items())),
        biggest_today=biggest_alert,
    )


def _alert_symbol(entry: AlertLogEntry) -> str | None:
    match = ALERT_SYMBOL_RE.search(entry.raw_line)
    if match is None:
        return None
    return match.group("symbol")


def _alert_change(entry: AlertLogEntry) -> Decimal | None:
    match = ALERT_CHANGE_RE.search(entry.raw_line)
    if match is None:
        return None
    return Decimal(match.group("change"))


def _format_alert_symbol_counts(stats: AlertStats) -> str:
    if not stats.counts_by_symbol:
        return "None"
    return ", ".join(f"{symbol}: {count}" for symbol, count in stats.counts_by_symbol)


def _format_biggest_alert(stats: AlertStats) -> str:
    if stats.biggest_today is None:
        return "None"
    return stats.biggest_today.raw_line


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    remaining_seconds = seconds % 60
    if minutes < 60:
        return f"{minutes}m {remaining_seconds}s"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    return f"{hours}h {remaining_minutes}m"


def _format_service_age(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    return _format_duration(seconds)


def _health_class(health: str) -> str:
    if health == "OK":
        return "status-good"
    if health in ("STALE", "UNKNOWN"):
        return "status-warn"
    return "status-bad"


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _percent_class(value: Decimal | None) -> str:
    if value is None:
        return ""
    return "positive" if value >= 0 else "negative"


def _format_optional_percent(value: Decimal | None) -> str:
    if value is None:
        return "Not enough data"
    return _format_percent(value)


def _format_percent(value: object) -> str:
    return f"{value:+.2f}%"


def _parse_summary_hours(value: str) -> int:
    try:
        parsed_value = int(value)
    except ValueError:
        return DEFAULT_SUMMARY_HOURS
    if parsed_value <= 0:
        return DEFAULT_SUMMARY_HOURS
    return parsed_value
