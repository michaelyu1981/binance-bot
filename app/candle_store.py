"""SQLite storage for public Binance candle data.

This module stores public market candles only. It must not store API keys,
account balances, orders, fills, or other private account data.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3

from app.binance_reader import Candle


DEFAULT_CANDLE_DB_PATH = Path("data/market_data.sqlite3")
DEFAULT_CANDLE_RETENTION_DAYS = 90


@dataclass(frozen=True)
class CandleStoreStats:
    db_path: Path
    size_before_bytes: int
    size_after_bytes: int
    deleted_rows: int
    remaining_rows: int
    vacuumed: bool


def initialize_candle_store(db_path: Path = DEFAULT_CANDLE_DB_PATH) -> None:
    """Create the candle database and schema if missing."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as connection:
        _create_schema(connection)


def upsert_candles(
    candles: Iterable[Candle],
    *,
    db_path: Path = DEFAULT_CANDLE_DB_PATH,
) -> int:
    """Insert or replace public candles by symbol, interval, and open time."""

    candle_rows = [
        (
            candle.symbol,
            candle.interval,
            candle.open_time_ms,
            candle.close_time_ms,
            str(candle.open_price),
            str(candle.high_price),
            str(candle.low_price),
            str(candle.close_price),
            str(candle.volume),
            str(candle.quote_volume),
            candle.trade_count,
            str(candle.taker_buy_base_volume),
            str(candle.taker_buy_quote_volume),
        )
        for candle in candles
    ]
    if not candle_rows:
        return 0

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as connection:
        _create_schema(connection)
        connection.executemany(
            """
            INSERT INTO candles (
                symbol,
                interval,
                open_time_ms,
                close_time_ms,
                open,
                high,
                low,
                close,
                volume,
                quote_volume,
                trade_count,
                taker_buy_base_volume,
                taker_buy_quote_volume
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, interval, open_time_ms)
            DO UPDATE SET
                close_time_ms = excluded.close_time_ms,
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                quote_volume = excluded.quote_volume,
                trade_count = excluded.trade_count,
                taker_buy_base_volume = excluded.taker_buy_base_volume,
                taker_buy_quote_volume = excluded.taker_buy_quote_volume
            """,
            candle_rows,
        )
    return len(candle_rows)


def cleanup_old_candles(
    *,
    db_path: Path = DEFAULT_CANDLE_DB_PATH,
    retention_days: int = DEFAULT_CANDLE_RETENTION_DAYS,
) -> int:
    """Delete candles older than the retention window."""

    cutoff_ms = _retention_cutoff_ms(retention_days)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as connection:
        _create_schema(connection)
        cursor = connection.execute(
            "DELETE FROM candles WHERE open_time_ms < ?",
            (cutoff_ms,),
        )
        return cursor.rowcount


def run_candle_db_maintenance(
    *,
    db_path: Path = DEFAULT_CANDLE_DB_PATH,
    retention_days: int = DEFAULT_CANDLE_RETENTION_DAYS,
    vacuum: bool = False,
) -> CandleStoreStats:
    """Run candle retention cleanup and optionally compact the SQLite file."""

    initialize_candle_store(db_path)
    size_before = _file_size(db_path)
    deleted_rows = cleanup_old_candles(
        db_path=db_path,
        retention_days=retention_days,
    )

    if vacuum:
        with _connect(db_path) as connection:
            connection.execute("VACUUM")

    remaining_rows = count_candles(db_path=db_path)
    size_after = _file_size(db_path)
    return CandleStoreStats(
        db_path=db_path,
        size_before_bytes=size_before,
        size_after_bytes=size_after,
        deleted_rows=deleted_rows,
        remaining_rows=remaining_rows,
        vacuumed=vacuum,
    )


def count_candles(*, db_path: Path = DEFAULT_CANDLE_DB_PATH) -> int:
    """Return the total stored candle row count."""

    if not db_path.exists():
        return 0
    with _connect(db_path) as connection:
        _create_schema(connection)
        row = connection.execute("SELECT COUNT(*) FROM candles").fetchone()
    return int(row[0])


def format_candle_store_stats(stats: CandleStoreStats) -> str:
    """Format DB maintenance output for terminal display."""

    return "\n".join(
        [
            "Candle database maintenance",
            f"Database: {stats.db_path}",
            f"Rows deleted: {stats.deleted_rows}",
            f"Rows remaining: {stats.remaining_rows}",
            f"Size before: {_format_bytes(stats.size_before_bytes)}",
            f"Size after: {_format_bytes(stats.size_after_bytes)}",
            f"VACUUM run: {'yes' if stats.vacuumed else 'no'}",
            "Safety: public candle data only; no API key, no account access, no orders.",
        ]
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS candles (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_time_ms INTEGER NOT NULL,
            close_time_ms INTEGER NOT NULL,
            open TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            close TEXT NOT NULL,
            volume TEXT NOT NULL,
            quote_volume TEXT NOT NULL,
            trade_count INTEGER NOT NULL,
            taker_buy_base_volume TEXT NOT NULL,
            taker_buy_quote_volume TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, interval, open_time_ms)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_candles_symbol_interval_close_time
        ON candles(symbol, interval, close_time_ms)
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_candles_updated_at
        AFTER UPDATE ON candles
        FOR EACH ROW
        BEGIN
            UPDATE candles
            SET updated_at = CURRENT_TIMESTAMP
            WHERE symbol = OLD.symbol
              AND interval = OLD.interval
              AND open_time_ms = OLD.open_time_ms;
        END
        """
    )


def _retention_cutoff_ms(retention_days: int) -> int:
    if retention_days <= 0:
        raise ValueError("retention_days must be positive.")
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    return int(cutoff.timestamp() * 1000)


def _file_size(path: Path) -> int:
    if not path.exists():
        return 0
    return path.stat().st_size


def _format_bytes(size_bytes: int) -> str:
    size = Decimal(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < Decimal("1024") or unit == "GB":
            return f"{size:.2f} {unit}" if unit != "B" else f"{size_bytes} B"
        size /= Decimal("1024")
    return f"{size_bytes} B"
