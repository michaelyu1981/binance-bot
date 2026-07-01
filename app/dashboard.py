"""Local read-only dashboard for public market monitor logs."""

from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from app.summary import DEFAULT_SUMMARY_HOURS, MarketSummary, build_market_summary


DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 8765
RECENT_LOG_LINE_LIMIT = 300


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
    <section class="panel" style="margin-bottom:18px;">
      <h2>Prices</h2>
      {_render_price_table(summary)}
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


def _metric_card(label: str, value: str) -> str:
    return (
        "<section class=\"panel\">"
        f"<div class=\"muted\">{escape(label)}</div>"
        f"<div class=\"metric\">{escape(value)}</div>"
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
