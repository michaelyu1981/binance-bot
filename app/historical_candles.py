"""Public historical candle downloader for local backtests.

This module uses Binance public klines only. It must not use API keys, account
data, or order endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

from app.binance_reader import BinancePublicMarketError, fetch_public_candles_window
from app.backtesting import DEFAULT_BACKTEST_DB_PATH
from app.candle_store import upsert_candles
from app.config import PUBLIC_MARKET_WATCHLIST


DEFAULT_HISTORY_INTERVAL = "1h"
DEFAULT_HISTORY_DAYS = 90
BINANCE_KLINES_LIMIT = 1000


@dataclass(frozen=True)
class HistoricalDownloadResult:
    symbol: str
    interval: str
    days: int
    fetched_rows: int
    stored_rows: int
    requests: int
    error: str | None


def download_historical_candles(
    *,
    days: int = DEFAULT_HISTORY_DAYS,
    interval: str = DEFAULT_HISTORY_INTERVAL,
    symbols: tuple[str, ...] = PUBLIC_MARKET_WATCHLIST,
    db_path: Path = DEFAULT_BACKTEST_DB_PATH,
    request_delay_seconds: float = 0.15,
) -> tuple[HistoricalDownloadResult, ...]:
    """Download public historical candles into local SQLite."""

    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    results = []
    for symbol in symbols:
        results.append(
            _download_symbol_history(
                symbol=symbol,
                interval=interval,
                days=days,
                start_ms=start_ms,
                end_ms=end_ms,
                db_path=db_path,
                request_delay_seconds=request_delay_seconds,
            )
        )
    return tuple(results)


def format_historical_download_results(
    results: tuple[HistoricalDownloadResult, ...],
) -> str:
    lines = [
        "CoinPilot public historical candle download",
        "Safety: public klines only; no API key; no account access; no orders.",
        "",
    ]
    for result in results:
        status = f"error: {result.error}" if result.error else "ok"
        lines.append(
            f"{result.symbol} {result.interval} {result.days}d | "
            f"requests {result.requests} | fetched {result.fetched_rows} | "
            f"stored {result.stored_rows} | {status}"
        )
    return "\n".join(lines)


def _download_symbol_history(
    *,
    symbol: str,
    interval: str,
    days: int,
    start_ms: int,
    end_ms: int,
    db_path: Path,
    request_delay_seconds: float,
) -> HistoricalDownloadResult:
    fetched_rows = 0
    stored_rows = 0
    requests = 0
    cursor_ms = start_ms
    try:
        while cursor_ms < end_ms:
            candles = fetch_public_candles_window(
                symbol,
                interval,
                start_time_ms=cursor_ms,
                end_time_ms=end_ms,
                limit=BINANCE_KLINES_LIMIT,
            )
            requests += 1
            if not candles:
                break
            fetched_rows += len(candles)
            stored_rows += upsert_candles(candles, db_path=db_path)
            next_cursor = candles[-1].close_time_ms + 1
            if next_cursor <= cursor_ms:
                break
            cursor_ms = next_cursor
            if len(candles) < BINANCE_KLINES_LIMIT:
                break
            if request_delay_seconds > 0:
                time.sleep(request_delay_seconds)
    except (BinancePublicMarketError, ValueError) as exc:
        return HistoricalDownloadResult(
            symbol=symbol,
            interval=interval,
            days=days,
            fetched_rows=fetched_rows,
            stored_rows=stored_rows,
            requests=requests,
            error=str(exc),
        )

    return HistoricalDownloadResult(
        symbol=symbol,
        interval=interval,
        days=days,
        fetched_rows=fetched_rows,
        stored_rows=stored_rows,
        requests=requests,
        error=None,
    )
