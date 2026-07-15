"""Shared on-disk state for the Live Bot Trader dashboard tab and its paper-trading loop.

Two JSON files live under `data/`:

- `LIVE_BOT_CONFIG_PATH`: written by the dashboard when the user toggles a
  bot on/off or saves parameters; read by the paper-trading loop every
  cycle to decide what to run and with which settings.
- `LIVE_BOT_RUNTIME_PATH`: written by the paper-trading loop after every
  processed candle; read by the dashboard to display each bot's current
  simulated position, balance, and recent decisions.

This module only exposes 3 pre-approved strategies (see [[validated
strategies]] memory / `docs/binance-api-key-policy.md`): the two Martingale
ladders (RSI, ATR) and Triad Confluence V5. Every other registered strategy
is intentionally left out of this tab.

PAPER TRADING ONLY. Nothing in this module calls a Binance order endpoint,
reads a signed/account credential, or places a real trade. Enabling real
order execution requires Michael's exact phrase "enable live spot trading."
per `docs/binance-api-key-policy.md` and is not implemented here.
"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.config import PUBLIC_MARKET_WATCHLIST
from app.logger import current_timestamp


LIVE_BOT_CONFIG_PATH = Path("data/live_bot_config.json")
LIVE_BOT_RUNTIME_PATH = Path("data/live_bot_runtime.json")

DEFAULT_CAPITAL_PER_SYMBOL = Decimal("1000")

# The 3 approved strategies this tab exposes, in the order they should
# render, each with its tested/validated default parameters and the
# timeframe it was actually validated on.
LIVE_BOT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "claude_modified_martingale_rsi": {
        "name": "Modified Martingale RSI",
        "summary": "28-level linear-lot DCA ladder gated by RSI(14) < 50, with bull-capture re-entry.",
        "recommended_interval": "4h",
        "recommended_interval_label": "4 Hour",
        "query_param": "rsi_symbol",
        "default_params": {
            "take_profit_percent": "4.5",
            "rsi_entry_max": "50",
            "step_drop_percent": "2.0",
            "bull_reentry_min_body_percent": "0.3",
        },
        "param_labels": {
            "take_profit_percent": "Take Profit (%)",
            "rsi_entry_max": "RSI Entry Threshold",
            "step_drop_percent": "Safety-Order Step Drop (%)",
            "bull_reentry_min_body_percent": "Bull-Capture Min Body (%)",
        },
    },
    "claude_modified_martingale_atr": {
        "name": "Modified Martingale ATR",
        "summary": "7-layer (1 BO + 6 SO) ladder with ATR(14)-based safety-order spacing.",
        "recommended_interval": "4h",
        "recommended_interval_label": "4 Hour",
        "query_param": "atr_symbol",
        "default_params": {
            "take_profit_percent": "2.5",
            "atr_multiplier": "2.2",
            "rsi_entry_max": "50",
        },
        "param_labels": {
            "take_profit_percent": "Take Profit (%)",
            "atr_multiplier": "ATR Multiplier",
            "rsi_entry_max": "RSI Entry Threshold (Base Order only)",
        },
    },
    "claude_triad_confluence_v5": {
        "name": "Triad Confluence V5",
        "summary": "Pattern + calendar + regime + momentum confluence swing trader with an ATR stop/trail.",
        "recommended_interval": "1w",
        "recommended_interval_label": "1 Week",
        "query_param": "confluence_symbol",
        "default_params": {},
        "param_labels": {},
    },
}

VALID_INTERVALS = ("15m", "1h", "4h", "1d", "1w")


def default_config() -> dict[str, Any]:
    """The config every bot starts from: off, tested defaults, full watchlist."""

    return {
        slug: {
            "enabled": False,
            "interval": definition["recommended_interval"],
            "capital_by_symbol": {symbol: str(DEFAULT_CAPITAL_PER_SYMBOL) for symbol in PUBLIC_MARKET_WATCHLIST},
            "symbols": list(PUBLIC_MARKET_WATCHLIST),
            "params": dict(definition["default_params"]),
        }
        for slug, definition in LIVE_BOT_DEFINITIONS.items()
    }


def read_live_bot_config() -> dict[str, Any]:
    """Merge stored config over defaults, so new default params/coins never go missing."""

    config = default_config()
    stored = _read_json(LIVE_BOT_CONFIG_PATH)
    for slug, defaults in config.items():
        stored_bot = stored.get(slug)
        if not isinstance(stored_bot, dict):
            continue
        merged = dict(defaults)
        merged.update(
            {key: value for key, value in stored_bot.items() if key not in ("params", "capital_by_symbol")}
        )
        merged_params = dict(defaults["params"])
        if isinstance(stored_bot.get("params"), dict):
            merged_params.update(stored_bot["params"])
        merged["params"] = merged_params
        merged_capital = dict(defaults["capital_by_symbol"])
        if isinstance(stored_bot.get("capital_by_symbol"), dict):
            merged_capital.update(stored_bot["capital_by_symbol"])
        merged["capital_by_symbol"] = merged_capital
        config[slug] = merged
    return config


def write_live_bot_config(config: dict[str, Any]) -> None:
    payload = {"updated_at": current_timestamp(), **config}
    _write_json_atomic(LIVE_BOT_CONFIG_PATH, payload)


def read_live_bot_runtime() -> dict[str, Any]:
    return _read_json(LIVE_BOT_RUNTIME_PATH)


def write_live_bot_runtime(runtime: dict[str, Any]) -> None:
    payload = {"updated_at": current_timestamp(), **runtime}
    _write_json_atomic(LIVE_BOT_RUNTIME_PATH, payload)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp_file:
        json.dump(payload, temp_file, indent=2, sort_keys=True)
        temp_file.write("\n")
        temp_path = Path(temp_file.name)
    temp_path.replace(path)
