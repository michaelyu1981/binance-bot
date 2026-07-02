"""Local read-only dashboard for public market monitor logs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import base64
import hashlib
import hmac
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import sqlite3
import time
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

from app.binance_account import (
    AccountBalance,
    AccountSnapshot,
    BinanceAccountError,
    fetch_account_snapshot,
    load_binance_account_config_from_env,
)
from app.binance_reader import BinancePublicMarketError, fetch_public_prices
from app.candle_store import DEFAULT_CANDLE_DB_PATH
from app.config import PUBLIC_MARKET_WATCHLIST
from app.advisory import (
    ADVISORY_BOTS,
    AdvisoryOpinion,
    build_advisory_opinions,
    build_consensus_summary,
)
from app.health import (
    CANDLE_COLLECTOR_HEALTH_PATH,
    PRICE_MONITOR_HEALTH_PATH,
    SIGNAL_WATCHER_HEALTH_PATH,
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
from app.indicators import (
    IndicatorSnapshot,
    build_indicator_snapshot,
    calculate_atr_series,
    calculate_bollinger_band_series,
    calculate_ema_series,
    calculate_macd_series,
    calculate_rsi_series,
    calculate_sma_series,
    describe_average_position,
    describe_atr,
    describe_bollinger,
    describe_macd,
    describe_rsi,
    describe_volume,
)
from app.signals import (
    MULTI_TIMEFRAME_WEIGHTS,
    MultiTimeframeSignalSummary,
    ScoreBreakdown,
    TechnicalSignalGuide,
    build_multi_timeframe_signal_summary,
    build_technical_signal_guide,
)
from app.strategies import (
    STRATEGIES,
    StrategyDecision,
    build_strategy_decision,
    build_strategy_decisions,
    strategy_by_slug,
)


DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 8765
RECENT_LOG_LINE_LIMIT = 300
EXPECTED_WATCH_INTERVAL_SECONDS = 300
STALE_AFTER_SECONDS = EXPECTED_WATCH_INTERVAL_SECONDS * 3
ALERT_SYMBOL_RE = re.compile(r"\s+ALERT\s+(?P<symbol>[A-Z0-9]+):")
ALERT_CHANGE_RE = re.compile(r":\s+(?P<change>[+-]?[0-9]+(?:\.[0-9]+)?)%")
DASHBOARD_SESSION_COOKIE = "coinpilot_session"
DASHBOARD_SESSION_TTL_SECONDS = 12 * 60 * 60
DEFAULT_CHART_INTERVAL = "1h"
CHART_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
CHART_CANDLE_LIMIT = 120
MULTI_TIMEFRAME_SUMMARY_INTERVALS = tuple(MULTI_TIMEFRAME_WEIGHTS)
PHILIPPINE_TIMEZONE = timezone(timedelta(hours=8))


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


@dataclass(frozen=True)
class PortfolioBalanceValuation:
    balance: AccountBalance
    price_usdt: Decimal | None
    value_usdt: Decimal | None
    allocation_percent: Decimal | None
    pricing_note: str


@dataclass(frozen=True)
class PortfolioValuation:
    total_usdt: Decimal
    rows: tuple[PortfolioBalanceValuation, ...]


@dataclass(frozen=True)
class ChartCandle:
    symbol: str
    interval: str
    open_time_ms: int
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal


@dataclass(frozen=True)
class ChartSeries:
    symbol: str
    interval: str
    candles: tuple[ChartCandle, ...]


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

        def do_POST(self) -> None:
            parsed_url = urlparse(self.path)
            if parsed_url.path != "/login":
                self.send_error(404, "Not found")
                return
            self._handle_login()

        def _serve_dashboard(self, *, include_body: bool) -> None:
            parsed_url = urlparse(self.path)
            if parsed_url.path == "/logout":
                self._handle_logout()
                return
            if parsed_url.path == "/login":
                self._write_html(
                    render_login_page(message=""),
                    include_body=include_body,
                )
                return
            if parsed_url.path not in (
                "/",
                "/index.html",
                "/charts",
                "/advisory",
                "/algorithms",
                "/account",
            ):
                self.send_error(404, "Not found")
                return

            if _is_auth_required() and not _is_authenticated(self.headers.get("Cookie", "")):
                self._redirect("/login")
                return

            query = parse_qs(parsed_url.query)
            if parsed_url.path == "/charts":
                interval = _parse_chart_interval(query.get("interval", [DEFAULT_CHART_INTERVAL])[0])
                body = render_chart_view(interval=interval)
            elif parsed_url.path == "/advisory":
                symbol = _parse_advisory_symbol(query.get("symbol", [PUBLIC_MARKET_WATCHLIST[0]])[0])
                advisor = query.get("advisor", ["all"])[0]
                body = render_advisory_view(symbol=symbol, advisor=advisor)
            elif parsed_url.path == "/algorithms":
                symbol = _parse_watchlist_symbol(query.get("symbol", [PUBLIC_MARKET_WATCHLIST[0]])[0])
                algorithm = query.get("algorithm", ["all"])[0]
                label = query.get("label", [""])[0]
                body = render_algorithms_view(symbol=symbol, algorithm=algorithm, label=label)
            elif parsed_url.path == "/account":
                body = render_account_view()
            else:
                summary_hours = _parse_summary_hours(query.get("hours", ["24"])[0])
                summary = build_market_summary(summary_hours=summary_hours)
                body = render_dashboard(summary)
            self._write_html(body, include_body=include_body)

        def _handle_login(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length).decode("utf-8")
            form = parse_qs(body)
            username = form.get("username", [""])[0]
            password = form.get("password", [""])[0]

            if _authenticate_credentials(username, password):
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header(
                    "Set-Cookie",
                    _build_session_cookie(username),
                )
                self.end_headers()
                return

            self._write_html(
                render_login_page(message="Invalid dashboard username or password."),
                include_body=True,
                status=401,
            )

        def _handle_logout(self) -> None:
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header(
                "Set-Cookie",
                f"{DASHBOARD_SESSION_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0",
            )
            self.end_headers()

        def _write_html(
            self,
            body: str,
            *,
            include_body: bool,
            status: int = 200,
        ) -> None:
            encoded_body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded_body)))
            self.end_headers()
            if include_body:
                self.wfile.write(encoded_body)

        def _redirect(self, location: str) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()

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
    signal_watcher_health = read_service_health(
        SIGNAL_WATCHER_HEALTH_PATH,
        service="signal_watcher",
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
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 24px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }}
    .nav {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .nav a {{
      color: var(--text);
      text-decoration: none;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 10px;
      background: var(--panel);
    }}
    .nav a.active {{
      border-color: var(--accent);
      color: var(--accent);
      font-weight: 700;
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
    .chart-grid {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 18px;
    }}
    .sparkline {{
      width: 100%;
      height: 260px;
      display: block;
      background: #fbfcfe;
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .indicator-chart {{ height: 132px; margin-top: 10px; }}
    .rsi-chart {{ height: 132px; margin-top: 10px; }}
    .volume-chart {{ height: 116px; margin-top: 10px; }}
    .login-wrap {{
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
    }}
    .login-panel {{
      width: min(420px, 100%);
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
    }}
    label {{ display: block; color: var(--muted); margin-top: 14px; }}
    input {{
      width: 100%;
      padding: 10px;
      margin-top: 6px;
      border: 1px solid var(--line);
      border-radius: 6px;
      font: inherit;
    }}
    button {{
      margin-top: 18px;
      width: 100%;
      padding: 10px;
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent);
      color: white;
      font: inherit;
      font-weight: 700;
    }}
    @media (max-width: 760px) {{
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      main, header, .topbar {{ padding: 16px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>CoinPilot</h1>
    <div class="muted">Read-only public Binance market monitor. No API key. No account access. No orders.</div>
  </header>
  {_render_nav("dashboard")}
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
      {_service_health_card("Signal Watcher", signal_watcher_health)}
      {_metric_card("Signal Last Success", signal_watcher_health.last_success or "None")}
      {_metric_card("Signal Last Error", signal_watcher_health.last_error_message or "None")}
      {_metric_card("Signal Safety", "Advisory only")}
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


def render_chart_view(*, interval: str) -> str:
    """Render read-only candle chart view from local SQLite data."""

    series_list = _load_chart_series(interval=interval)
    multi_timeframe_series = _load_multi_timeframe_series()
    options = "".join(
        f"<a class=\"{'active' if item == interval else ''}\" href=\"/charts?{urlencode({'interval': item})}\">{escape(item)}</a>"
        for item in CHART_INTERVALS
    )
    chart_sections = "".join(_render_chart_series(series) for series in series_list)
    if not chart_sections:
        chart_sections = (
            "<section class=\"panel\">"
            "<p class=\"muted\">No candle data found yet. Run the public candle collector first.</p>"
            "</section>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="60">
  <title>CoinPilot Charts</title>
  <style>{_shared_page_css()}</style>
</head>
<body>
  <header>
    <h1>CoinPilot Charts</h1>
    <div class="muted">Read-only candle view from local SQLite. No API key. No account access. No orders.</div>
  </header>
  {_render_nav("charts")}
  <main>
    <section class="panel" style="margin-bottom:18px;">
      <h2>Interval</h2>
      <div class="nav">{options}</div>
    </section>
    <section class="panel" style="margin-bottom:18px;">
      <h2>Multi-Timeframe Summary</h2>
      <p class="muted">Rule-based rollup from 1d, 4h, 1h, and 15m. Higher timeframes carry more weight; conflicts default to WAIT.</p>
      {_render_multi_timeframe_summary(multi_timeframe_series)}
    </section>
    <section class="panel" style="margin-bottom:18px;">
      <h2>Technical Indicators</h2>
      <p class="muted">Advisory only. Calculated from local public candles; no API key, no account access, no orders.</p>
      {_render_indicator_table(series_list)}
    </section>
    <section class="panel" style="margin-bottom:18px;">
      <h2>Technical Signal Guide</h2>
      <p class="muted">Rule-based advisory only. No AI, no prediction model, no account access, no orders.</p>
      {_render_signal_guides(series_list)}
    </section>
    <section class="chart-grid">
      {chart_sections}
    </section>
  </main>
  <script>{_chart_interaction_script()}</script>
</body>
</html>
"""


def render_account_view() -> str:
    """Render read-only Binance account snapshot page."""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="300">
  <title>CoinPilot Account</title>
  <style>{_shared_page_css()}</style>
</head>
<body>
  <header>
    <h1>CoinPilot Account</h1>
    <div class="muted">Read-only Binance Spot account snapshot. No buy/sell. No order endpoints. No withdrawals.</div>
  </header>
  {_render_nav("account")}
  <main>
    {_render_account_snapshot_panel()}
  </main>
</body>
</html>
"""


def render_advisory_view(*, symbol: str, advisor: str) -> str:
    """Render modular advisory-board commentary from deterministic signals."""

    symbol_options = "".join(
        f"<a class=\"{'active' if item == symbol else ''}\" href=\"/advisory?{urlencode({'symbol': item, 'advisor': advisor})}\">{escape(item)}</a>"
        for item in PUBLIC_MARKET_WATCHLIST
    )
    advisor_options = (
        f"<a class=\"{'active' if advisor == 'all' else ''}\" href=\"/advisory?{urlencode({'symbol': symbol, 'advisor': 'all'})}\">All Advisors</a>"
        + "".join(
            f"<a class=\"{'active' if bot.slug == advisor else ''}\" href=\"/advisory?{urlencode({'symbol': symbol, 'advisor': bot.slug})}\">{escape(bot.name)}</a>"
            for bot in ADVISORY_BOTS
        )
    )
    body = _render_advisory_body(symbol=symbol, advisor=advisor)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="300">
  <title>CoinPilot Advisory</title>
  <style>{_shared_page_css()}</style>
</head>
<body>
  <header>
    <h1>CoinPilot Advisory</h1>
    <div class="muted">Template-based advisory board from deterministic public-market signals. No AI calls. No account access. No orders.</div>
  </header>
  {_render_nav("advisory")}
  <main>
    <section class="panel" style="margin-bottom:18px;">
      <h2>Coin</h2>
      <div class="nav">{symbol_options}</div>
    </section>
    <section class="panel" style="margin-bottom:18px;">
      <h2>Advisor</h2>
      <div class="nav">{advisor_options}</div>
    </section>
    {body}
  </main>
</body>
</html>
"""


def render_algorithms_view(*, symbol: str, algorithm: str, label: str) -> str:
    """Render deterministic strategy algorithm output."""

    symbol_options = "".join(
        f"<a class=\"{'active' if item == symbol else ''}\" href=\"/algorithms?{urlencode({'symbol': item, 'algorithm': algorithm, 'label': label})}\">{escape(item)}</a>"
        for item in PUBLIC_MARKET_WATCHLIST
    )
    algorithm_options = (
        f"<a class=\"{'active' if algorithm == 'all' else ''}\" href=\"/algorithms?{urlencode({'symbol': symbol, 'algorithm': 'all', 'label': label})}\">All Algorithms</a>"
        + "".join(
            f"<a class=\"{'active' if item.slug == algorithm else ''}\" href=\"/algorithms?{urlencode({'symbol': symbol, 'algorithm': item.slug, 'label': label})}\">{escape(item.name)}</a>"
            for item in STRATEGIES
        )
    )
    body = _render_algorithms_body(symbol=symbol, algorithm=algorithm, label=label)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="300">
  <title>CoinPilot Algorithms</title>
  <style>{_shared_page_css()}</style>
</head>
<body>
  <header>
    <h1>CoinPilot Algorithms</h1>
    <div class="muted">Deterministic strategy rules only. No AI calls. No Binance order endpoints. No automatic trading.</div>
  </header>
  {_render_nav("algorithms")}
  <main>
    <section class="panel" style="margin-bottom:18px;">
      <h2>Coin</h2>
      <div class="nav">{symbol_options}</div>
    </section>
    <section class="panel" style="margin-bottom:18px;">
      <h2>Algorithm</h2>
      <div class="nav">{algorithm_options}</div>
    </section>
    <section class="panel" style="margin-bottom:18px;">
      <h2>Run Label</h2>
      <form method="get" action="/algorithms" class="filter-form">
        <input type="hidden" name="symbol" value="{escape(symbol)}">
        <input type="hidden" name="algorithm" value="{escape(algorithm)}">
        <input name="label" value="{escape(label)}" placeholder="Example: Michael Fast Scraper test">
        <button type="submit">Apply Label</button>
      </form>
      <p class="muted">The label is display-only and is not saved. Algorithm logic remains static and rule-based.</p>
    </section>
    {body}
  </main>
</body>
</html>
"""


def render_login_page(*, message: str) -> str:
    """Render dashboard login page."""

    auth_note = (
        "Dashboard password is configured."
        if _is_auth_required()
        else "Dashboard password is not configured; login is disabled."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CoinPilot Login</title>
  <style>{_shared_page_css()}</style>
</head>
<body>
  <main class="login-wrap">
    <section class="login-panel">
      <h1>CoinPilot</h1>
      <p class="muted">Private read-only dashboard.</p>
      <p class="muted">{escape(auth_note)}</p>
      {_render_login_message(message)}
      <form method="post" action="/login">
        <label for="username">Username</label>
        <input id="username" name="username" autocomplete="username" required>
        <label for="password">Password</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
        <button type="submit">Sign in</button>
      </form>
    </section>
  </main>
</body>
</html>
"""


def _shared_page_css() -> str:
    return """
    :root {
      color-scheme: light;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --text: #1c2430;
      --muted: #647084;
      --line: #d9e0ea;
      --good: #087f5b;
      --bad: #c92a2a;
      --accent: #1f6feb;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      padding: 24px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1, h2 { margin: 0; }
    h1 { font-size: 24px; }
    h2 { font-size: 16px; margin-bottom: 12px; }
    main { padding: 24px; max-width: 1180px; margin: 0 auto; }
    .muted { color: var(--muted); }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .nav {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .nav a {
      color: var(--text);
      text-decoration: none;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 10px;
      background: var(--panel);
    }
    .nav a.active {
      border-color: var(--accent);
      color: var(--accent);
      font-weight: 700;
    }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 24px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }
    .chart-grid { display: grid; grid-template-columns: 1fr; gap: 18px; }
    .signal-grid { display: grid; grid-template-columns: 1fr; gap: 14px; }
    .signal-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: #fbfcfe;
    }
    .signal-head {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .signal-metric {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      background: var(--panel);
    }
    .signal-label { color: var(--muted); font-size: 12px; }
    .signal-value { font-weight: 700; overflow-wrap: anywhere; }
    .signal-detail-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }
    .signal-detail h3 { font-size: 14px; margin: 0 0 6px; }
    .signal-detail ul { margin: 0; padding-left: 18px; }
    .signal-detail li { margin: 3px 0; }
    .advisory-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    .advisory-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: #fbfcfe;
    }
    .advisory-card h3 { margin: 0 0 6px; font-size: 15px; }
    .filter-form {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: end;
    }
    .filter-form input, .filter-form button {
      margin-top: 0;
    }
    .filter-form button {
      width: auto;
      white-space: nowrap;
    }
    .sparkline {
      width: 100%;
      height: 260px;
      display: block;
      background: #fbfcfe;
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .indicator-chart { height: 132px; margin-top: 10px; }
    .rsi-chart { height: 132px; margin-top: 10px; }
    .volume-chart { height: 116px; margin-top: 10px; }
    .chart-controls {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 10px;
      color: var(--muted);
    }
    .chart-controls label {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      margin: 0;
    }
    .chart-toggle {
      width: auto;
      margin: 0;
    }
    .chart-wrap.hide-bollinger .bollinger-overlay,
    .chart-wrap.hide-ema .ema-overlay,
    .chart-wrap.hide-sma .sma-overlay {
      display: none;
    }
    .chart-section.hide-macd .macd-wrap,
    .chart-section.hide-atr .atr-wrap {
      display: none;
    }
    .chart-wrap {
      position: relative;
    }
    .chart-tooltip {
      position: absolute;
      display: none;
      min-width: 150px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      box-shadow: 0 8px 20px rgba(28, 36, 48, 0.12);
      pointer-events: none;
      z-index: 3;
    }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { padding: 10px 8px; border-bottom: 1px solid var(--line); text-align: right; }
    th:first-child, td:first-child { text-align: left; }
    .summary-table th, .summary-table td { text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: 600; }
    .positive { color: var(--good); }
    .negative { color: var(--bad); }
    .login-wrap {
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
    }
    .login-panel {
      width: min(420px, 100%);
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
    }
    label { display: block; color: var(--muted); margin-top: 14px; }
    input {
      width: 100%;
      padding: 10px;
      margin-top: 6px;
      border: 1px solid var(--line);
      border-radius: 6px;
      font: inherit;
    }
    button {
      margin-top: 18px;
      width: 100%;
      padding: 10px;
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent);
      color: white;
      font: inherit;
      font-weight: 700;
    }
    @media (max-width: 760px) {
      main, header, .topbar { padding: 16px; }
      .signal-head, .signal-detail-grid { grid-template-columns: 1fr; }
      .advisory-grid { grid-template-columns: 1fr; }
      .filter-form { grid-template-columns: 1fr; }
      .filter-form button { width: 100%; }
    }
    """


def _render_nav(active: str) -> str:
    logout_link = "<a href=\"/logout\">Logout</a>" if _is_auth_required() else ""
    return (
        "<nav class=\"topbar\">"
        "<div class=\"nav\">"
        f"<a class=\"{'active' if active == 'dashboard' else ''}\" href=\"/\">Dashboard Main</a>"
        f"<a class=\"{'active' if active == 'charts' else ''}\" href=\"/charts\">Chart View</a>"
        f"<a class=\"{'active' if active == 'advisory' else ''}\" href=\"/advisory\">Advisory</a>"
        f"<a class=\"{'active' if active == 'algorithms' else ''}\" href=\"/algorithms\">Algorithms</a>"
        f"<a class=\"{'active' if active == 'account' else ''}\" href=\"/account\">Account</a>"
        "</div>"
        f"<div class=\"nav\">{logout_link}</div>"
        "</nav>"
    )


def _render_login_message(message: str) -> str:
    if not message:
        return ""
    return f"<p class=\"negative\">{escape(message)}</p>"


def _load_chart_series(*, interval: str) -> tuple[ChartSeries, ...]:
    if not DEFAULT_CANDLE_DB_PATH.exists():
        return ()

    series_list: list[ChartSeries] = []
    try:
        connection = sqlite3.connect(DEFAULT_CANDLE_DB_PATH)
        for symbol in PUBLIC_MARKET_WATCHLIST:
            rows = connection.execute(
                """
                SELECT symbol, interval, open_time_ms, open, high, low, close, volume
                FROM candles
                WHERE symbol = ? AND interval = ?
                ORDER BY open_time_ms DESC
                LIMIT ?
                """,
                (symbol, interval, CHART_CANDLE_LIMIT),
            ).fetchall()
            candles = tuple(
                ChartCandle(
                    symbol=row[0],
                    interval=row[1],
                    open_time_ms=int(row[2]),
                    open_price=Decimal(row[3]),
                    high_price=Decimal(row[4]),
                    low_price=Decimal(row[5]),
                    close_price=Decimal(row[6]),
                    volume=Decimal(row[7]),
                )
                for row in reversed(rows)
            )
            series_list.append(ChartSeries(symbol=symbol, interval=interval, candles=candles))
    except sqlite3.Error:
        return ()
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass

    return tuple(series_list)


def _load_multi_timeframe_series() -> dict[str, dict[str, ChartSeries]]:
    if not DEFAULT_CANDLE_DB_PATH.exists():
        return {}

    series_by_symbol: dict[str, dict[str, ChartSeries]] = {
        symbol: {} for symbol in PUBLIC_MARKET_WATCHLIST
    }
    try:
        connection = sqlite3.connect(DEFAULT_CANDLE_DB_PATH)
        for symbol in PUBLIC_MARKET_WATCHLIST:
            for interval in MULTI_TIMEFRAME_SUMMARY_INTERVALS:
                rows = connection.execute(
                    """
                    SELECT symbol, interval, open_time_ms, open, high, low, close, volume
                    FROM candles
                    WHERE symbol = ? AND interval = ?
                    ORDER BY open_time_ms DESC
                    LIMIT ?
                    """,
                    (symbol, interval, CHART_CANDLE_LIMIT),
                ).fetchall()
                candles = tuple(
                    ChartCandle(
                        symbol=row[0],
                        interval=row[1],
                        open_time_ms=int(row[2]),
                        open_price=Decimal(row[3]),
                        high_price=Decimal(row[4]),
                        low_price=Decimal(row[5]),
                        close_price=Decimal(row[6]),
                        volume=Decimal(row[7]),
                    )
                    for row in reversed(rows)
                )
                if candles:
                    series_by_symbol[symbol][interval] = ChartSeries(
                        symbol=symbol,
                        interval=interval,
                        candles=candles,
                    )
    except sqlite3.Error:
        return {}
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass

    return series_by_symbol


def _render_chart_series(series: ChartSeries) -> str:
    if not series.candles:
        return (
            "<section class=\"panel\">"
            f"<h2>{escape(series.symbol)} {escape(series.interval)}</h2>"
            "<p class=\"muted\">No candle rows found for this interval.</p>"
            "</section>"
        )

    latest = series.candles[-1]
    first = series.candles[0]
    change_percent = Decimal("0")
    if first.close_price != 0:
        change_percent = ((latest.close_price - first.close_price) / first.close_price) * Decimal("100")
    change_class = "positive" if change_percent >= 0 else "negative"
    return (
        "<section class=\"panel chart-section\">"
        f"<h2>{escape(series.symbol)} {escape(series.interval)}</h2>"
        f"{_render_sparkline(series.candles)}"
        f"{_render_volume_panel(series.candles)}"
        f"{_render_rsi_panel(series.candles)}"
        f"{_render_macd_panel(series.candles)}"
        f"{_render_atr_panel(series.candles)}"
        "<table>"
        "<thead><tr><th>Latest Time</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th><th>Window Change</th></tr></thead>"
        "<tbody><tr>"
        f"<td>{escape(_format_candle_time(latest.open_time_ms))}</td>"
        f"<td>{escape(str(latest.open_price))}</td>"
        f"<td>{escape(str(latest.high_price))}</td>"
        f"<td>{escape(str(latest.low_price))}</td>"
        f"<td>{escape(str(latest.close_price))}</td>"
        f"<td>{escape(str(latest.volume))}</td>"
        f"<td class=\"{change_class}\">{escape(_format_percent(change_percent))}</td>"
        "</tr></tbody></table>"
        "</section>"
    )


def _render_indicator_table(series_list: tuple[ChartSeries, ...]) -> str:
    rows = []
    for series in series_list:
        if not series.candles:
            continue
        latest_close = series.candles[-1].close_price
        snapshot = build_indicator_snapshot(
            highs=tuple(candle.high_price for candle in series.candles),
            lows=tuple(candle.low_price for candle in series.candles),
            closes=tuple(candle.close_price for candle in series.candles),
            volumes=tuple(candle.volume for candle in series.candles),
        )
        rows.append(
            "<tr>"
            f"<td>{escape(series.symbol)}</td>"
            f"<td>{escape(_format_axis_price(latest_close))}</td>"
            f"<td>{escape(_format_indicator_value(snapshot.rsi))}<br><span class=\"muted\">{escape(describe_rsi(snapshot.rsi))}</span></td>"
            f"<td>{escape(_format_bollinger_value(snapshot))}<br><span class=\"muted\">{escape(describe_bollinger(snapshot.bollinger_percent_b))}</span></td>"
            f"<td>{escape(_format_indicator_value(snapshot.ema))}<br><span class=\"muted\">{escape(describe_average_position(latest_close, snapshot.ema))}</span></td>"
            f"<td>{escape(_format_indicator_value(snapshot.sma))}<br><span class=\"muted\">{escape(describe_average_position(latest_close, snapshot.sma))}</span></td>"
            f"<td>{escape(_format_macd_value(snapshot))}<br><span class=\"muted\">{escape(describe_macd(snapshot.macd, snapshot.macd_signal))}</span></td>"
            f"<td>{escape(_format_volume_value(snapshot))}<br><span class=\"muted\">{escape(describe_volume(snapshot.volume_ratio))}</span></td>"
            f"<td>{escape(_format_atr_value(snapshot))}<br><span class=\"muted\">{escape(describe_atr(snapshot.atr_percent))}</span></td>"
            "</tr>"
        )

    if not rows:
        return "<p class=\"muted\">No candle data found yet. Run the public candle collector first.</p>"

    return (
        "<table>"
        "<thead><tr>"
        "<th>Symbol</th><th>Close</th><th>RSI 14</th><th>Bollinger 20/2</th>"
        "<th>EMA 20</th><th>SMA 50</th><th>MACD 12/26/9</th><th>Volume</th><th>ATR 14</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _render_multi_timeframe_summary(
    series_by_symbol: dict[str, dict[str, ChartSeries]],
) -> str:
    summaries = []
    for symbol in PUBLIC_MARKET_WATCHLIST:
        interval_series = series_by_symbol.get(symbol, {})
        guides_by_interval = {
            interval: guide
            for interval, series in interval_series.items()
            if (guide := _build_signal_guide(series)) is not None
        }
        if not guides_by_interval:
            continue
        summaries.append(
            build_multi_timeframe_signal_summary(
                symbol=symbol,
                guides_by_interval=guides_by_interval,
            )
        )

    if not summaries:
        return "<p class=\"muted\">No multi-timeframe candle data found yet.</p>"

    rows = "".join(_render_multi_timeframe_summary_row(summary) for summary in summaries)
    return (
        "<table class=\"summary-table\">"
        "<thead><tr>"
        "<th>Symbol</th><th>Overall</th><th>Bias</th><th>Score</th>"
        "<th>Alignment</th><th>Higher TF Bias</th><th>Short-Term Pressure</th>"
        "<th>Best Use</th><th>Timeframes</th><th>Final Decision</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )


def _render_multi_timeframe_summary_row(summary: MultiTimeframeSignalSummary) -> str:
    timeframe_text = " | ".join(
        f"{item.interval}: {item.signal}/{item.bias}/{item.score}"
        for item in summary.timeframes
    )
    if summary.missing_intervals:
        timeframe_text = (
            f"{timeframe_text} | Missing: {', '.join(summary.missing_intervals)}"
            if timeframe_text
            else f"Missing: {', '.join(summary.missing_intervals)}"
        )
    return (
        "<tr>"
        f"<td>{escape(summary.symbol)}</td>"
        f"<td><strong>{escape(summary.overall)}</strong></td>"
        f"<td>{escape(summary.bias)}</td>"
        f"<td>{summary.score}/100</td>"
        f"<td>{escape(summary.alignment)}</td>"
        f"<td>{escape(summary.higher_timeframe_bias)}</td>"
        f"<td>{escape(summary.short_term_pressure)}</td>"
        f"<td>{escape(summary.best_use)}</td>"
        f"<td>{escape(timeframe_text)}</td>"
        f"<td>{escape(summary.final_decision)}</td>"
        "</tr>"
    )


def _render_advisory_body(*, symbol: str, advisor: str) -> str:
    interval_series = _load_multi_timeframe_series().get(symbol, {})
    guides_by_interval = {
        interval: guide
        for interval, series in interval_series.items()
        if (guide := _build_signal_guide(series)) is not None
    }
    if not guides_by_interval:
        return (
            "<section class=\"panel\">"
            f"<h2>{escape(symbol)}</h2>"
            "<p class=\"muted\">No multi-timeframe candle data found yet.</p>"
            "</section>"
        )

    summary = build_multi_timeframe_signal_summary(
        symbol=symbol,
        guides_by_interval=guides_by_interval,
    )
    opinions = build_advisory_opinions(
        summary=summary,
        guides_by_interval=guides_by_interval,
    )
    if advisor != "all":
        opinions = tuple(opinion for opinion in opinions if opinion.bot.slug == advisor)
    if not opinions:
        opinions = build_advisory_opinions(
            summary=summary,
            guides_by_interval=guides_by_interval,
        )

    return (
        "<section class=\"panel\" style=\"margin-bottom:18px;\">"
        f"<h2>{escape(symbol)} Advisory Summary</h2>"
        f"<p><strong>Overall:</strong> {escape(summary.overall)} | "
        f"<strong>Bias:</strong> {escape(summary.bias)} | "
        f"<strong>Score:</strong> {summary.score}/100 | "
        f"<strong>Alignment:</strong> {escape(summary.alignment)}</p>"
        f"<p><strong>Consensus:</strong> {escape(build_consensus_summary(opinions))}</p>"
        "<p class=\"muted\">These are fixed-template advisor lenses from public candle signals. They are not AI-generated and cannot trade.</p>"
        "</section>"
        "<section class=\"advisory-grid\">"
        f"{''.join(_render_advisory_opinion(opinion) for opinion in opinions)}"
        "</section>"
    )


def _render_advisory_opinion(opinion: AdvisoryOpinion) -> str:
    return (
        "<article class=\"advisory-card\">"
        f"<h3>{escape(opinion.bot.name)}</h3>"
        f"<p class=\"muted\">{escape(opinion.bot.lens)}</p>"
        f"<p><strong>Verdict:</strong> {escape(opinion.verdict)} | "
        f"<strong>Confidence:</strong> {opinion.confidence}/100</p>"
        f"<p>{escape(opinion.outlook)}</p>"
        "<p class=\"muted\">Safety: advisory only; no orders; public data only.</p>"
        "</article>"
    )


def _render_algorithms_body(*, symbol: str, algorithm: str, label: str) -> str:
    interval_series = _load_multi_timeframe_series().get(symbol, {})
    guides_by_interval = {
        interval: guide
        for interval, series in interval_series.items()
        if (guide := _build_signal_guide(series)) is not None
    }
    if not guides_by_interval:
        return (
            "<section class=\"panel\">"
            f"<h2>{escape(symbol)}</h2>"
            "<p class=\"muted\">No multi-timeframe candle data found yet.</p>"
            "</section>"
        )

    summary = build_multi_timeframe_signal_summary(
        symbol=symbol,
        guides_by_interval=guides_by_interval,
    )
    if algorithm == "all":
        decisions = build_strategy_decisions(
            symbol=symbol,
            summary=summary,
            guides_by_interval=guides_by_interval,
            user_label=label,
        )
    else:
        strategy = strategy_by_slug(algorithm)
        decisions = (
            build_strategy_decision(
                strategy=strategy,
                symbol=symbol,
                summary=summary,
                guides_by_interval=guides_by_interval,
                user_label=label,
            ),
        )

    return (
        "<section class=\"panel\" style=\"margin-bottom:18px;\">"
        f"<h2>{escape(symbol)} Algorithm Summary</h2>"
        f"<p><strong>Overall:</strong> {escape(summary.overall)} | "
        f"<strong>Bias:</strong> {escape(summary.bias)} | "
        f"<strong>Score:</strong> {summary.score}/100 | "
        f"<strong>Alignment:</strong> {escape(summary.alignment)}</p>"
        "<p class=\"muted\">Algorithm output is advisory only. It cannot place trades, size orders, or access Binance order endpoints.</p>"
        "</section>"
        "<section class=\"advisory-grid\">"
        f"{''.join(_render_strategy_decision(decision) for decision in decisions)}"
        "</section>"
    )


def _render_strategy_decision(decision: StrategyDecision) -> str:
    label = (
        f"<p><strong>Run Label:</strong> {escape(decision.user_label)}</p>"
        if decision.user_label
        else ""
    )
    return (
        "<article class=\"advisory-card\">"
        f"<h3>{escape(decision.strategy.name)}</h3>"
        f"<p class=\"muted\">{escape(decision.strategy.style)}</p>"
        f"{label}"
        f"<p><strong>Verdict:</strong> {escape(decision.verdict)} | "
        f"<strong>Score:</strong> {decision.score}/100 | "
        f"<strong>Risk:</strong> {escape(decision.risk_level)}</p>"
        f"<p><strong>Mode:</strong> {escape(decision.mode)}</p>"
        f"<p>{escape(decision.thesis)}</p>"
        f"{_signal_list('Triggers Needed', decision.triggers)}"
        f"{_signal_list('Invalidation / Stop Conditions', decision.invalidation)}"
        f"{_signal_list('Rule Reasons', decision.reasons)}"
        "<p class=\"muted\">Safety: static algorithm only; no AI; no orders; no automatic trading.</p>"
        "</article>"
    )


def _render_account_snapshot_panel() -> str:
    config = load_binance_account_config_from_env()
    if config is None:
        return (
            "<section class=\"panel\">"
            "<h2>Account Snapshot</h2>"
            "<p class=\"muted\">BINANCE_API_KEY and BINANCE_API_SECRET are not configured.</p>"
            "<p>Safety: add read-only credentials only in .env. Do not enable trading or withdrawals.</p>"
            "</section>"
        )

    try:
        snapshot = fetch_account_snapshot(config=config)
    except BinanceAccountError as exc:
        return (
            "<section class=\"panel\">"
            "<h2>Account Snapshot</h2>"
            f"<p class=\"negative\">Could not fetch account snapshot: {escape(str(exc))}</p>"
            "<p class=\"muted\">No secret values are displayed or logged by this dashboard.</p>"
            "</section>"
        )

    return (
        "<section class=\"panel\" style=\"margin-bottom:18px;\">"
        "<h2>Account Snapshot</h2>"
        f"{_render_account_policy_panel(snapshot)}"
        "<section class=\"grid\">"
        f"{_metric_card('API Mode', 'Read-only')}"
        f"{_metric_card('Trading', 'Disabled by policy')}"
        f"{_metric_card('Withdrawals', 'Forbidden')}"
        f"{_metric_card('Futures / Margin', 'Forbidden')}"
        f"{_metric_card('Last Snapshot', _format_account_time(snapshot.fetched_at_ms))}"
        f"{_metric_card('Account Type', snapshot.account_type)}"
        f"{_metric_card('Binance canTrade Flag', str(snapshot.can_trade))}"
        f"{_metric_card('Binance canWithdraw Flag', str(snapshot.can_withdraw))}"
        f"{_metric_card('Permissions', ', '.join(snapshot.permissions) if snapshot.permissions else 'None')}"
        "</section>"
        "<p class=\"muted\">Read-only account endpoint only: GET /api/v3/account. No order endpoints are used. Binance capability flags are displayed for awareness only and do not override project policy.</p>"
        f"{_render_account_balances(snapshot)}"
        f"{_render_portfolio_valuation(snapshot)}"
        "</section>"
    )


def _render_account_policy_panel(snapshot: AccountSnapshot) -> str:
    warnings = []
    if snapshot.can_trade:
        warnings.append(
            "Binance reports canTrade=true. Treat this as an account capability flag; CoinPilot trading remains disabled."
        )
    if snapshot.can_withdraw:
        warnings.append(
            "Binance reports canWithdraw=true. Withdrawal permission is forbidden by project policy and must not be enabled on any API key."
        )
    warning_html = ""
    if warnings:
        items = "".join(f"<li>{escape(item)}</li>" for item in warnings)
        warning_html = f"<ul>{items}</ul>"
    else:
        warning_html = "<p class=\"status-good\">No trading or withdrawal capability flags were reported.</p>"
    return (
        "<div class=\"panel\" style=\"margin-bottom:14px;\">"
        "<strong>Project Safety Policy</strong>"
        "<ul>"
        "<li>API mode: read-only account visibility.</li>"
        "<li>Trading: disabled unless Michael uses the exact required approval phrase.</li>"
        "<li>Withdrawals, futures, margin, leverage, and borrowing: forbidden.</li>"
        "<li>Secrets are read from environment variables only and are never displayed.</li>"
        "</ul>"
        f"{warning_html}"
        "</div>"
    )


def _render_account_balances(snapshot: AccountSnapshot) -> str:
    if not snapshot.balances:
        return "<p class=\"muted\">No non-zero balances returned.</p>"
    rows = "".join(
        (
            "<tr>"
            f"<td>{escape(balance.asset)}</td>"
            f"<td>{escape(str(balance.free))}</td>"
            f"<td>{escape(str(balance.locked))}</td>"
            f"<td>{escape(str(balance.total))}</td>"
            "</tr>"
        )
        for balance in snapshot.balances
    )
    return (
        "<h3>Non-zero Balances</h3>"
        "<table>"
        "<thead><tr><th>Asset</th><th>Free</th><th>Locked</th><th>Total</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )


def _render_portfolio_valuation(snapshot: AccountSnapshot) -> str:
    valuation = _build_portfolio_valuation(snapshot)
    priced_count = sum(1 for row in valuation.rows if row.value_usdt is not None)
    unpriced_count = len(valuation.rows) - priced_count
    rows = "".join(_render_portfolio_valuation_row(row) for row in valuation.rows)
    if not rows:
        rows = "<tr><td colspan=\"6\" class=\"muted\">No non-zero balances to value.</td></tr>"
    return (
        "<h3>Estimated Portfolio Value</h3>"
        "<section class=\"grid\">"
        f"{_metric_card('Total Estimated Value', _format_usdt_value(valuation.total_usdt))}"
        f"{_metric_card('Priced Assets', str(priced_count))}"
        f"{_metric_card('Unpriced Assets', str(unpriced_count))}"
        "</section>"
        "<p class=\"muted\">Valuation uses public Binance spot prices only. It is an estimate, not an order quote, and it does not use trading endpoints.</p>"
        "<table>"
        "<thead><tr><th>Asset</th><th>Total</th><th>Public Price</th><th>Estimated Value</th><th>Allocation</th><th>Status</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )


def _render_portfolio_valuation_row(row: PortfolioBalanceValuation) -> str:
    return (
        "<tr>"
        f"<td>{escape(row.balance.asset)}</td>"
        f"<td>{escape(str(row.balance.total))}</td>"
        f"<td>{escape(_format_optional_usdt_price(row.price_usdt))}</td>"
        f"<td>{escape(_format_optional_usdt_value(row.value_usdt))}</td>"
        f"<td>{escape(_format_optional_percent(row.allocation_percent))}</td>"
        f"<td>{escape(row.pricing_note)}</td>"
        "</tr>"
    )


def _build_portfolio_valuation(snapshot: AccountSnapshot) -> PortfolioValuation:
    rows = [_value_balance(balance) for balance in snapshot.balances]
    total_usdt = sum(
        (row.value_usdt for row in rows if row.value_usdt is not None),
        Decimal("0"),
    )
    if total_usdt > 0:
        rows = [
            PortfolioBalanceValuation(
                balance=row.balance,
                price_usdt=row.price_usdt,
                value_usdt=row.value_usdt,
                allocation_percent=(row.value_usdt / total_usdt) * Decimal("100")
                if row.value_usdt is not None
                else None,
                pricing_note=row.pricing_note,
            )
            for row in rows
        ]
    rows.sort(
        key=lambda row: (
            row.value_usdt is None,
            -(row.value_usdt or Decimal("0")),
            row.balance.asset,
        )
    )
    return PortfolioValuation(total_usdt=total_usdt, rows=tuple(rows))


def _value_balance(balance: AccountBalance) -> PortfolioBalanceValuation:
    price_usdt, note = _price_asset_in_usdt(balance.asset)
    value_usdt = balance.total * price_usdt if price_usdt is not None else None
    return PortfolioBalanceValuation(
        balance=balance,
        price_usdt=price_usdt,
        value_usdt=value_usdt,
        allocation_percent=None,
        pricing_note=note,
    )


def _price_asset_in_usdt(asset: str) -> tuple[Decimal | None, str]:
    normalized_asset = asset.strip().upper()
    if normalized_asset == "USDT":
        return Decimal("1"), "USDT cash balance"
    symbol = f"{normalized_asset}USDT"
    try:
        price = fetch_public_prices((symbol,), timeout_seconds=5.0)[0].price
    except (BinancePublicMarketError, ValueError, IndexError):
        return None, f"No public {symbol} price"
    return price, f"Public {symbol} ticker"


def _format_usdt_value(value: Decimal) -> str:
    return f"{value:,.2f} USDT"


def _format_optional_usdt_value(value: Decimal | None) -> str:
    if value is None:
        return "Unavailable"
    return _format_usdt_value(value)


def _format_optional_usdt_price(value: Decimal | None) -> str:
    if value is None:
        return "Unavailable"
    if value >= Decimal("1"):
        return f"{value:,.2f} USDT"
    return f"{value:,.8f} USDT"


def _format_optional_percent(value: Decimal | None) -> str:
    if value is None:
        return "Unavailable"
    return f"{value:.2f}%"


def _format_account_time(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "Unavailable"
    return datetime.fromtimestamp(timestamp_ms / 1000, PHILIPPINE_TIMEZONE).isoformat()


def _render_signal_guides(series_list: tuple[ChartSeries, ...]) -> str:
    guides = []
    for series in series_list:
        if not series.candles:
            continue
        guide = _build_signal_guide(series)
        if guide is not None:
            guides.append(_render_signal_guide(guide))

    if not guides:
        return "<p class=\"muted\">No candle data found yet. Run the public candle collector first.</p>"
    return f"<div class=\"signal-grid\">{''.join(guides)}</div>"


def _build_signal_guide(series: ChartSeries) -> TechnicalSignalGuide | None:
    if not series.candles:
        return None
    highs = tuple(candle.high_price for candle in series.candles)
    lows = tuple(candle.low_price for candle in series.candles)
    closes = tuple(candle.close_price for candle in series.candles)
    volumes = tuple(candle.volume for candle in series.candles)
    snapshot = build_indicator_snapshot(
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
    )
    return build_technical_signal_guide(
        symbol=series.symbol,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        snapshot=snapshot,
    )


def _render_signal_guide(guide: TechnicalSignalGuide) -> str:
    trend = _signal_detail(
        "Trend",
        (
            ("Current Price", _format_optional_price(guide.current_price)),
            ("SMA50", _format_optional_price(guide.sma50)),
            ("EMA20", _format_optional_price(guide.ema20)),
            ("Price vs SMA50", guide.price_vs_sma50),
            ("Price vs EMA20", guide.price_vs_ema20),
            ("Trend Status", guide.trend_status),
            ("Distance from SMA50", _format_optional_percent(guide.distance_from_sma50_percent)),
        ),
    )
    momentum = _signal_detail(
        "Momentum",
        (
            ("RSI14", _format_optional_decimal(guide.rsi14)),
            ("RSI Status", guide.rsi_status),
            ("MACD", _format_optional_price(guide.macd)),
            ("MACD Signal", _format_optional_price(guide.macd_signal)),
            ("MACD Histogram", _format_optional_price(guide.macd_histogram)),
            ("MACD Status", guide.macd_status),
            ("Histogram Status", guide.macd_histogram_status),
        ),
    )
    volatility = _signal_detail(
        "Volatility",
        (
            ("ATR14", _format_optional_price(guide.atr14)),
            ("ATR%", _format_optional_percent(guide.atr_percent)),
            ("ATR Status", guide.atr_status),
            ("Bollinger Band Width", _format_optional_percent(guide.bollinger_band_width_percent)),
            ("Band Width Status", guide.bollinger_band_width_status),
            ("Squeeze Risk", guide.bollinger_squeeze),
        ),
    )
    bollinger = _signal_detail(
        "Bollinger Status",
        (
            ("Upper Band", _format_optional_price(guide.bollinger_upper)),
            ("Middle Band", _format_optional_price(guide.bollinger_middle)),
            ("Lower Band", _format_optional_price(guide.bollinger_lower)),
            ("Price Location", guide.bollinger_price_location),
            ("Squeeze", guide.bollinger_squeeze),
            ("Reversal Risk", guide.bollinger_reversal_risk),
        ),
    )
    key_levels = _signal_detail(
        "Key Levels",
        (
            ("Nearest Support", _format_optional_price(guide.nearest_support)),
            ("Nearest Resistance", _format_optional_price(guide.nearest_resistance)),
            ("Breakout Level", _format_optional_price(guide.breakout_level)),
            ("Breakdown Level", _format_optional_price(guide.breakdown_level)),
            ("Bullish Trigger", guide.bullish_trigger),
            ("Bearish Trigger", guide.bearish_trigger),
        ),
    )
    score_breakdown = _signal_detail(
        "Score Breakdown",
        _score_breakdown_items(guide.score_breakdown, guide.volume_vs_average_percent),
    )
    risk_guide = _signal_list(
        "Risk Guide",
        guide.risk_guide
        + (
            f"Conservative Stop Guide: {_format_optional_price(guide.conservative_stop_guide)}",
            f"Wide Stop Guide: {_format_optional_price(guide.wide_stop_guide)}",
            "Avoid trade if reward-to-risk is below 2:1.",
        ),
    )
    return (
        "<article class=\"signal-card\">"
        f"<h3>{escape(guide.symbol)}</h3>"
        "<div class=\"signal-head\">"
        f"{_signal_metric('Signal', guide.signal)}"
        f"{_signal_metric('Bias', guide.bias)}"
        f"{_signal_metric('Score', f'{guide.score}/100')}"
        f"{_signal_metric('Market Type', guide.market_type)}"
        f"{_signal_metric('Trade Quality', guide.trade_quality)}"
        f"{_signal_metric('Action', guide.action)}"
        "</div>"
        f"<p><strong>Plain English:</strong> {escape(guide.plain_english)}</p>"
        "<div class=\"signal-detail-grid\">"
        f"{trend}"
        f"{momentum}"
        f"{volatility}"
        f"{bollinger}"
        f"{key_levels}"
        f"{score_breakdown}"
        f"{_signal_list('Waiting For', guide.waiting_for)}"
        f"{risk_guide}"
        f"{_signal_list('Reasons', guide.reasons)}"
        "</div>"
        f"<p><strong>Final Decision:</strong> {escape(guide.final_decision)}</p>"
        "</article>"
    )


def _signal_metric(label: str, value: str) -> str:
    return (
        "<div class=\"signal-metric\">"
        f"<div class=\"signal-label\">{escape(label)}</div>"
        f"<div class=\"signal-value\">{escape(value)}</div>"
        "</div>"
    )


def _signal_detail(title: str, items: tuple[tuple[str, str], ...]) -> str:
    rows = "".join(
        f"<li><strong>{escape(label)}:</strong> {escape(value)}</li>"
        for label, value in items
    )
    return f"<div class=\"signal-detail\"><h3>{escape(title)}</h3><ul>{rows}</ul></div>"


def _signal_list(title: str, items: tuple[str, ...]) -> str:
    rows = "".join(f"<li>{escape(item)}</li>" for item in items)
    return f"<div class=\"signal-detail\"><h3>{escape(title)}</h3><ul>{rows}</ul></div>"


def _score_breakdown_items(
    score: ScoreBreakdown,
    volume_ratio: Decimal | None,
) -> tuple[tuple[str, str], ...]:
    volume_value = "Unavailable" if score.volume is None else f"{score.volume}/10"
    volume_note = "" if volume_ratio is None else f" ({volume_ratio:.0f}% of average)"
    return (
        ("Trend", f"{score.trend}/30"),
        ("Momentum", f"{score.momentum}/25"),
        ("Volatility/Risk", f"{score.volatility}/15"),
        ("Bollinger Setup", f"{score.bollinger}/10"),
        ("Structure", f"{score.structure}/10"),
        ("Volume", f"{volume_value}{volume_note}"),
        ("Total", f"{score.total}/100"),
    )


def _render_volume_panel(candles: tuple[ChartCandle, ...]) -> str:
    if not candles:
        return "<p class=\"muted\">No volume data found.</p>"

    width = Decimal("1000")
    height = Decimal("116")
    chart_left = Decimal("86")
    chart_right = Decimal("24")
    chart_top = Decimal("12")
    chart_bottom = Decimal("24")
    chart_width = width - chart_left - chart_right
    chart_height = height - chart_top - chart_bottom
    max_volume = max((candle.volume for candle in candles), default=Decimal("0"))
    bar_width = max(
        Decimal("3"),
        min(Decimal("9"), (chart_width / Decimal(len(candles))) * Decimal("0.58")),
    )
    bar_half_width = bar_width / Decimal("2")
    baseline_y = chart_top + chart_height

    bars = []
    for index, candle in enumerate(candles):
        x = chart_left if len(candles) == 1 else chart_left + (Decimal(index) / Decimal(len(candles) - 1)) * chart_width
        color = "#087f5b" if candle.close_price >= candle.open_price else "#c92a2a"
        if max_volume == 0 or candle.volume == 0:
            bar_height = Decimal("1")
        else:
            bar_height = max((candle.volume / max_volume) * chart_height, Decimal("1"))
        y = baseline_y - bar_height
        bars.append(
            f"<rect x=\"{(x - bar_half_width):.2f}\" y=\"{y:.2f}\" "
            f"width=\"{bar_width:.2f}\" height=\"{bar_height:.2f}\" "
            f"fill=\"{color}\" opacity=\"0.75\" />"
        )

    latest_volume = candles[-1].volume
    return (
        "<div class=\"volume-wrap\">"
        "<svg class=\"sparkline volume-chart\" viewBox=\"0 0 1000 116\" preserveAspectRatio=\"none\" role=\"img\">"
        f"<line x1=\"{chart_left:.2f}\" y1=\"{baseline_y:.2f}\" x2=\"{(width - chart_right):.2f}\" y2=\"{baseline_y:.2f}\" stroke=\"#b8c2d1\" stroke-width=\"1\" />"
        f"<text x=\"10\" y=\"{(chart_top + Decimal('4')):.2f}\" font-size=\"13\" fill=\"#647084\">Vol {_format_volume_axis(max_volume)}</text>"
        f"<text x=\"10\" y=\"{(baseline_y + Decimal('4')):.2f}\" font-size=\"13\" fill=\"#647084\">0</text>"
        f"{''.join(bars)}"
        f"<text x=\"{(width - chart_right):.2f}\" y=\"18\" font-size=\"13\" fill=\"#647084\" text-anchor=\"end\">Volume {_format_volume_axis(latest_volume)}</text>"
        "</svg>"
        "</div>"
    )


def _render_rsi_panel(candles: tuple[ChartCandle, ...]) -> str:
    closes = [candle.close_price for candle in candles]
    rsi_values = calculate_rsi_series(closes)
    if not any(value is not None for value in rsi_values):
        return "<p class=\"muted\">Not enough candles for RSI chart.</p>"

    width = Decimal("1000")
    height = Decimal("132")
    chart_left = Decimal("86")
    chart_right = Decimal("24")
    chart_top = Decimal("12")
    chart_bottom = Decimal("24")
    chart_width = width - chart_left - chart_right
    chart_height = height - chart_top - chart_bottom

    def point_for_rsi(index: int, value: Decimal) -> tuple[Decimal, Decimal]:
        x = chart_left + (Decimal(index) / Decimal(len(candles) - 1)) * chart_width
        y = chart_top + ((Decimal("100") - value) / Decimal("100")) * chart_height
        return x, y

    rsi_points = _series_points(
        tuple((index, value) for index, value in enumerate(rsi_values)),
        point_for_rsi,
    )
    latest_rsi = next((value for value in reversed(rsi_values) if value is not None), None)
    if latest_rsi is None:
        return "<p class=\"muted\">Not enough candles for RSI chart.</p>"

    reference_lines = "".join(
        _rsi_reference_line(level, chart_left, width - chart_right, chart_top, chart_height)
        for level in (Decimal("70"), Decimal("50"), Decimal("30"))
    )
    latest_x, latest_y = point_for_rsi(len(candles) - 1, latest_rsi)
    return (
        "<div class=\"rsi-wrap\">"
        "<svg class=\"sparkline rsi-chart\" viewBox=\"0 0 1000 132\" preserveAspectRatio=\"none\" role=\"img\">"
        f"{reference_lines}"
        f"<line x1=\"{chart_left:.2f}\" y1=\"{chart_top:.2f}\" x2=\"{chart_left:.2f}\" y2=\"{(chart_top + chart_height):.2f}\" stroke=\"#b8c2d1\" stroke-width=\"1\" />"
        f"<line x1=\"{chart_left:.2f}\" y1=\"{(chart_top + chart_height):.2f}\" x2=\"{(width - chart_right):.2f}\" y2=\"{(chart_top + chart_height):.2f}\" stroke=\"#b8c2d1\" stroke-width=\"1\" />"
        f"<polyline points=\"{rsi_points}\" fill=\"none\" stroke=\"#7c3aed\" stroke-width=\"2.5\" />"
        f"<circle cx=\"{latest_x:.2f}\" cy=\"{latest_y:.2f}\" r=\"4\" fill=\"#7c3aed\" />"
        f"<text x=\"{(width - chart_right):.2f}\" y=\"18\" font-size=\"13\" fill=\"#7c3aed\" text-anchor=\"end\">RSI 14 {_format_rsi_value(latest_rsi)}</text>"
        "</svg>"
        "</div>"
    )


def _render_macd_panel(candles: tuple[ChartCandle, ...]) -> str:
    closes = [candle.close_price for candle in candles]
    macd_values = calculate_macd_series(closes)
    if not any(histogram is not None for _, _, histogram in macd_values):
        return "<p class=\"muted macd-wrap\">Not enough candles for MACD chart.</p>"

    width = Decimal("1000")
    height = Decimal("132")
    chart_left = Decimal("86")
    chart_right = Decimal("24")
    chart_top = Decimal("12")
    chart_bottom = Decimal("24")
    chart_width = width - chart_left - chart_right
    chart_height = height - chart_top - chart_bottom
    all_values = [
        value
        for values in macd_values
        for value in values
        if value is not None
    ] + [Decimal("0")]
    low = min(all_values)
    high = max(all_values)
    spread = high - low
    if spread == 0:
        spread = Decimal("1")

    def point_for_value(index: int, value: Decimal) -> tuple[Decimal, Decimal]:
        x = chart_left + (Decimal(index) / Decimal(len(candles) - 1)) * chart_width
        y = chart_top + ((high - value) / spread) * chart_height
        return x, y

    zero_y = point_for_value(0, Decimal("0"))[1]
    macd_points = _series_points(
        tuple((index, values[0]) for index, values in enumerate(macd_values)),
        point_for_value,
    )
    signal_points = _series_points(
        tuple((index, values[1]) for index, values in enumerate(macd_values)),
        point_for_value,
    )
    bar_width = max(
        Decimal("3"),
        min(Decimal("9"), (chart_width / Decimal(len(candles))) * Decimal("0.58")),
    )
    bar_half_width = bar_width / Decimal("2")
    histogram_bars = []
    for index, (_, _, histogram) in enumerate(macd_values):
        if histogram is None:
            continue
        x, histogram_y = point_for_value(index, histogram)
        y = min(zero_y, histogram_y)
        bar_height = max(abs(zero_y - histogram_y), Decimal("1"))
        color = "#087f5b" if histogram >= 0 else "#c92a2a"
        histogram_bars.append(
            f"<rect x=\"{(x - bar_half_width):.2f}\" y=\"{y:.2f}\" "
            f"width=\"{bar_width:.2f}\" height=\"{bar_height:.2f}\" "
            f"fill=\"{color}\" opacity=\"0.55\" />"
        )

    latest_macd, latest_signal, latest_histogram = next(
        values for values in reversed(macd_values) if values[2] is not None
    )
    latest_text = (
        f"MACD {_format_axis_price(latest_macd)} | "
        f"Signal {_format_axis_price(latest_signal)} | "
        f"Hist {_format_axis_price(latest_histogram)}"
    )
    return (
        "<div class=\"macd-wrap\">"
        "<svg class=\"sparkline indicator-chart macd-chart\" viewBox=\"0 0 1000 132\" preserveAspectRatio=\"none\" role=\"img\">"
        f"<line x1=\"{chart_left:.2f}\" y1=\"{zero_y:.2f}\" x2=\"{(width - chart_right):.2f}\" y2=\"{zero_y:.2f}\" stroke=\"#b8c2d1\" stroke-width=\"1\" stroke-dasharray=\"5 5\" />"
        f"<line x1=\"{chart_left:.2f}\" y1=\"{chart_top:.2f}\" x2=\"{chart_left:.2f}\" y2=\"{(chart_top + chart_height):.2f}\" stroke=\"#b8c2d1\" stroke-width=\"1\" />"
        f"<line x1=\"{chart_left:.2f}\" y1=\"{(chart_top + chart_height):.2f}\" x2=\"{(width - chart_right):.2f}\" y2=\"{(chart_top + chart_height):.2f}\" stroke=\"#b8c2d1\" stroke-width=\"1\" />"
        f"<text x=\"10\" y=\"{(chart_top + Decimal('4')):.2f}\" font-size=\"13\" fill=\"#647084\">{escape(_format_axis_price(high))}</text>"
        f"<text x=\"10\" y=\"{(zero_y + Decimal('4')):.2f}\" font-size=\"13\" fill=\"#647084\">0</text>"
        f"<text x=\"10\" y=\"{(chart_top + chart_height + Decimal('4')):.2f}\" font-size=\"13\" fill=\"#647084\">{escape(_format_axis_price(low))}</text>"
        f"{''.join(histogram_bars)}"
        f"<polyline points=\"{macd_points}\" fill=\"none\" stroke=\"#1f6feb\" stroke-width=\"2.2\" />"
        f"<polyline points=\"{signal_points}\" fill=\"none\" stroke=\"#f59e0b\" stroke-width=\"2.2\" />"
        f"<text x=\"{(width - chart_right):.2f}\" y=\"18\" font-size=\"13\" fill=\"#1f6feb\" text-anchor=\"end\">{escape(latest_text)}</text>"
        "</svg>"
        "</div>"
    )


def _render_atr_panel(candles: tuple[ChartCandle, ...]) -> str:
    highs = [candle.high_price for candle in candles]
    lows = [candle.low_price for candle in candles]
    closes = [candle.close_price for candle in candles]
    atr_values = calculate_atr_series(highs=highs, lows=lows, closes=closes)
    if not any(value is not None for value in atr_values):
        return "<p class=\"muted atr-wrap\">Not enough candles for ATR chart.</p>"

    width = Decimal("1000")
    height = Decimal("132")
    chart_left = Decimal("86")
    chart_right = Decimal("24")
    chart_top = Decimal("12")
    chart_bottom = Decimal("24")
    chart_width = width - chart_left - chart_right
    chart_height = height - chart_top - chart_bottom
    high = max(value for value in atr_values if value is not None)
    low = Decimal("0")
    spread = high - low
    if spread == 0:
        spread = Decimal("1")

    def point_for_atr(index: int, value: Decimal) -> tuple[Decimal, Decimal]:
        x = chart_left + (Decimal(index) / Decimal(len(candles) - 1)) * chart_width
        y = chart_top + ((high - value) / spread) * chart_height
        return x, y

    atr_points = _series_points(
        tuple((index, value) for index, value in enumerate(atr_values)),
        point_for_atr,
    )
    latest_atr = next((value for value in reversed(atr_values) if value is not None), None)
    if latest_atr is None:
        return "<p class=\"muted atr-wrap\">Not enough candles for ATR chart.</p>"

    latest_x, latest_y = point_for_atr(len(candles) - 1, latest_atr)
    return (
        "<div class=\"atr-wrap\">"
        "<svg class=\"sparkline indicator-chart atr-chart\" viewBox=\"0 0 1000 132\" preserveAspectRatio=\"none\" role=\"img\">"
        f"<line x1=\"{chart_left:.2f}\" y1=\"{chart_top:.2f}\" x2=\"{chart_left:.2f}\" y2=\"{(chart_top + chart_height):.2f}\" stroke=\"#b8c2d1\" stroke-width=\"1\" />"
        f"<line x1=\"{chart_left:.2f}\" y1=\"{(chart_top + chart_height):.2f}\" x2=\"{(width - chart_right):.2f}\" y2=\"{(chart_top + chart_height):.2f}\" stroke=\"#b8c2d1\" stroke-width=\"1\" />"
        f"<text x=\"10\" y=\"{(chart_top + Decimal('4')):.2f}\" font-size=\"13\" fill=\"#647084\">{escape(_format_axis_price(high))}</text>"
        f"<text x=\"10\" y=\"{(chart_top + chart_height + Decimal('4')):.2f}\" font-size=\"13\" fill=\"#647084\">0</text>"
        f"<polyline points=\"{atr_points}\" fill=\"none\" stroke=\"#9333ea\" stroke-width=\"2.4\" />"
        f"<circle cx=\"{latest_x:.2f}\" cy=\"{latest_y:.2f}\" r=\"4\" fill=\"#9333ea\" />"
        f"<text x=\"{(width - chart_right):.2f}\" y=\"18\" font-size=\"13\" fill=\"#9333ea\" text-anchor=\"end\">ATR 14 {_format_axis_price(latest_atr)}</text>"
        "</svg>"
        "</div>"
    )


def _render_sparkline(candles: tuple[ChartCandle, ...]) -> str:
    if len(candles) < 2:
        return "<p class=\"muted\">Not enough candles for chart.</p>"

    width = Decimal("1000")
    height = Decimal("260")
    chart_left = Decimal("86")
    chart_right = Decimal("24")
    chart_top = Decimal("18")
    chart_bottom = Decimal("44")
    chart_width = width - chart_left - chart_right
    chart_height = height - chart_top - chart_bottom
    closes = [candle.close_price for candle in candles]
    ema_values = calculate_ema_series(closes, 20)
    sma_values = calculate_sma_series(closes, 50)
    candle_prices = [
        price
        for candle in candles
        for price in (
            candle.open_price,
            candle.high_price,
            candle.low_price,
            candle.close_price,
        )
    ]
    bollinger_bands = calculate_bollinger_band_series(closes)
    bollinger_values = [
        value
        for bands in bollinger_bands
        for value in bands
        if value is not None
    ]
    moving_average_values = [value for value in (*ema_values, *sma_values) if value is not None]
    low = min(candle_prices + bollinger_values + moving_average_values)
    high = max(candle_prices + bollinger_values + moving_average_values)
    spread = high - low
    if spread == 0:
        spread = Decimal("1")

    def point_for_value(index: int, value: Decimal) -> tuple[Decimal, Decimal]:
        x = chart_left + (Decimal(index) / Decimal(len(closes) - 1)) * chart_width
        y = chart_top + ((high - value) / spread) * chart_height
        return x, y

    candle_width = max(
        Decimal("3"),
        min(Decimal("9"), (chart_width / Decimal(len(candles))) * Decimal("0.58")),
    )
    candle_half_width = candle_width / Decimal("2")
    candle_elements = []
    point_metadata = []
    for index, candle in enumerate(candles):
        x, close_y = point_for_value(index, candle.close_price)
        _, open_y = point_for_value(index, candle.open_price)
        _, high_y = point_for_value(index, candle.high_price)
        _, low_y = point_for_value(index, candle.low_price)
        color = "#087f5b" if candle.close_price >= candle.open_price else "#c92a2a"
        body_y = min(open_y, close_y)
        body_height = max(abs(close_y - open_y), Decimal("1.5"))
        candle_elements.append(
            f"<line x1=\"{x:.2f}\" y1=\"{high_y:.2f}\" x2=\"{x:.2f}\" y2=\"{low_y:.2f}\" "
            f"stroke=\"{color}\" stroke-width=\"1.4\" />"
            f"<rect x=\"{(x - candle_half_width):.2f}\" y=\"{body_y:.2f}\" "
            f"width=\"{candle_width:.2f}\" height=\"{body_height:.2f}\" "
            f"fill=\"{color}\" opacity=\"0.82\" />"
        )
        point_metadata.append(
            {
                "x": float(x),
                "y": float(close_y),
                "time": _format_candle_time(candle.open_time_ms),
                "open": _format_axis_price(candle.open_price),
                "high": _format_axis_price(candle.high_price),
                "low": _format_axis_price(candle.low_price),
                "close": _format_axis_price(candle.close_price),
                "volume": str(candle.volume),
            }
        )

    price_ticks = tuple(
        (
            high - ((spread / Decimal("6")) * Decimal(index)),
            chart_top + ((chart_height / Decimal("6")) * Decimal(index)),
        )
        for index in range(7)
    )
    price_grid = "".join(
        (
            f"<line x1=\"{chart_left:.2f}\" y1=\"{y:.2f}\" x2=\"{(width - chart_right):.2f}\" y2=\"{y:.2f}\" "
            "stroke=\"#d9e0ea\" stroke-width=\"1\" />"
            f"<text x=\"10\" y=\"{(y + Decimal('4')):.2f}\" font-size=\"13\" fill=\"#647084\">"
            f"{escape(_format_axis_price(price))}</text>"
        )
        for price, y in price_ticks
    )

    x_ticks = tuple(
        (
            chart_left + ((chart_width / Decimal("6")) * Decimal(index)),
            _format_axis_time(candles[round((len(candles) - 1) * (index / 6))].open_time_ms),
            _axis_label_anchor(index),
        )
        for index in range(7)
    )
    time_labels = "".join(
        (
            f"<text x=\"{x:.2f}\" y=\"238\" font-size=\"13\" fill=\"#647084\" "
            f"text-anchor=\"{anchor}\">{escape(label)}</text>"
        )
        for x, label, anchor in x_ticks
    )

    bollinger_upper_points = _series_points(
        tuple((index, bands[0]) for index, bands in enumerate(bollinger_bands)),
        point_for_value,
    )
    bollinger_middle_points = _series_points(
        tuple((index, bands[1]) for index, bands in enumerate(bollinger_bands)),
        point_for_value,
    )
    bollinger_lower_points = _series_points(
        tuple((index, bands[2]) for index, bands in enumerate(bollinger_bands)),
        point_for_value,
    )
    bollinger_overlay = (
        "<g class=\"bollinger-overlay\">"
        f"<polyline points=\"{bollinger_upper_points}\" fill=\"none\" stroke=\"#c2410c\" stroke-width=\"2\" stroke-dasharray=\"7 5\" opacity=\"0.8\" />"
        f"<polyline points=\"{bollinger_middle_points}\" fill=\"none\" stroke=\"#475569\" stroke-width=\"2\" opacity=\"0.65\" />"
        f"<polyline points=\"{bollinger_lower_points}\" fill=\"none\" stroke=\"#c2410c\" stroke-width=\"2\" stroke-dasharray=\"7 5\" opacity=\"0.8\" />"
        "</g>"
        if bollinger_upper_points and bollinger_middle_points and bollinger_lower_points
        else ""
    )
    ema_points = _series_points(
        tuple((index, value) for index, value in enumerate(ema_values)),
        point_for_value,
    )
    sma_points = _series_points(
        tuple((index, value) for index, value in enumerate(sma_values)),
        point_for_value,
    )
    moving_average_overlay = (
        f"<polyline class=\"ema-overlay\" points=\"{ema_points}\" fill=\"none\" stroke=\"#f59e0b\" stroke-width=\"2.4\" opacity=\"0.9\" />"
        if ema_points
        else ""
    ) + (
        f"<polyline class=\"sma-overlay\" points=\"{sma_points}\" fill=\"none\" stroke=\"#0f766e\" stroke-width=\"2.4\" opacity=\"0.9\" />"
        if sma_points
        else ""
    )
    legend = (
        "<g>"
        "<line x1=\"604\" y1=\"20\" x2=\"634\" y2=\"20\" stroke=\"#f59e0b\" stroke-width=\"2.4\" />"
        "<text x=\"642\" y=\"24\" font-size=\"13\" fill=\"#647084\">EMA20</text>"
        "<line x1=\"704\" y1=\"20\" x2=\"734\" y2=\"20\" stroke=\"#0f766e\" stroke-width=\"2.4\" />"
        "<text x=\"742\" y=\"24\" font-size=\"13\" fill=\"#647084\">SMA50</text>"
        "<line x1=\"804\" y1=\"20\" x2=\"834\" y2=\"20\" stroke=\"#c2410c\" stroke-width=\"2\" stroke-dasharray=\"7 5\" />"
        "<text x=\"842\" y=\"24\" font-size=\"13\" fill=\"#647084\">Bollinger</text>"
        "</g>"
    )

    latest_close = closes[-1]
    latest_x, latest_y = point_for_value(len(closes) - 1, latest_close)
    return (
        f"{_render_chart_controls()}"
        "<div class=\"chart-wrap\" "
        f"data-points=\"{escape(json.dumps(point_metadata, separators=(',', ':')))}\">"
        "<svg class=\"sparkline\" viewBox=\"0 0 1000 260\" preserveAspectRatio=\"none\" role=\"img\">"
        f"{price_grid}"
        f"<line x1=\"{chart_left:.2f}\" y1=\"{chart_top:.2f}\" x2=\"{chart_left:.2f}\" y2=\"{(chart_top + chart_height):.2f}\" stroke=\"#b8c2d1\" stroke-width=\"1\" />"
        f"<line x1=\"{chart_left:.2f}\" y1=\"{(chart_top + chart_height):.2f}\" x2=\"{(width - chart_right):.2f}\" y2=\"{(chart_top + chart_height):.2f}\" stroke=\"#b8c2d1\" stroke-width=\"1\" />"
        f"<line class=\"hover-guide\" x1=\"0\" y1=\"{chart_top:.2f}\" x2=\"0\" y2=\"{(chart_top + chart_height):.2f}\" stroke=\"#087f5b\" stroke-width=\"1\" opacity=\"0\" />"
        f"{bollinger_overlay}"
        f"{moving_average_overlay}"
        f"{''.join(candle_elements)}"
        f"<circle cx=\"{latest_x:.2f}\" cy=\"{latest_y:.2f}\" r=\"5\" fill=\"#1f6feb\" />"
        "<circle class=\"hover-marker\" cx=\"0\" cy=\"0\" r=\"6\" fill=\"#087f5b\" stroke=\"#ffffff\" stroke-width=\"2\" opacity=\"0\" />"
        f"<text x=\"{(latest_x - Decimal('8')):.2f}\" y=\"{(latest_y - Decimal('10')):.2f}\" font-size=\"13\" fill=\"#1f6feb\" text-anchor=\"end\">"
        f"{escape(_format_axis_price(latest_close))}</text>"
        f"{legend}"
        f"{time_labels}"
        "</svg>"
        "<div class=\"chart-tooltip\"></div>"
        "</div>"
    )


def _render_chart_controls() -> str:
    return (
        "<div class=\"chart-controls\">"
        "<label><input class=\"chart-toggle\" type=\"checkbox\" data-overlay=\"bollinger\" checked> Bollinger</label>"
        "<label><input class=\"chart-toggle\" type=\"checkbox\" data-overlay=\"ema\" checked> EMA20</label>"
        "<label><input class=\"chart-toggle\" type=\"checkbox\" data-overlay=\"sma\" checked> SMA50</label>"
        "<label><input class=\"chart-toggle\" type=\"checkbox\" data-overlay=\"macd\" checked> MACD</label>"
        "<label><input class=\"chart-toggle\" type=\"checkbox\" data-overlay=\"atr\" checked> ATR14</label>"
        "</div>"
    )


def _series_points(
    values: tuple[tuple[int, Decimal | None], ...],
    point_for_value: Callable[[int, Decimal], tuple[Decimal, Decimal]],
) -> str:
    points = []
    for index, value in values:
        if value is None:
            continue
        x, y = point_for_value(index, value)
        points.append(f"{x:.2f},{y:.2f}")
    return " ".join(points)


def _rsi_reference_line(
    level: Decimal,
    chart_left: Decimal,
    chart_right_x: Decimal,
    chart_top: Decimal,
    chart_height: Decimal,
) -> str:
    y = chart_top + ((Decimal("100") - level) / Decimal("100")) * chart_height
    color = "#c92a2a" if level == Decimal("70") else "#087f5b" if level == Decimal("30") else "#d9e0ea"
    return (
        f"<line x1=\"{chart_left:.2f}\" y1=\"{y:.2f}\" x2=\"{chart_right_x:.2f}\" y2=\"{y:.2f}\" "
        f"stroke=\"{color}\" stroke-width=\"1\" stroke-dasharray=\"5 5\" opacity=\"0.85\" />"
        f"<text x=\"10\" y=\"{(y + Decimal('4')):.2f}\" font-size=\"13\" fill=\"#647084\">RSI {level:.0f}</text>"
    )


def _format_candle_time(open_time_ms: int) -> str:
    return datetime.fromtimestamp(open_time_ms / 1000, tz=PHILIPPINE_TIMEZONE).isoformat()


def _format_axis_time(open_time_ms: int) -> str:
    return datetime.fromtimestamp(open_time_ms / 1000, tz=PHILIPPINE_TIMEZONE).strftime("%m-%d %H:%M")


def _format_axis_price(price: Decimal) -> str:
    if price >= Decimal("100"):
        return f"{price:.2f}"
    if price >= Decimal("1"):
        return f"{price:.4f}"
    return f"{price:.6f}"


def _format_optional_price(price: Decimal | None) -> str:
    if price is None:
        return "Unavailable"
    return _format_axis_price(price)


def _format_optional_decimal(value: Decimal | None) -> str:
    if value is None:
        return "Unavailable"
    return f"{value:.2f}"


def _format_indicator_value(value: Decimal | None) -> str:
    if value is None:
        return "Not enough data"
    return _format_axis_price(value)


def _format_rsi_value(value: Decimal) -> str:
    return f"{value:.1f}"


def _format_volume_axis(value: Decimal) -> str:
    if value >= Decimal("1000000"):
        return f"{(value / Decimal('1000000')):.2f}M"
    if value >= Decimal("1000"):
        return f"{(value / Decimal('1000')):.2f}K"
    return f"{value:.2f}"


def _format_bollinger_value(snapshot: IndicatorSnapshot) -> str:
    if (
        snapshot.bollinger_lower is None
        or snapshot.bollinger_middle is None
        or snapshot.bollinger_upper is None
        or snapshot.bollinger_percent_b is None
    ):
        return "Not enough data"
    return (
        f"%B {snapshot.bollinger_percent_b:.1f} | "
        f"L {_format_axis_price(snapshot.bollinger_lower)} | "
        f"M {_format_axis_price(snapshot.bollinger_middle)} | "
        f"U {_format_axis_price(snapshot.bollinger_upper)}"
    )


def _format_macd_value(snapshot: IndicatorSnapshot) -> str:
    if snapshot.macd is None or snapshot.macd_signal is None or snapshot.macd_histogram is None:
        return "Not enough data"
    return (
        f"M {_format_axis_price(snapshot.macd)} | "
        f"S {_format_axis_price(snapshot.macd_signal)} | "
        f"H {_format_axis_price(snapshot.macd_histogram)}"
    )


def _format_volume_value(snapshot: IndicatorSnapshot) -> str:
    if snapshot.volume is None or snapshot.average_volume is None or snapshot.volume_ratio is None:
        return "Not enough data"
    return (
        f"Now {snapshot.volume:.4f} | "
        f"Avg {snapshot.average_volume:.4f} | "
        f"{snapshot.volume_ratio:.0f}%"
    )


def _format_atr_value(snapshot: IndicatorSnapshot) -> str:
    if snapshot.atr is None or snapshot.atr_percent is None:
        return "Not enough data"
    return f"{_format_axis_price(snapshot.atr)} | {snapshot.atr_percent:.2f}%"


def _axis_label_anchor(index: int) -> str:
    if index == 0:
        return "start"
    if index == 6:
        return "end"
    return "middle"


def _chart_interaction_script() -> str:
    return r"""
    (() => {
      const viewBoxWidth = 1000;

      function nearestPoint(points, x) {
        let selected = points[0];
        let selectedDistance = Math.abs(points[0].x - x);
        for (const point of points) {
          const distance = Math.abs(point.x - x);
          if (distance < selectedDistance) {
            selected = point;
            selectedDistance = distance;
          }
        }
        return selected;
      }

      function showPoint(wrapper, point, clientX, clientY) {
        const guide = wrapper.querySelector(".hover-guide");
        const marker = wrapper.querySelector(".hover-marker");
        const tooltip = wrapper.querySelector(".chart-tooltip");
        if (!guide || !marker || !tooltip) return;

        guide.setAttribute("x1", point.x);
        guide.setAttribute("x2", point.x);
        guide.setAttribute("opacity", "0.85");
        marker.setAttribute("cx", point.x);
        marker.setAttribute("cy", point.y);
        marker.setAttribute("opacity", "1");
        tooltip.innerHTML = `
          <strong>Close ${point.close}</strong><br>
          Open ${point.open} | High ${point.high}<br>
          Low ${point.low} | Volume ${point.volume}<br>
          ${point.time}
        `;
        tooltip.style.display = "block";

        const wrapperRect = wrapper.getBoundingClientRect();
        const tooltipLeft = Math.min(
          Math.max(clientX - wrapperRect.left + 14, 8),
          Math.max(8, wrapperRect.width - tooltip.offsetWidth - 8)
        );
        const tooltipTop = Math.min(
          Math.max(clientY - wrapperRect.top - tooltip.offsetHeight - 10, 8),
          Math.max(8, wrapperRect.height - tooltip.offsetHeight - 8)
        );
        tooltip.style.left = `${tooltipLeft}px`;
        tooltip.style.top = `${tooltipTop}px`;
      }

      function hidePoint(wrapper) {
        const guide = wrapper.querySelector(".hover-guide");
        const marker = wrapper.querySelector(".hover-marker");
        const tooltip = wrapper.querySelector(".chart-tooltip");
        if (guide) guide.setAttribute("opacity", "0");
        if (marker) marker.setAttribute("opacity", "0");
        if (tooltip) tooltip.style.display = "none";
      }

      for (const wrapper of document.querySelectorAll(".chart-wrap")) {
        const points = JSON.parse(wrapper.dataset.points || "[]");
        const svg = wrapper.querySelector("svg");
        if (!points.length || !svg) continue;

        const update = (event) => {
          const rect = svg.getBoundingClientRect();
          const x = ((event.clientX - rect.left) / rect.width) * viewBoxWidth;
          showPoint(wrapper, nearestPoint(points, x), event.clientX, event.clientY);
        };

        svg.addEventListener("mousemove", update);
        svg.addEventListener("mouseleave", () => hidePoint(wrapper));
        svg.addEventListener("touchstart", (event) => {
          if (event.touches.length) update(event.touches[0]);
        }, { passive: true });
        svg.addEventListener("touchmove", (event) => {
          if (event.touches.length) update(event.touches[0]);
        }, { passive: true });
      }

      for (const controls of document.querySelectorAll(".chart-controls")) {
        const wrapper = controls.nextElementSibling;
        if (!wrapper || !wrapper.classList.contains("chart-wrap")) continue;
        for (const checkbox of controls.querySelectorAll(".chart-toggle")) {
          const apply = () => {
            wrapper.classList.toggle(`hide-${checkbox.dataset.overlay}`, !checkbox.checked);
            const section = controls.closest(".chart-section");
            if (section) {
              section.classList.toggle(`hide-${checkbox.dataset.overlay}`, !checkbox.checked);
            }
          };
          checkbox.addEventListener("change", apply);
          apply();
        }
      }
    })();
    """


def _is_auth_required() -> bool:
    return bool(os.environ.get("DASHBOARD_PASSWORD", ""))


def _dashboard_username() -> str:
    return os.environ.get("DASHBOARD_USERNAME", "admin") or "admin"


def _dashboard_password() -> str:
    return os.environ.get("DASHBOARD_PASSWORD", "")


def _authenticate_credentials(username: str, password: str) -> bool:
    if not _is_auth_required():
        return True
    return hmac.compare_digest(username, _dashboard_username()) and hmac.compare_digest(
        password,
        _dashboard_password(),
    )


def _build_session_cookie(username: str) -> str:
    expires_at = int(time.time()) + DASHBOARD_SESSION_TTL_SECONDS
    payload = f"{username}:{expires_at}"
    signature = _sign_payload(payload)
    token = base64.urlsafe_b64encode(f"{payload}:{signature}".encode("utf-8")).decode("ascii")
    return (
        f"{DASHBOARD_SESSION_COOKIE}={token}; "
        "HttpOnly; SameSite=Lax; Path=/; "
        f"Max-Age={DASHBOARD_SESSION_TTL_SECONDS}"
    )


def _is_authenticated(cookie_header: str) -> bool:
    if not _is_auth_required():
        return True
    cookies = _parse_cookies(cookie_header)
    token = cookies.get(DASHBOARD_SESSION_COOKIE)
    if not token:
        return False
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False

    parts = decoded.rsplit(":", 2)
    if len(parts) != 3:
        return False
    username, expires_at_text, signature = parts
    payload = f"{username}:{expires_at_text}"
    if not hmac.compare_digest(signature, _sign_payload(payload)):
        return False
    if not hmac.compare_digest(username, _dashboard_username()):
        return False
    try:
        expires_at = int(expires_at_text)
    except ValueError:
        return False
    return expires_at > int(time.time())


def _sign_payload(payload: str) -> str:
    return hmac.new(
        _dashboard_password().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _parse_cookies(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in cookie_header.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        cookies[key.strip()] = value.strip()
    return cookies


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


def _parse_chart_interval(value: str) -> str:
    if value in CHART_INTERVALS:
        return value
    return DEFAULT_CHART_INTERVAL


def _parse_advisory_symbol(value: str) -> str:
    return _parse_watchlist_symbol(value)


def _parse_watchlist_symbol(value: str) -> str:
    if value in PUBLIC_MARKET_WATCHLIST:
        return value
    return PUBLIC_MARKET_WATCHLIST[0]
