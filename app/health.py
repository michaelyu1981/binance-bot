"""Heartbeat files for local CoinPilot process health."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.logger import current_timestamp


HEALTH_DIR = Path("data/health")
PRICE_MONITOR_HEALTH_PATH = HEALTH_DIR / "price_monitor.json"
CANDLE_COLLECTOR_HEALTH_PATH = HEALTH_DIR / "candle_collector.json"
SIGNAL_WATCHER_HEALTH_PATH = HEALTH_DIR / "signal_watcher.json"


@dataclass(frozen=True)
class ServiceHealth:
    service: str
    status: str
    health: str
    last_success: str | None
    last_error: str | None
    last_error_message: str | None
    interval_seconds: int | None
    age_seconds: int | None
    details: dict[str, Any]


def write_success_heartbeat(
    *,
    path: Path,
    service: str,
    interval_seconds: int,
    details: dict[str, Any] | None = None,
) -> None:
    """Write an OK heartbeat after a successful process cycle."""

    previous = _read_payload(path)
    payload = {
        "service": service,
        "status": "ok",
        "last_success": current_timestamp(),
        "last_error": previous.get("last_error"),
        "last_error_message": previous.get("last_error_message"),
        "interval_seconds": interval_seconds,
        "details": details or {},
    }
    _write_payload(path, payload)


def write_error_heartbeat(
    *,
    path: Path,
    service: str,
    interval_seconds: int,
    error_message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Write an error heartbeat without storing secrets."""

    previous = _read_payload(path)
    payload = {
        "service": service,
        "status": "error",
        "last_success": previous.get("last_success"),
        "last_error": current_timestamp(),
        "last_error_message": error_message,
        "interval_seconds": interval_seconds,
        "details": details or previous.get("details", {}),
    }
    _write_payload(path, payload)


def read_service_health(path: Path, *, service: str) -> ServiceHealth:
    """Read and classify one service heartbeat."""

    payload = _read_payload(path)
    if not payload:
        return ServiceHealth(
            service=service,
            status="missing",
            health="DOWN",
            last_success=None,
            last_error=None,
            last_error_message=None,
            interval_seconds=None,
            age_seconds=None,
            details={},
        )

    interval_seconds = _optional_int(payload.get("interval_seconds"))
    last_success = _optional_str(payload.get("last_success"))
    age_seconds = _age_seconds(last_success)
    health = _classify_health(
        status=_optional_str(payload.get("status")) or "unknown",
        interval_seconds=interval_seconds,
        age_seconds=age_seconds,
    )
    details = payload.get("details")
    if not isinstance(details, dict):
        details = {}

    return ServiceHealth(
        service=_optional_str(payload.get("service")) or service,
        status=_optional_str(payload.get("status")) or "unknown",
        health=health,
        last_success=last_success,
        last_error=_optional_str(payload.get("last_error")),
        last_error_message=_optional_str(payload.get("last_error_message")),
        interval_seconds=interval_seconds,
        age_seconds=age_seconds,
        details=details,
    )


def _classify_health(
    *,
    status: str,
    interval_seconds: int | None,
    age_seconds: int | None,
) -> str:
    if status == "error":
        return "ERROR"
    if age_seconds is None or interval_seconds is None:
        return "UNKNOWN"
    if age_seconds > interval_seconds * 3:
        return "STALE"
    return "OK"


def _age_seconds(timestamp: str | None) -> int | None:
    if timestamp is None:
        return None
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    now = datetime.fromisoformat(current_timestamp())
    return max(0, int((now - parsed_timestamp).total_seconds()))


def _read_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as temp_file:
        json.dump(payload, temp_file, indent=2, sort_keys=True)
        temp_file.write("\n")
        temp_path = Path(temp_file.name)
    temp_path.replace(path)


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None
