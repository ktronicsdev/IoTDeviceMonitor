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


def test_r6_battery_alarm_under_six_hours_is_suppressed() -> None:
    alarm = record("low battery", datetime(
        2026, 9, 28, 3, 30, tzinfo=UTC), "SOC")

    assert alarm_triage.is_actionable(alarm, datetime(
        2026, 9, 28, 9, 29, tzinfo=UTC), CONFIG)[0] is False


def test_r6_battery_alarm_over_six_hours_is_actionable_even_overnight() -> None:
    # 22:00 plant-local, judged at 04:01 the next morning. R5 used to hold this
    # until 15:00; battery alarms are now on the same clock as every other class.
    alarm = record("low battery", datetime(
        2026, 9, 27, 16, 30, tzinfo=UTC), "SOC")

    assert alarm_triage.is_actionable(alarm, datetime(
        2026, 9, 27, 22, 31, tzinfo=UTC), CONFIG) == (
        True, CONFIG["escalation_hint"]["SOC"])


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


def test_thermal_matcher_classifies_dessmonitor_message() -> None:
    assert alarm_triage.classify(
        "Over temperature happen at PV circult", CONFIG
    ) == "THERMAL"


def test_normalize_solis_epoch_and_open_state() -> None:
    normalized = alarm_triage.normalize(
        {
            "stationName": "Solis Plant",
            "inverterName": "Solis-1",
            "alarmCode": "1015",
            "alarmMessage": "NO-Grid",
            "alarmBeginTime": 1790575200000,
            "state": "0",
        },
        "SolisCloud",
        CONFIG,
    )

    assert normalized["plant"] == "Solis Plant"
    assert normalized["class"] == "GRID"
    assert normalized["open"] is True
    assert normalized["started_at_utc"].tzinfo is not None


def test_solis_open_is_state_zero_only() -> None:
    """R3 for Solis, read off the live alarmList on 23 Oct 2026: the 2 open
    alarms said state "0" and the 58 cleared ones "2". The gate was written
    before there was an API key and tested for 1, which reads every open Solis
    alarm as resolved, so nothing would ever have been mailed. "1" is
    'processed' (someone handled it), and a handled alarm never escalates."""
    def is_open(state: Any) -> bool:
        return alarm_triage.normalize(
            {"stationName": "P", "alarmMsg": "NO-Grid", "alarmBeginTime": 1790575200000,
             "state": state}, "Solis", CONFIG)["open"]

    assert is_open("0") and is_open(0)
    assert not is_open("1") and not is_open("2") and not is_open(2)


def test_solis_comms_alarm_is_never_mailed() -> None:
    """SolisCloud words a dead logger 'Loss of internet connection' (code 1D4C2),
    which no COMMS pattern matched: it fell to UNKNOWN and took the 6 h clock."""
    normalized = alarm_triage.normalize(
        {"stationName": "P", "alarmCode": "1D4C2", "alarmMsg": "Loss of internet connection",
         "alarmBeginTime": 1790575200000, "state": "0"}, "Solis", CONFIG)

    assert normalized["class"] == "COMMS"
    assert alarm_triage.classify("Loss of internet connection", CONFIG) == "COMMS"
    assert alarm_triage.is_actionable(
        normalized, normalized["started_at_utc"] + timedelta(days=3), CONFIG)[0] is False


def test_an_alarm_its_code_places_is_not_logged_unclassified(caplog: Any) -> None:
    """The UNKNOWN log is how the class map grows from real traffic. Before
    23 Oct 2026 normalize() classified the text first and applied the code after,
    so every 'UV-G-V' (code 1011, GRID) was logged as unclassified."""
    with caplog.at_level("WARNING"):
        normalized = alarm_triage.normalize(
            {"stationName": "P", "alarmCode": "1011", "alarmMsg": "UV-G-V",
             "alarmBeginTime": 1790575200000, "state": "0"}, "Solis", CONFIG)
    assert normalized["class"] == "GRID"
    assert "UV-G-V" not in caplog.text


def test_solis_grid_fault_codes_are_grid() -> None:
    for code, message in [("1010", "OV-G-V01"), ("1011", "UV-G-V"), ("1012", "OV-G-F01"),
                          ("1013", "UN-G-F01"), ("1015", "NO-Grid"), ("1019", "Uac-Unstable")]:
        normalized = alarm_triage.normalize(
            {"stationName": "P", "alarmCode": code, "alarmMsg": message,
             "alarmBeginTime": 1790575200000, "state": "0"}, "Solis", CONFIG)
        assert normalized["class"] == "GRID", code


def test_unknown_alarm_is_logged(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        assert alarm_triage.classify("new vendor fault", CONFIG) == "UNKNOWN"
    assert "new vendor fault" in caplog.text


def test_a_class_can_carry_its_own_dwell_hours() -> None:
    # The way to quieten one class without a code change: 14 hours outlasts a
    # 22:00 -> 07:00 overnight battery dip.
    slow_config = deepcopy(CONFIG)
    slow_config["classes"]["SOC"]["dwell_hours"] = 14
    alarm = record("low battery", datetime(2026, 9, 28, tzinfo=UTC), "SOC")

    assert alarm_triage.is_actionable(
        alarm, datetime(2026, 9, 28, 13, tzinfo=UTC), slow_config
    )[0] is False
    assert alarm_triage.is_actionable(
        alarm, datetime(2026, 9, 28, 14, tzinfo=UTC), slow_config
    )[0] is True


def test_comms_is_suppressed_at_any_age() -> None:
    for hours in (1, 10, 100):
        alarm = record("datalogger lost", datetime(
            2026, 9, 28, tzinfo=UTC), "COMMS")
        assert alarm_triage.is_actionable(
            alarm, datetime(2026, 9, 28, tzinfo=UTC) +
            timedelta(hours=hours), CONFIG
        ) == (False, "COMMS alarms are not mailed")
