"""Optional Telegram notifications for local public-market alerts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram settings loaded from environment variables."""

    bot_token: str
    chat_id: str


class TelegramSendError(RuntimeError):
    """Raised when an optional Telegram alert cannot be sent."""


def load_telegram_config_from_env() -> TelegramConfig | None:
    """Load optional Telegram settings without printing or logging secrets."""

    bot_token = os.environ.get(TELEGRAM_BOT_TOKEN_ENV, "").strip()
    chat_id = os.environ.get(TELEGRAM_CHAT_ID_ENV, "").strip()
    if not bot_token or not chat_id:
        return None
    return TelegramConfig(bot_token=bot_token, chat_id=chat_id)


def send_telegram_message(
    message: str,
    config: TelegramConfig,
    *,
    timeout_seconds: float = 10.0,
) -> None:
    """Send one Telegram message using Bot API credentials from env config."""

    url = f"{TELEGRAM_API_BASE_URL}/bot{config.bot_token}/sendMessage"
    body = urlencode(
        {
            "chat_id": config.chat_id,
            "text": message,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raise TelegramSendError(
            f"Telegram API request failed with HTTP {exc.code}."
        ) from exc
    except URLError as exc:
        raise TelegramSendError(
            f"Could not reach Telegram API: {exc.reason}"
        ) from exc

    try:
        response_payload = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TelegramSendError("Telegram API returned invalid JSON.") from exc

    if not response_payload.get("ok"):
        description = response_payload.get("description")
        if isinstance(description, str) and description:
            raise TelegramSendError(f"Telegram API rejected the message: {description}")
        raise TelegramSendError("Telegram API rejected the message.")


def send_alert_lines_to_telegram(alert_lines: list[str]) -> None:
    """Send alert lines to Telegram only when Telegram is configured."""

    config = load_telegram_config_from_env()
    if config is None or not alert_lines:
        return

    send_telegram_message("\n".join(alert_lines), config)
