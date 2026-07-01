"""Collect public Binance candles into the local SQLite store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from app.binance_reader import BinancePublicMarketError, fetch_public_candles
from app.candle_store import (
    DEFAULT_CANDLE_DB_PATH,
    DEFAULT_CANDLE_RETENTION_DAYS,
    cleanup_old_candles,
    count_candles,
    initialize_candle_store,
    upsert_candles,
)
from app.config import PUBLIC_MARKET_WATCHLIST
from app.health import (
    CANDLE_COLLECTOR_HEALTH_PATH,
    write_error_heartbeat,
    write_success_heartbeat,
)


DEFAULT_CANDLE_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
DEFAULT_CANDLE_FETCH_LIMIT = 100


@dataclass(frozen=True)
class CandleCollectionResult:
    symbols: tuple[str, ...]
    intervals: tuple[str, ...]
    fetch_limit: int
    fetched_rows: int
    upserted_rows: int
    deleted_rows: int
    total_rows: int
    db_path: Path


def collect_public_candles_once(
    *,
    symbols: tuple[str, ...] = PUBLIC_MARKET_WATCHLIST,
    intervals: tuple[str, ...] = DEFAULT_CANDLE_INTERVALS,
    limit: int = DEFAULT_CANDLE_FETCH_LIMIT,
    retention_days: int = DEFAULT_CANDLE_RETENTION_DAYS,
    db_path: Path = DEFAULT_CANDLE_DB_PATH,
) -> CandleCollectionResult:
    """Fetch public candles once and store them locally."""

    if limit <= 0 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000.")

    initialize_candle_store(db_path)
    fetched_rows = 0
    upserted_rows = 0

    for symbol in symbols:
        for interval in intervals:
            candles = fetch_public_candles(symbol, interval, limit=limit)
            fetched_rows += len(candles)
            upserted_rows += upsert_candles(candles, db_path=db_path)

    deleted_rows = cleanup_old_candles(
        db_path=db_path,
        retention_days=retention_days,
    )
    total_rows = count_candles(db_path=db_path)
    return CandleCollectionResult(
        symbols=symbols,
        intervals=intervals,
        fetch_limit=limit,
        fetched_rows=fetched_rows,
        upserted_rows=upserted_rows,
        deleted_rows=deleted_rows,
        total_rows=total_rows,
        db_path=db_path,
    )


def run_candle_collection_once(
    *,
    limit: int = DEFAULT_CANDLE_FETCH_LIMIT,
    retention_days: int = DEFAULT_CANDLE_RETENTION_DAYS,
    db_path: Path = DEFAULT_CANDLE_DB_PATH,
    interval_seconds: int = 0,
) -> int:
    """Run one public candle collection cycle and print a short report."""

    try:
        result = collect_public_candles_once(
            limit=limit,
            retention_days=retention_days,
            db_path=db_path,
        )
    except BinancePublicMarketError as exc:
        print(f"Error: {exc}")
        write_error_heartbeat(
            path=CANDLE_COLLECTOR_HEALTH_PATH,
            service="candle_collector",
            interval_seconds=interval_seconds,
            error_message=str(exc),
        )
        return 1

    print(format_candle_collection_result(result))
    write_success_heartbeat(
        path=CANDLE_COLLECTOR_HEALTH_PATH,
        service="candle_collector",
        interval_seconds=interval_seconds,
        details={
            "symbols": list(result.symbols),
            "intervals": list(result.intervals),
            "fetch_limit": result.fetch_limit,
            "fetched_rows": result.fetched_rows,
            "upserted_rows": result.upserted_rows,
            "deleted_rows": result.deleted_rows,
            "total_rows": result.total_rows,
        },
    )
    return 0


def run_candle_collection_watch(
    *,
    interval_seconds: int,
    limit: int = DEFAULT_CANDLE_FETCH_LIMIT,
    retention_days: int = DEFAULT_CANDLE_RETENTION_DAYS,
    db_path: Path = DEFAULT_CANDLE_DB_PATH,
) -> int:
    """Run public candle collection repeatedly until interrupted."""

    print("Starting read-only Binance public candle collector")
    print(f"Symbols: {', '.join(PUBLIC_MARKET_WATCHLIST)}")
    print(f"Intervals: {', '.join(DEFAULT_CANDLE_INTERVALS)}")
    print(f"Interval seconds: {interval_seconds}")
    print(f"Fetch limit per symbol/interval: {limit}")
    print(f"Retention days: {retention_days}")
    print("Safety: public candle data only; no API key, no account access, no orders.")

    try:
        while True:
            exit_code = run_candle_collection_once(
                limit=limit,
                retention_days=retention_days,
                db_path=db_path,
                interval_seconds=interval_seconds,
            )
            if exit_code != 0:
                return exit_code
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("Stopping read-only Binance public candle collector.")
        return 0


def format_candle_collection_result(result: CandleCollectionResult) -> str:
    """Format candle collection output for terminal display."""

    return "\n".join(
        [
            "Binance public candle collection",
            f"Symbols: {', '.join(result.symbols)}",
            f"Intervals: {', '.join(result.intervals)}",
            f"Fetch limit per symbol/interval: {result.fetch_limit}",
            f"Fetched candle rows: {result.fetched_rows}",
            f"Upserted candle rows: {result.upserted_rows}",
            f"Deleted old rows: {result.deleted_rows}",
            f"Total stored rows: {result.total_rows}",
            f"Database: {result.db_path}",
            "Retention: 90 days by default",
            "Safety: public candle data only; no API key, no account access, no orders.",
        ]
    )
