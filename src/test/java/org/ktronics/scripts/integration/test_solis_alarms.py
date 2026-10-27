#!/usr/bin/env python3
"""
SolisCloud alarms through the six-hour gate (UC18) — tests.

No network. The alarm records are shaped like the live alarmList of 23 Oct 2026:
open alarms read state "0", cleared ones "2", and every record carries the
inverter model in `machine`.
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[6]
ROOT = SRC.parent
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_solis_alarms as sa  # noqa: E402
import generate_device_alarms as gda  # noqa: E402

NOW = datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc)
HYBRID = "S6-EH1P5K-L-PLUS"
GRID_TIE = "S6-GR1P5K-S"


def ms(dt):
    return int(dt.timestamp() * 1000)


def alarm(hours_open, machine=HYBRID, code="1015", msg="NO-Grid", state="0",
          station="Test Plant", sn="1031720254230285"):
    return {
        "stationId": "1298491919450225535", "stationName": station,
        "alarmDeviceSn": sn, "machine": machine,
        "alarmCode": code, "alarmMsg": msg, "state": state,
        "alarmBeginTime": ms(NOW - timedelta(hours=hours_open)),
        "alarmEndTime": ms(NOW),
    }


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "on")


def run_gate(raw, state=None):
    """select -> prune -> six-hour gate -> 3x/4h filter, as main() runs them."""
    state = {} if state is None else state
    alarms, _ = sa.select_alarms(raw)
    sa.prune_state(state, {gda.create_alarm_key(a) for a in alarms})
    passed = gda.triage_alarms(alarms, state, sa.PLATFORM, NOW)
    return gda.filter_alarms_to_send(passed, state), state


class TestModel:
    @pytest.mark.parametrize("machine", [
        "S6-EH1P5K-L-PLUS", "S6-EH1P5K-L-PRO", "S6-EH3P10K-H", "S6-EH3P8K02-NV-YD-L",
        "RHI-5K-48ES-5G"])
    def test_hybrids(self, machine):
        assert sa.is_hybrid(machine) is True

    @pytest.mark.parametrize("machine", ["S6-GR1P5K-S", "S5-GR3P10K", "S5-GC40K", "S6-GR1P3K-M"])
    def test_grid_tie(self, machine):
        assert sa.is_hybrid(machine) is False

    def test_missing_model_is_unknown(self):
        assert sa.is_hybrid("") is None and sa.is_hybrid(None) is None


class TestOpen:
    def test_state_zero_is_open(self):
        assert sa.is_open({"state": "0"}) and sa.is_open({"state": 0})

    def test_processed_restored_and_missing_are_not(self):
        for raw in ({"state": "1"}, {"state": "2"}, {}, {"state": False}):
            assert not sa.is_open(raw), raw


class TestSelect:
    def test_keeps_open_hybrid_alarms_only(self):
        alarms, counts = sa.select_alarms([
            alarm(7), alarm(7, state="2"), alarm(16, machine=GRID_TIE)])
        assert len(alarms) == 1
        assert counts["closed"] == 1 and counts["grid_tie"] == 1

    def test_unknown_model_is_kept(self):
        """A gap in the feed must never silence alerting."""
        alarms, counts = sa.select_alarms([alarm(7, machine="")])
        assert len(alarms) == 1 and counts["unknown_model"] == 1

    def test_excluded_plant_is_dropped(self, monkeypatch):
        monkeypatch.setattr(sa.exclusions, "is_plant_excluded", lambda name: name == "Gone")
        alarms, counts = sa.select_alarms([alarm(7, station="Gone"), alarm(7)])
        assert len(alarms) == 1 and counts["excluded"] == 1

    def test_one_occurrence_keeps_one_key(self):
        a = sa.to_gate_alarm(alarm(7))
        assert gda.create_alarm_key(a) == (
            f"1298491919450225535:1031720254230285:1015@{a['alarmBeginTime']}")
        assert a["plant"] == "Test Plant" and a["desc"] == "NO-Grid" and a["alias"] == HYBRID


class TestGate:
    def test_a_three_hour_cut_is_silent(self, gate_on):
        to_send, state = run_gate([alarm(3)])
        assert to_send == []
        held = next(iter(state.values()))
        assert held["class"] == "GRID" and "less than 6 hours" in held["suppressed_reason"]

    def test_a_seven_hour_outage_mails(self, gate_on):
        to_send, _ = run_gate([alarm(7)])
        assert len(to_send) == 1

    def test_the_six_hour_line(self, gate_on):
        assert run_gate([alarm(5.98)])[0] == []
        assert len(run_gate([alarm(6.02)])[0]) == 1

    def test_a_battery_alarm_rides_the_same_clock(self, gate_on):
        """No Solis battery alarm has been seen yet, so its exact wording is not
        in the class map: it lands in UNKNOWN, which takes the 6 h clock too."""
        assert run_gate([alarm(5, code="2099", msg="BMS-Alarm")])[0] == []
        assert len(run_gate([alarm(7, code="2099", msg="BMS-Alarm")])[0]) == 1

    def test_a_dead_logger_is_never_mailed(self, gate_on):
        to_send, _ = run_gate([alarm(72, code="1D4C2", msg="Loss of internet connection")])
        assert to_send == []

    def test_grid_tie_overnight_alarm_is_never_mailed(self, gate_on):
        """A grid-tie NO-Grid stays open until the inverter wakes: 16 h is normal."""
        assert run_gate([alarm(16, machine=GRID_TIE)])[0] == []

    def test_three_sends_four_hours_apart(self, gate_on):
        to_send, state = run_gate([alarm(7)])
        gda.update_alarm_state(state, to_send)
        assert run_gate([alarm(7)], state)[0] == []          # same hour: too soon
        key = to_send[0]["alarm_key"]
        state[key]["send_count"] = 3
        state[key]["last_sent"] = (datetime.now() - timedelta(hours=5)).isoformat()
        assert run_gate([alarm(7)], state)[0] == []          # spent its three sends


class TestState:
    def test_cleared_alarms_are_forgotten(self, gate_on):
        _, state = run_gate([alarm(3)])
        assert len(state) == 1
        _, state = run_gate([alarm(3, state="2")], state)
        assert state == {}

    def test_prune_keeps_live_keys(self):
        state = {"a": {}, "b": {}}
        assert sa.prune_state(state, {"b"}) == {"b": {}}


class TestEmail:
    def test_names_the_plant_the_fault_and_the_first_check(self, gate_on):
        to_send, _ = run_gate([alarm(7.5, station="Surath 5KV")])
        body = sa.format_email(to_send, NOW)
        assert "Surath 5KV" in body and HYBRID in body and "NO-Grid" in body
        assert "7.5 h" in body and "Send 1/3" in body
        assert gda.ALARM_TRIAGE_CONFIG["escalation_hint"]["GRID"] in body

    def test_start_time_is_plant_local(self, gate_on):
        to_send, _ = run_gate([alarm(7)])
        start_local = (NOW - timedelta(hours=7)).astimezone(timezone(timedelta(minutes=330)))
        assert f"Open since {start_local:%Y-%m-%d %H:%M}" in sa.format_email(to_send, NOW)


class FakeClient:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def post(self, resource, payload):
        self.calls.append((resource, payload))
        records = self.pages[payload["pageNo"] - 1]
        total = sum(len(p) for p in self.pages)
        return {"success": True, "code": "0", "data": {"page": {"records": records, "total": total}}}


class TestFetch:
    def test_pages_until_done_over_a_seven_day_window(self, monkeypatch):
        monkeypatch.setattr(sa.time, "sleep", lambda s: None)
        client = FakeClient([[alarm(1)] * 100, [alarm(1)] * 20])
        out = sa.fetch_alarms(client, date(2026, 10, 23))
        assert len(out) == 120 and len(client.calls) == 2
        resource, payload = client.calls[0]
        assert resource == "/v1/api/alarmList"
        assert payload["alarmBeginTime"] == "2026-10-16" and payload["alarmEndTime"] == "2026-10-23"

    def test_a_failed_call_stops_the_run(self):
        class Failing:
            def post(self, resource, payload):
                return {"success": False, "code": "1", "msg": "denied"}
        with pytest.raises(SystemExit):
            sa.fetch_alarms(Failing(), date(2026, 10, 23))


class TestWiring:
    def test_workflow_runs_hourly_and_keeps_its_state(self):
        text = (ROOT / ".github" / "workflows" / "trigger-solis-alarms.yml").read_text(encoding="utf-8")
        assert "check_solis_alarms.py" in text
        assert 'cron: "33 * * * *"' in text
        assert "state/solis_alarms_state.json" in text

    def test_email_channel_has_a_flag(self):
        import json
        flags = json.loads((SRC / "main" / "java" / "org" / "ktronics" / "config" / "features.json")
                           .read_text(encoding="utf-8"))
        assert flags["emails"]["solis_alarms_admin"] is True
        assert sa.EMAIL_CHANNEL == "emails.solis_alarms_admin"
