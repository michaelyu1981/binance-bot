"""Collect public Binance candles into the local SQLite store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
        return 1

    print(format_candle_collection_result(result))
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
