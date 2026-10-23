"""Regression tests for tracking issue #19."""

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[7]
SCRIPTS = ROOT / "src" / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_device_alarms as gda  # noqa: E402


def _alarm(**overrides):
    alarm = {
        "pid": 1,
        "pn": "INV-1",
        "id": "alarm-1",
        "status": False,
        "pName": "Plant One",
        "desc": "Over temperature happen at PV circult",
        "gts": "2026-10-23 12:00:00",
        "customer_label": "Customer One",
    }
    alarm.update(overrides)
    return alarm


def test_thermal_matcher_classifies_dessmonitor_message():
    assert gda.alarm_triage.classify(
        "Over temperature happen at PV circult",
        gda.ALARM_TRIAGE_CONFIG,
    ) == "THERMAL"


def test_stale_suppressed_reason_is_cleared_after_escalation(monkeypatch):
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "on")
    alarm = _alarm(gts="2026-10-23 06:00:00")
    key = gda.create_alarm_key(alarm)
    state = {
        key: {
            "send_count": 0,
            "last_sent": None,
            "first_seen": "2026-10-23T00:30:00+00:00",
            "ignored": False,
            "suppressed_reason": "dwell time is less than 6 hours",
        }
    }

    gda.triage_alarms(
        [alarm],
        state,
        platform="dessmonitor",
        utc_now=datetime(2026, 10, 23, 13, 0, tzinfo=timezone.utc),
    )

    to_send = gda.filter_alarms_to_send([alarm], state)
    gda.update_alarm_state(state, to_send)

    assert "suppressed_reason" not in state[key]
    assert state[key]["send_count"] == 1


def test_triage_writes_complete_metadata_for_new_alarm(monkeypatch):
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "on")
    alarm = _alarm()
    state = {}

    gda.triage_alarms(
        [alarm],
        state,
        platform="dessmonitor",
        utc_now=datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc),
    )

    record = state[gda.create_alarm_key(alarm)]
    assert record["class"] == "THERMAL"
    assert record["started_at_utc"] == "2026-10-23T06:30:00+00:00"
    assert record["customer"] == "Customer One"
    assert record["plant"] == "Plant One"
    assert record["message"] == "Over temperature happen at PV circult"
    assert record["send_count"] == 0
    assert record["ignored"] is False


def test_legacy_records_at_cap_or_ignored_are_not_resent(monkeypatch):
    monkeypatch.setattr(
        gda,
        "datetime",
        type("FixedDateTime", (datetime,), {
            "now": classmethod(lambda cls: datetime(2026, 10, 23, 15, 0)),
        }),
    )
    capped = _alarm(id="capped")
    ignored = _alarm(id="ignored")
    state = {
        gda.create_alarm_key(capped): {
            "send_count": 36,
            "ignored": False,
        },
        gda.create_alarm_key(ignored): {
            "send_count": 1,
            "ignored": True,
        },
    }

    assert gda.filter_alarms_to_send([capped, ignored], state) == []
    assert state[gda.create_alarm_key(capped)]["ignored"] is True
