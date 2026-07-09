"""Read-only Crypto Fear & Greed Index reporter.

Uses the free, public, unauthenticated alternative.me Fear & Greed API. This
is a market-wide sentiment gauge (0 = Extreme Fear, 100 = Extreme Greed),
not per-symbol. It does not use a Binance API key, does not access account
data, and must not place orders. Runs on its own once-daily schedule,
independent of every candle-based watcher.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from app.health import HEALTH_DIR, write_error_heartbeat, write_success_heartbeat
from app.logger import append_market_price_log, current_timestamp
from app.telegram_notifier import (
    TelegramSendError,
    load_telegram_config_from_env,
    send_telegram_message,
)


FEAR_GREED_API_URL = "https://api.alternative.me/fng/?limit=1"
FEAR_GREED_LOG_PREFIX = "FEARGREED"
FEAR_GREED_HEALTH_PATH = HEALTH_DIR / "fear_greed_reporter.json"
DAILY_INTERVAL_SECONDS = 24 * 3600


class FearGreedFetchError(RuntimeError):
    """Raised when the Fear & Greed Index cannot be fetched or parsed."""


@dataclass(frozen=True)
class FearGreedReading:
    value: int
    classification: str


def fetch_fear_greed_index(*, timeout_seconds: float = 10.0) -> FearGreedReading:
    """Fetch the current global Crypto Fear & Greed Index reading."""

    try:
        with urlopen(FEAR_GREED_API_URL, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raise FearGreedFetchError(f"Fear & Greed API request failed with HTTP {exc.code}.") from exc
    except URLError as exc:
        raise FearGreedFetchError(f"Could not reach Fear & Greed API: {exc.reason}") from exc

    try:
        parsed = json.loads(payload)
        entry = parsed["data"][0]
        value = int(entry["value"])
        classification = str(entry["value_classification"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise FearGreedFetchError("Fear & Greed API returned an unexpected response.") from exc

    return FearGreedReading(value=value, classification=classification)


def format_fear_greed_message(reading: FearGreedReading) -> str:
    return "\n".join(
        [
            "CoinPilot Fear & Greed Index",
            "",
            f"{reading.value}/100 - {reading.classification}",
            "0 = Extreme Fear, 100 = Extreme Greed. Market-wide, not per-coin.",
            "",
            "Advisory only. No automatic trade.",
        ]
    )


def run_fear_greed_report_once(*, send_telegram: bool = True) -> int:
    """Fetch and report the current Fear & Greed reading once."""

    try:
        reading = fetch_fear_greed_index()
    except FearGreedFetchError as exc:
        message = f"Fear & Greed fetch failed: {exc}"
        print(message)
        append_market_price_log([f"{current_timestamp()} {FEAR_GREED_LOG_PREFIX}: {message}"])
        write_error_heartbeat(
            path=FEAR_GREED_HEALTH_PATH,
            service="fear_greed_reporter",
            interval_seconds=DAILY_INTERVAL_SECONDS,
            error_message=str(exc),
        )
        return 1

    log_line = f"{current_timestamp()} {FEAR_GREED_LOG_PREFIX}: {reading.value}/100 - {reading.classification}"
    print(log_line)
    append_market_price_log([log_line])

    if send_telegram:
        config = load_telegram_config_from_env()
        if config is None:
            print("Telegram Fear & Greed report not sent because Telegram env vars are missing.")
        else:
            try:
                send_telegram_message(format_fear_greed_message(reading), config)
            except TelegramSendError as exc:
                print(f"Telegram Fear & Greed report failed: {exc}")

    write_success_heartbeat(
        path=FEAR_GREED_HEALTH_PATH,
        service="fear_greed_reporter",
        interval_seconds=DAILY_INTERVAL_SECONDS,
        details={"value": reading.value, "classification": reading.classification},
    )
    return 0


def run_fear_greed_report_loop(*, send_telegram: bool = True) -> int:
    """Run the Fear & Greed reporter once every 24 hours until Ctrl+C."""

    print("CoinPilot Fear & Greed reporter started.")
    print("Safety: public sentiment data only. No API key. No account access. No orders.")
    print(f"Interval: once every {DAILY_INTERVAL_SECONDS} seconds (24 hours).")
    try:
        while True:
            run_fear_greed_report_once(send_telegram=send_telegram)
            time.sleep(DAILY_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("Stopping CoinPilot Fear & Greed reporter.")
        return 0
