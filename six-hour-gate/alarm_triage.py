"""Pure alarm normalization, classification, and six-hour gate logic.

The vendor adapters accept the small variations used by ShineMonitor,
DessMonitor, and SolisCloud.  Policy is supplied by the caller; the decision
functions perform no file I/O and receive ``now`` from their caller so they
are deterministic and easy to test.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


LOGGER = logging.getLogger(__name__)


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
    if _is_solis_vendor(vendor):
        # alarmList state: "0" open, "1" processed by someone, "2" restored.
        # Read off live data on 23 Oct 2026 (58 cleared alarms "2", 2 open "0").
        # Before that this tested for 1, which reads every open alarm as resolved.
        return value is not False and str(value) == "0"
    return value in (False, 0, "0", "false", "False", "closed", "resolved")


def _message(raw: Mapping[str, Any]) -> str:
    return str(_first(raw, "message", "alarmMessage", "alarmMsg", "warning", "content", default=""))


def _is_solis_vendor(vendor: str) -> bool:
    return vendor.casefold() in {"solis", "soliscloud"}


def _plant_offset_minutes(raw: Mapping[str, Any], config: Mapping[str, Any]) -> int:
    """Read a plant timezone from API seconds, falling back to policy minutes."""
    minute_value = _first(
        raw, "plant_tz_offset_minutes", "timezone_offset_minutes", default=None)
    if minute_value is not None:
        return int(minute_value)

    address = raw.get("address")
    address_timezone = address.get(
        "timezone") if isinstance(address, Mapping) else None
    second_value = _first(
        raw,
        "plant_tz_offset_seconds",
        "timezone_offset_seconds",
        "timezone",
        default=address_timezone,
    )
    if second_value is not None and second_value != "":
        return int(second_value) // 60

    timezone_config = config.get("timezone", {})
    return int(timezone_config.get("plant_tz_offset_minutes_default", 330))


def normalize(
    raw: Mapping[str, Any], vendor: str, config: Mapping[str, Any]
) -> dict[str, Any]:
    """Convert one vendor alarm payload to the common gate record schema.

    ShineMonitor and DessMonitor ``gts`` values are plant-local.  SolisCloud
    supplies ``alarmBeginTime`` as an epoch timestamp.  A per-record offset,
    when supplied by the caller, takes precedence over the policy fallback.
    """
    vendor_name = vendor.casefold()
    offset = _plant_offset_minutes(raw, config)
    timestamp = _first(raw, "alarmBeginTime", "alarm_begin_time",
                       "started_at_utc", "gts", "first_seen")
    started_at = _parse_datetime(
        timestamp, local_offset_minutes=0 if vendor_name == "solis" else offset)
    message = _message(raw)
    vendor_code = str(_first(raw, "vendor_code", "alarmCode",
                      "alarm_code", "code", default=""))

    if _is_solis_vendor(vendor_name):
        plant = _first(raw, "plant", "stationName",
                       "station_name", "plantName")
        device = _first(raw, "device", "deviceName", "inverterName", "sn")
        customer = _first(raw, "customer", "owner", "customerName")
    else:
        plant = _first(raw, "plant", "plantName", "plant_name", "name")
        device = _first(raw, "device", "deviceName",
                        "devname", "devName", "serial", "sn")
        customer = _first(raw, "customer", "customerName", "owner", "username")

    vendor_codes = config.get("vendor_codes", {})
    code_map = vendor_codes.get(
        "solis", {}) if _is_solis_vendor(vendor_name) else vendor_codes.get(
            vendor_name, {})
    # The code first: classifying the text of an alarm the code already places
    # would log it as unclassified, and that log is how the class map grows.
    code_class = code_map.get(vendor_code)

    record: Record = {
        "plant": str(plant),
        "device": str(device),
        "vendor_code": vendor_code,
        "message": message,
        "class": code_class or classify(message, config),
        "started_at_utc": started_at,
        "open": _open_flag(raw, vendor_name),
        "customer": str(customer),
    }
    return record


def classify(message: str, config: Mapping[str, Any]) -> str:
    """Classify a message; ties are resolved by config order, longest wins."""
    lowered = message.casefold()
    matches = [
        (pattern.casefold(), alarm_class)
        for alarm_class, patterns in config.get("match", {}).items()
        if not alarm_class.startswith("_")
        if isinstance(patterns, (list, tuple))
        for pattern in patterns
        if pattern.casefold() in lowered
    ]
    if matches:
        return max(matches, key=lambda match: len(match[0]))[1]
    if config.get("classes", {}).get("UNKNOWN", {}).get("log"):
        LOGGER.warning("Unclassified alarm message: %s", message)
    return "UNKNOWN"


def is_actionable(
    record: Mapping[str, Any], now: datetime, config: Mapping[str, Any]
) -> tuple[bool, str | None]:
    """Return whether an open alarm has passed its gate and its mail hint."""
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

    dwell_hours = class_config.get(
        "dwell_hours", config["dwell_hours_default"])
    if age < timedelta(hours=dwell_hours):
        return False, f"dwell time is less than {dwell_hours} hours"
    return True, config.get("escalation_hint", {}).get(alarm_class)
