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
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent.parent.parent / \
    "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# Walk up to the repo root rather than counting `.parent`s: pytest.ini already
# puts the scripts on sys.path, so SCRIPTS_DIR above is vestigial and does not
# actually resolve — counting from it lands outside the tree.
_FEATURES_REL = Path("src/main/java/org/ktronics/config/features.json")


def _features_json():
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _FEATURES_REL
        if candidate.is_file():
            return candidate
    raise AssertionError(f"could not find {_FEATURES_REL} above {__file__}")


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


def test_a_triage_error_still_lets_the_alarm_through(monkeypatch):
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
    # The gate ships off, so a test that means to exercise it must say so —
    # otherwise triage never runs and this passes without testing anything.
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "true")

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
    # As above: the gate ships off, so switch it on to exercise it.
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "true")

    utc_now = datetime(2026, 10, 8, 6, 0, tzinfo=timezone.utc)

    config = copy.deepcopy(gda.ALARM_TRIAGE_CONFIG)
    config.setdefault("vendor_codes", {})[
        "shinemonitor"] = {"9001": "HARDWARE"}
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


def test_the_gate_is_behind_a_flag():
    """
    Every other channel that can go quiet has a flag; so must this one.

    The gate decides which faults a human is told about. If it is wrong in the
    quiet direction it does not announce itself — the symptom is an inbox that
    looks healthy. The flag is the off switch for when that happens.

    This asserted `is False` until 2026-10-11, because the gate had to merge
    inert. It was then switched on deliberately, so the assertion is now only
    that the flag exists and is readable — whether it is on is an operational
    decision, not something a test should pin.
    """
    flags = json.loads(_features_json().read_text(encoding="utf-8"))

    assert "six_hour_gate" in flags.get("alerts", {}), (
        "add `alerts.six_hour_gate` to features.json — the gate suppresses "
        "alarms, and every suppressing channel in this repo carries a flag"
    )
    value = flags["alerts"]["six_hour_gate"]
    assert value in (True, False, "off", "dry_run", "on"), (
        "`alerts.six_hour_gate` must be one of the three modes, or a legacy "
        f"boolean meaning on/off. Got: {value!r}"
    )


def test_the_gate_does_nothing_while_its_flag_is_off(monkeypatch):
    """
    With the flag off, every alarm passes through untouched.

    The check belongs inside `triage_alarms` rather than at one call site —
    there are two callers today (ShineMonitor and DessMonitor) and a flag that
    only one of them honours is worse than no flag, because it looks like
    cover it does not provide.
    """
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "false")

    utc_now = datetime(2026, 10, 9, 6, 0, tzinfo=timezone.utc)
    # One hour old: comfortably inside the six-hour clock, so the gate would
    # hold this one if it were switched on.
    started_local = utc_now + PLANT_OFFSET - timedelta(hours=1)
    alarm = _alarm(gts=started_local.strftime("%Y-%m-%d %H:%M:%S"))
    state = {}

    actionable = gda.triage_alarms(
        [alarm], state, platform="shinemonitor", utc_now=utc_now)

    assert alarm in actionable, (
        "with alerts.six_hour_gate off, triage_alarms must pass every alarm "
        "through unchanged — a flag that does not actually disable the "
        "behaviour is worse than not having one"
    )


# --- #20: the gate has three modes, not two -------------------------------
#
# `off` and `on` are what the boolean already meant. `dry_run` is the one that
# did not exist when the gate was switched on for live traffic on 2026-10-11,
# and its absence is why nobody could say in advance which of 142 alarms would
# be silenced. It decides and records, and suppresses nothing.


def _held_alarm(utc_now):
    """An alarm one hour old in plant-local terms — the gate would hold it."""
    started_local = utc_now + PLANT_OFFSET - timedelta(hours=1)
    return _alarm(gts=started_local.strftime("%Y-%m-%d %H:%M:%S"))


def test_dry_run_records_what_it_would_hold_but_holds_nothing(monkeypatch):
    """
    The whole point: find out what the gate would silence, while it silences
    nothing. The alarm still reaches the admin, and state says it would not
    have.
    """
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "dry_run")

    utc_now = datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc)
    alarm = _held_alarm(utc_now)
    state = {}

    actionable = gda.triage_alarms(
        [alarm], state, platform="shinemonitor", utc_now=utc_now)

    assert alarm in actionable, (
        "dry_run must suppress nothing — that is the only reason it is safe "
        "to turn on without watching it first"
    )

    key = gda.create_alarm_key(alarm)
    assert key in state, "dry_run must still record its decision in state"
    reason = state[key].get("suppressed_reason", "")
    assert reason.startswith("DRY RUN"), (
        "a dry-run decision must be marked as one, so nobody reads the state "
        f"file and thinks the alarm was actually held. Got: {reason!r}"
    )


def test_mode_on_still_holds(monkeypatch):
    """`on` keeps doing exactly what `true` did."""
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "on")

    utc_now = datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc)
    alarm = _held_alarm(utc_now)
    state = {}

    actionable = gda.triage_alarms(
        [alarm], state, platform="shinemonitor", utc_now=utc_now)

    assert alarm not in actionable, "`on` must hold what the gate decides to hold"
    assert not state[gda.create_alarm_key(alarm)][
        "suppressed_reason"].startswith("DRY RUN")


def test_mode_off_holds_nothing_and_records_nothing(monkeypatch):
    """`off` keeps doing exactly what `false` did: the gate never runs."""
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "off")

    utc_now = datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc)
    alarm = _held_alarm(utc_now)
    state = {}

    actionable = gda.triage_alarms(
        [alarm], state, platform="shinemonitor", utc_now=utc_now)

    assert alarm in actionable


def test_a_legacy_boolean_still_means_what_it_used_to(monkeypatch):
    """
    Migration guard. features.json carried `true`/`false` before this change,
    and a stale branch or an old override may still. `true` must mean `on` and
    `false` must mean `off` — never silently fall through to a default, which
    for a suppressing feature would be the dangerous kind of quiet.
    """
    utc_now = datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc)

    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "true")
    held_state = {}
    held = gda.triage_alarms([_held_alarm(utc_now)], held_state,
                             platform="shinemonitor", utc_now=utc_now)
    assert held == [], "a legacy `true` must still mean the gate is on"

    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "false")
    passed_state = {}
    alarm = _held_alarm(utc_now)
    passed = gda.triage_alarms([alarm], passed_state,
                               platform="shinemonitor", utc_now=utc_now)
    assert alarm in passed, "a legacy `false` must still mean the gate is off"


def test_stale_suppressed_reason_is_cleared_after_escalation(monkeypatch):
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "on")
    alarm = _alarm(gts="2026-10-08 05:30:00")
    key = gda.create_alarm_key(alarm)
    state = {
        key: {
            "send_count": 0,
            "last_sent": None,
            "first_seen": "2026-10-08T00:30:00+00:00",
            "ignored": False,
            "suppressed_reason": "dwell time is less than 6 hours",
        }
    }

    gda.triage_alarms(
        [alarm],
        state,
        platform="dessmonitor",
        utc_now=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
    )

    to_send = gda.filter_alarms_to_send([alarm], state)
    gda.update_alarm_state(state, to_send)

    assert "suppressed_reason" not in state[key]
    assert state[key]["send_count"] == 1


def test_triage_and_update_write_consistent_alarm_metadata(monkeypatch):
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "on")
    alarm = _alarm()
    state = {}

    gda.triage_alarms(
        [alarm],
        state,
        platform="dessmonitor",
        utc_now=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
    )
    key = gda.create_alarm_key(alarm)
    fields = gda.alarm_fields(alarm)
    expected = dict(zip(("customer", "plant", "message"), fields))

    assert {field: state[key][field] for field in expected} == expected

    gda.update_alarm_state(state, [{
        "alarm": alarm,
        "alarm_key": key,
        "send_count": 0,
    }])

    assert {field: state[key][field] for field in expected} == expected
