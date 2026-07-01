"""Command-line entrypoint for the read-only Binance public market monitor."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from app.candle_collector import DEFAULT_CANDLE_FETCH_LIMIT, run_candle_collection_once
from app.candle_store import (
    DEFAULT_CANDLE_RETENTION_DAYS,
    format_candle_store_stats,
    run_candle_db_maintenance,
)
from app.dashboard import DEFAULT_DASHBOARD_HOST, DEFAULT_DASHBOARD_PORT, run_dashboard_server
from app.monitor import run_once, run_watch
from app.summary import DEFAULT_SUMMARY_HOURS, build_market_summary, format_market_summary
from app.telegram_notifier import TelegramSendError, send_summary_to_telegram


DEFAULT_WATCH_INTERVAL_SECONDS = 60
DEFAULT_ALERT_THRESHOLD_PERCENT = Decimal("1.0")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments without fetching market data."""

    parser = argparse.ArgumentParser(
        description=(
            "Read-only Binance public market monitor. Uses public ticker data only; "
            "no API key, no account access, and no orders."
        )
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Run continuously until Ctrl+C.",
    )
    parser.add_argument(
        "--interval",
        type=_positive_int,
        default=DEFAULT_WATCH_INTERVAL_SECONDS,
        metavar="N",
        help="Watch interval in seconds. Default: 60.",
    )
    parser.add_argument(
        "--alert-threshold",
        type=_non_negative_decimal,
        default=DEFAULT_ALERT_THRESHOLD_PERCENT,
        metavar="N",
        help="Price-change alert threshold percentage for watch mode. Default: 1.0.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Build a summary from local market price logs without fetching Binance data.",
    )
    parser.add_argument(
        "--summary-hours",
        type=_positive_int,
        default=DEFAULT_SUMMARY_HOURS,
        metavar="N",
        help="Summary lookback period in hours. Default: 24.",
    )
    parser.add_argument(
        "--send-telegram",
        action="store_true",
        help="Send summary mode output to Telegram when Telegram env vars are configured.",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Run a local read-only dashboard from market price logs.",
    )
    parser.add_argument(
        "--dashboard-host",
        default=DEFAULT_DASHBOARD_HOST,
        metavar="HOST",
        help="Dashboard bind host. Default: 127.0.0.1.",
    )
    parser.add_argument(
        "--dashboard-port",
        type=_positive_int,
        default=DEFAULT_DASHBOARD_PORT,
        metavar="PORT",
        help="Dashboard port. Default: 8765.",
    )
    parser.add_argument(
        "--collect-candles",
        action="store_true",
        help="Fetch public Binance klines once and store them in local SQLite.",
    )
    parser.add_argument(
        "--candle-limit",
        type=_positive_int,
        default=DEFAULT_CANDLE_FETCH_LIMIT,
        metavar="N",
        help="Candles to fetch per symbol/interval. Default: 100. Maximum: 1000.",
    )
    parser.add_argument(
        "--retention-days",
        type=_positive_int,
        default=DEFAULT_CANDLE_RETENTION_DAYS,
        metavar="N",
        help="Candle retention window in days. Default: 90.",
    )
    parser.add_argument(
        "--db-maintenance",
        action="store_true",
        help="Clean old candle rows from SQLite without fetching Binance data.",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Run SQLite VACUUM during --db-maintenance.",
    )
    return parser.parse_args(argv)


def run_summary(summary_hours: int, *, send_telegram: bool) -> int:
    """Build and optionally send a summary from the local market log."""

    try:
        summary = build_market_summary(summary_hours=summary_hours)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    summary_text = format_market_summary(summary)
    print(summary_text)

    if send_telegram:
        try:
            sent = send_summary_to_telegram(summary_text)
        except TelegramSendError as exc:
            print(f"Telegram summary send failed: {exc}")
            return 1

        if sent:
            print("Telegram summary sent.")
        else:
            print(
                "Warning: Telegram summary not sent because TELEGRAM_BOT_TOKEN "
                "and TELEGRAM_CHAT_ID are not both configured."
            )

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the read-only public market monitor."""

    args = parse_args(argv)
    if args.candle_limit > 1000:
        print("Error: --candle-limit must be 1000 or less.")
        return 1

    if args.dashboard:
        run_dashboard_server(host=args.dashboard_host, port=args.dashboard_port)
        return 0

    if args.db_maintenance:
        stats = run_candle_db_maintenance(
            retention_days=args.retention_days,
            vacuum=args.vacuum,
        )
        print(format_candle_store_stats(stats))
        return 0

    if args.collect_candles:
        return run_candle_collection_once(
            limit=args.candle_limit,
            retention_days=args.retention_days,
        )

    if args.summary:
        return run_summary(args.summary_hours, send_telegram=args.send_telegram)

    if args.watch:
        return run_watch(args.interval, args.alert_threshold)
    return run_once()


def _positive_int(value: str) -> int:
    parsed_value = int(value)
    if parsed_value <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed_value


def _non_negative_decimal(value: str) -> Decimal:
    try:
        parsed_value = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("value must be a number") from exc

    if parsed_value < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return parsed_value


if __name__ == "__main__":
    raise SystemExit(main())
