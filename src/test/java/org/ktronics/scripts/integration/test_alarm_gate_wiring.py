"""
Behaviour the six-hour gate's WIRING must have (UC3 / six-hour-gate).

These are not tests of alarm_triage.py itself — that is covered by
test_alarm_triage.py. These cover what happens where the gate is plugged into
generate_device_alarms.py, which is a separate place to get wrong.

Both encode findings from the review of PR #17. They SKIP entirely until the
gate is actually wired in, so a branch without the wiring stays green; on a
branch that wires it up they run, and they must pass before it merges.
"""

import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent.parent.parent / \
    "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import generate_device_alarms as gda  # noqa: E402

# Plant-local time is UTC+5:30; `gts` is written in it.
PLANT_OFFSET = timedelta(minutes=330)

pytestmark = pytest.mark.skipif(
    not hasattr(gda, "triage_alarms"),
    reason="the six-hour gate is not wired into generate_device_alarms.py yet",
)


def _alarm(**overrides):
    """One ShineMonitor-shaped alarm. Override only what a test cares about."""
    alarm = {
        "pid": 99001,
        "plant": "Gate Wiring Test Plant",
        "pn": "SN-GATE-TEST",
        "id": "W-GATE-TEST",
        "alias": "Inverter 1",
        "desc": "Inverter bus voltage is too low",
        "gts": "2026-10-08 11:30:00",
        "status": False,
        "customer_label": "Test-Customer",
    }
    alarm.update(overrides)
    return alarm


def test_a_triage_error_still_lets_the_alarm_through():
    """
    If our own code throws while judging an alarm, the alarm must still be sent.

    Suppressing it turns a bug on our side into a fault nobody ever hears about:
    no failing test, no error mail, nothing in the report. The only symptom is an
    inbox quieter than it should be, which looks exactly like good news — so this
    can lose real alarms for months before anyone notices.

    The gate's own rule says the same thing about messages it cannot classify:
    an UNKNOWN takes the six-hour clock rather than being dropped. A crash
    deserves that treatment more, not less: an unrecognised message is at least
    data we managed to read.
    """
    utc_now = datetime(2026, 10, 8, 6, 0, tzinfo=timezone.utc)
    # An unparseable timestamp makes normalize() raise ValueError.
    alarm = _alarm(gts="not a timestamp")
    state = {}

    actionable = gda.triage_alarms(
        [alarm], state, platform="shinemonitor", utc_now=utc_now)

    assert alarm in actionable, (
        "a triage error must let the alarm through, not swallow it — "
        "fail loudly and send, rather than failing quietly and dropping a "
        "fault the customer is paying us to catch"
    )


def test_a_vendor_code_outranks_the_message_text(monkeypatch):
    """
    Where a portal gives a numeric fault code, that code decides the class.

    normalize() already applies the vendor_codes map, and the config says why:
    a code "is stabler than the message string". Re-running classify() on the
    message afterwards throws that away silently — the mapping stays in the
    config looking effective while nothing consults it.

    Here the message says battery (SOC) and the code says hardware. Hardware
    must win.
    """
    utc_now = datetime(2026, 10, 8, 6, 0, tzinfo=timezone.utc)

    config = copy.deepcopy(gda.ALARM_TRIAGE_CONFIG)
    config.setdefault("vendor_codes", {})["shinemonitor"] = {"9001": "HARDWARE"}
    monkeypatch.setattr(gda, "ALARM_TRIAGE_CONFIG", config)

    # One hour old in plant-local terms, so it is held by either gate and the
    # class lands in state where we can read it back.
    started_local = utc_now + PLANT_OFFSET - timedelta(hours=1)
    alarm = _alarm(
        desc="Battery voltage is too low",          # message alone => SOC
        alarmCode="9001",                           # vendor code  => HARDWARE
        gts=started_local.strftime("%Y-%m-%d %H:%M:%S"),
    )
    state = {}

    gda.triage_alarms([alarm], state,
                      platform="shinemonitor", utc_now=utc_now)

    key = gda.create_alarm_key(alarm)
    assert key in state, "a held alarm must be recorded in state"
    assert state[key]["class"] == "HARDWARE", (
        "the vendor code must decide the class — reclassifying on the message "
        "after normalize() has run discards the vendor_codes map entirely"
    )
