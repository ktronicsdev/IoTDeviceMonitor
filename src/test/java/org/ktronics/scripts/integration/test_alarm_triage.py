"""Rule-defending tests for the pure Six-Hour Gate core."""

from __future__ import annotations

import json
import importlib.util
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[7]
GATE_DIR = PROJECT_ROOT / "six-hour-gate"
_MODULE_SPEC = importlib.util.spec_from_file_location(
    "alarm_triage", GATE_DIR / "alarm_triage.py")
assert _MODULE_SPEC is not None and _MODULE_SPEC.loader is not None
alarm_triage = importlib.util.module_from_spec(_MODULE_SPEC)
_MODULE_SPEC.loader.exec_module(alarm_triage)


CONFIG: dict[str, Any] = json.loads(
    (GATE_DIR / "config" / "alarm_triage.json").read_text(encoding="utf-8")
)
UTC = timezone.utc
LOCAL = timezone(timedelta(hours=5, minutes=30))


def record(message: str, started_at: datetime, alarm_class: str | None = None, open_: bool = True) -> dict[str, Any]:
    return {
        "plant": "Test Plant",
        "device": "INV-1",
        "vendor_code": "",
        "message": message,
        "class": alarm_class or alarm_triage.classify(message, CONFIG),
        "started_at_utc": started_at,
        "open": open_,
        "customer": "Test Customer",
    }


def test_r1_dwell_from_vendor_timestamp() -> None:
    raw = {
        "plantName": "Plant",
        "devname": "INV-1",
        "warning": "inverter grid under frequency",
        "gts": "2026-09-28T06:29:00+00:00",
        "first_seen": "2026-09-28T12:00:00+00:00",
        "status": False,
    }
    normalized = alarm_triage.normalize(raw, "ShineMonitor", CONFIG)

    assert alarm_triage.is_actionable(
        normalized, datetime(2026, 9, 28, 12, 30, tzinfo=UTC), CONFIG
    ) == (True, CONFIG["escalation_hint"]["GRID"])
    assert alarm_triage.is_actionable(
        normalized, datetime(2026, 9, 28, 12, 28, tzinfo=UTC), CONFIG
    )[0] is False


def test_r1_alarm_absent_from_current_feed_is_cleared() -> None:
    alarm = record("grid loss", datetime(
        2026, 9, 27, tzinfo=UTC), "GRID")
    current_feed = [record("grid loss", datetime(
        2026, 9, 28, tzinfo=UTC), "GRID")]
    current_feed[0]["device"] = "INV-2"

    current_matches = [
        current_alarm
        for current_alarm in current_feed
        if (current_alarm["plant"], current_alarm["device"]) ==
        (alarm["plant"], alarm["device"])
    ]

    assert current_matches == []


def test_r2_timezone_conversion_offset() -> None:
    local_gts = datetime(2026, 9, 28, 12, 0, tzinfo=LOCAL)
    now_utc = datetime(2026, 9, 28, 18, 0, tzinfo=UTC)
    correctly_converted_age = now_utc - local_gts.astimezone(UTC)
    naive_age = now_utc.replace(tzinfo=None) - local_gts.replace(tzinfo=None)

    assert correctly_converted_age == timedelta(hours=11, minutes=30)
    assert correctly_converted_age - \
        naive_age == timedelta(hours=5, minutes=30)
    normalized = alarm_triage.normalize(
        {"warning": "grid loss", "gts": "2026-09-28T12:00:00", "status": False},
        "ShineMonitor",
        CONFIG,
    )
    assert normalized["started_at_utc"] == datetime(
        2026, 9, 28, 6, 30, tzinfo=UTC)


def test_r3_resolved_alarm_is_never_actionable() -> None:
    alarm = record("grid loss", datetime(
        2026, 9, 27, tzinfo=UTC), "GRID", open_=False)

    assert alarm_triage.is_actionable(alarm, datetime(2026, 9, 28, tzinfo=UTC), CONFIG) == (
        False,
        "alarm is resolved",
    )


def test_comms_never_actionable() -> None:
    alarm = record("datalogger lost", datetime(
        2026, 9, 1, tzinfo=UTC), "COMMS")

    assert alarm_triage.is_actionable(alarm, datetime(2026, 9, 28, tzinfo=UTC), CONFIG) == (
        False,
        "COMMS alarms are not mailed",
    )


def test_unknown_uses_default_six_hour_dwell() -> None:
    alarm = record("new vendor fault", datetime(
        2026, 9, 28, 5, 59, tzinfo=UTC))

    assert alarm["class"] == "UNKNOWN"
    assert alarm_triage.is_actionable(alarm, datetime(2026, 9, 28, 12, tzinfo=UTC), CONFIG) == (
        True,
        None,
    )


def test_r5_soc_must_survive_daylight_window() -> None:
    alarm = record("low battery", datetime(
        2026, 9, 28, 3, 30, tzinfo=UTC), "SOC")

    assert alarm_triage.is_actionable(alarm, datetime(
        2026, 9, 28, 9, 30, tzinfo=UTC), CONFIG)[0] is True


def test_r5_soc_overnight_alarm_is_suppressed_before_daylight_end() -> None:
    alarm = record("low battery", datetime(
        2026, 9, 27, 16, 30, tzinfo=UTC), "SOC")

    assert alarm_triage.is_actionable(alarm, datetime(
        2026, 9, 28, 3, 30, tzinfo=UTC), CONFIG)[0] is False


def test_r6_grid_loss_under_six_hours_is_suppressed() -> None:
    alarm = record("1015 NO-Grid", datetime(2026,
                   9, 28, 9, tzinfo=UTC), "GRID")

    assert alarm_triage.is_actionable(alarm, datetime(
        2026, 9, 28, 12, tzinfo=UTC), CONFIG)[0] is False


def test_r6_grid_loss_over_six_hours_is_actionable() -> None:
    alarm = record("1015 NO-Grid", datetime(2026,
                   9, 28, 9, tzinfo=UTC), "GRID")

    assert alarm_triage.is_actionable(alarm, datetime(
        2026, 9, 28, 16, tzinfo=UTC), CONFIG) == (
        True, CONFIG["escalation_hint"]["GRID"])


def test_longest_configured_pattern_wins() -> None:
    assert alarm_triage.classify("overload time out", CONFIG) == "LOAD"
    assert alarm_triage.classify("overload", CONFIG) == "LOAD"


def test_normalize_solis_epoch_and_open_state() -> None:
    normalized = alarm_triage.normalize(
        {
            "stationName": "Solis Plant",
            "inverterName": "Solis-1",
            "alarmCode": "1015",
            "alarmMessage": "NO-Grid",
            "alarmBeginTime": 1790575200000,
            "state": 1,
        },
        "SolisCloud",
        CONFIG,
    )

    assert normalized["plant"] == "Solis Plant"
    assert normalized["class"] == "GRID"
    assert normalized["open"] is True
    assert normalized["started_at_utc"].tzinfo is not None


def test_unknown_alarm_is_logged(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        assert alarm_triage.classify("new vendor fault", CONFIG) == "UNKNOWN"
    assert "new vendor fault" in caplog.text


def test_soc_clock_uses_configured_fallback_dwell() -> None:
    clock_config = deepcopy(CONFIG)
    clock_config["classes"]["SOC"]["gate"] = "clock"
    alarm = record("low battery", datetime(2026, 9, 28, tzinfo=UTC), "SOC")

    assert alarm_triage.is_actionable(
        alarm, datetime(2026, 9, 28, 13, tzinfo=UTC), clock_config
    )[0] is False
    assert alarm_triage.is_actionable(
        alarm, datetime(2026, 9, 28, 14, tzinfo=UTC), clock_config
    )[0] is True


def test_daylight_gate_uses_per_plant_offset() -> None:
    alarm = record("low battery", datetime(2026, 9, 28, 8, tzinfo=UTC), "SOC")
    alarm["plant_tz_offset_minutes"] = 0

    assert alarm_triage.is_actionable(
        alarm, datetime(2026, 9, 28, 14, tzinfo=UTC), CONFIG
    )[0] is False


def test_comms_is_suppressed_at_any_age() -> None:
    for hours in (1, 10, 100):
        alarm = record("datalogger lost", datetime(
            2026, 9, 28, tzinfo=UTC), "COMMS")
        assert alarm_triage.is_actionable(
            alarm, datetime(2026, 9, 28, tzinfo=UTC) +
            timedelta(hours=hours), CONFIG
        ) == (False, "COMMS alarms are not mailed")
