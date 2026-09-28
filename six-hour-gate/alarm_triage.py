"""Pure alarm normalization, classification, and six-hour gate logic.

The vendor adapters accept the small variations used by ShineMonitor,
DessMonitor, and SolisCloud.  Policy is loaded once when this module is
imported; the decision functions perform no I/O and receive ``now`` from
their caller so they are deterministic and easy to test.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


_CONFIG_PATH = Path(__file__).with_name("config") / "alarm_triage.json"
with _CONFIG_PATH.open(encoding="utf-8") as _config_file:
    _DEFAULT_CONFIG: dict[str, Any] = json.load(_config_file)


Record = dict[str, Any]


def _first(raw: Mapping[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        value = raw.get(name)
        if value is not None and value != "":
            return value
    return default


def _parse_datetime(value: Any, *, local_offset_minutes: int = 0) -> datetime:
    """Return an aware UTC datetime from an ISO value, epoch, or local gts."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(
            value / 1000 if value > 10_000_000_000 else value, timezone.utc)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for pattern in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError(
                    f"Unsupported alarm timestamp: {value!r}") from None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(
            timedelta(minutes=local_offset_minutes)))
    return parsed.astimezone(timezone.utc)


def _open_flag(raw: Mapping[str, Any], vendor: str) -> bool:
    if "open" in raw or "is_open" in raw:
        value = _first(raw, "open", "is_open", default=False)
        return value in (1, "1", True, "true", "True", "open", "OPEN")
    value = _first(raw, "status", "state", default=False)
    if vendor.lower() == "solis":
        return value in (1, "1", True, "true", "TRUE", "open", "OPEN")
    return value in (False, 0, "0", "false", "False", "closed", "resolved")


def _message(raw: Mapping[str, Any]) -> str:
    return str(_first(raw, "message", "alarmMessage", "alarmMsg", "warning", "content", default=""))


def normalize(raw: dict[str, Any], vendor: str) -> dict[str, Any]:
    """Convert one vendor alarm payload to the common gate record schema.

    ShineMonitor and DessMonitor ``gts`` values are plant-local.  SolisCloud
    supplies ``alarmBeginTime`` as an epoch timestamp.  A per-record offset,
    when supplied by the caller, takes precedence over the policy fallback.
    """
    vendor_name = vendor.lower()
    offset = int(_first(raw, "plant_tz_offset_minutes", "timezone_offset_minutes",
                 default=_DEFAULT_CONFIG["timezone"]["plant_tz_offset_minutes_default"]))
    timestamp = _first(raw, "alarmBeginTime", "alarm_begin_time",
                       "started_at_utc", "gts", "first_seen")
    started_at = _parse_datetime(
        timestamp, local_offset_minutes=0 if vendor_name == "solis" else offset)
    message = _message(raw)
    vendor_code = str(_first(raw, "vendor_code", "alarmCode",
                      "alarm_code", "code", default=""))

    if vendor_name == "solis":
        plant = _first(raw, "plant", "stationName",
                       "station_name", "plantName")
        device = _first(raw, "device", "deviceName", "inverterName", "sn")
        customer = _first(raw, "customer", "owner", "customerName")
    else:
        plant = _first(raw, "plant", "plantName", "plant_name", "name")
        device = _first(raw, "device", "deviceName",
                        "devname", "devName", "serial", "sn")
        customer = _first(raw, "customer", "customerName", "owner", "username")

    record: Record = {
        "plant": str(plant),
        "device": str(device),
        "vendor_code": vendor_code,
        "message": message,
        "class": classify(message, _DEFAULT_CONFIG),
        "started_at_utc": started_at,
        "open": _open_flag(raw, vendor_name),
        "customer": str(customer),
    }
    code_class = _DEFAULT_CONFIG.get("vendor_codes", {}).get(
        vendor_name, {}).get(vendor_code)
    if code_class:
        record["class"] = code_class
    return record


def classify(message: str, config: dict[str, Any]) -> str:
    """Classify a message; ties are resolved by config order, longest wins."""
    lowered = message.casefold()
    matches = [
        (pattern.casefold(), alarm_class)
        for alarm_class, patterns in config.get("match", {}).items()
        for pattern in patterns
        if pattern.casefold() in lowered
    ]
    return max(matches, key=lambda match: len(match[0]))[1] if matches else "UNKNOWN"


def is_actionable(record: dict[str, Any], now: datetime, config: dict[str, Any]) -> tuple[bool, str | None]:
    """Return whether an open alarm has passed its configured gate."""
    if not record.get("open", False):
        return False, "alarm is resolved"

    alarm_class = str(record.get("class", "UNKNOWN"))
    class_config = config.get("classes", {}).get(
        alarm_class, config.get("classes", {}).get("UNKNOWN", {}))
    if class_config.get("gate") == "never" or alarm_class == "COMMS":
        return False, "COMMS alarms are not mailed"

    started_at = record["started_at_utc"]
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    current = now if now.tzinfo is not None else now.replace(
        tzinfo=timezone.utc)
    age = current.astimezone(timezone.utc) - \
        started_at.astimezone(timezone.utc)

    if class_config.get("gate") == "daylight":
        daylight = config["daylight_window_local"]
        offset = int(config["timezone"].get(
            "plant_tz_offset_minutes_default", 330))
        local_started = started_at.astimezone(
            timezone(timedelta(minutes=offset)))
        local_now = current.astimezone(timezone(timedelta(minutes=offset)))
        daylight_end = daylight["end_hour"]
        crossed_daylight_end = any(
            local_started <= datetime.combine(day, datetime.min.time()).replace(
                hour=daylight_end, tzinfo=local_started.tzinfo
            ) <= local_now
            for day_offset in range((local_now.date() - local_started.date()).days + 1)
            for day in [local_started.date() + timedelta(days=day_offset)]
        )
        if not crossed_daylight_end:
            return False, "SOC alarm has not survived the daylight window"
    else:
        dwell_hours = class_config.get(
            "dwell_hours", config["dwell_hours_default"])
        if age < timedelta(hours=dwell_hours):
            return False, f"dwell time is less than {dwell_hours} hours"
    return True, None
