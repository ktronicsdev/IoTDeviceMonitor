#!/usr/bin/env python3
"""
SolaX Cloud alarms through the six-hour gate (UC21) - tests.

No network. The alarm records are shaped like the fleet's live
/openapi/v2/alarm/page_alarm_info of 11 Nov 2026: plant-local start times
("2026-09-18 10:59:31"), alarmState 1 ongoing and 0 closed, a numeric
errorCode, and no plant id on the record (it comes from the plant asked about).
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[6]
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_solax_alarms as sa  # noqa: E402
import generate_device_alarms as gda  # noqa: E402
import solax_cloud as sc  # noqa: E402

TZ = timezone(timedelta(minutes=330))
NOW = datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc)      # 10:30 plant time
PLANT = {"plantId": "155", "plantName": "Test Plant"}


def alarm(hours_open, name="GridFreqFault", code="3", state=1, level=2, sn="MC206TK3871049"):
    start = (NOW - timedelta(hours=hours_open)).astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")
    raw = {"alarmStartTime": start, "alarmName": name, "errorCode": code, "alarmType": "1",
           "alarmLevel": level, "alarmState": state, "alarmEndTime": None,
           "registerNo": "SRER6VXP7Z", "deviceType": 1, "deviceModel": "16", "deviceSn": sn}
    if state == 0:
        raw["alarmEndTime"] = start
    return raw


def entries(*raws, plant=PLANT):
    return [(plant, raw) for raw in raws]


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "on")


def run_gate(raws, state=None, plant=PLANT):
    state = {} if state is None else state
    alarms, _ = sa.select_alarms(entries(*raws, plant=plant), NOW)
    sa.prune_state(state, {gda.create_alarm_key(a) for a in alarms})
    passed = gda.triage_alarms(alarms, state, sa.PLATFORM, NOW)
    return gda.filter_alarms_to_send(passed, state), state


class TestSelect:
    def test_only_open_alarms_are_kept(self):
        alarms, counts = sa.select_alarms(entries(alarm(7), alarm(8, state=0)), NOW)
        assert len(alarms) == 1 and counts["closed"] == 1

    def test_no_state_and_no_end_time_counts_as_open(self):
        raw = alarm(7)
        del raw["alarmState"]
        assert sa.is_open(raw)

    def test_a_stale_alarm_is_skipped(self):
        """Pasindu 6kw's records were 53 days old on a dead inverter."""
        alarms, counts = sa.select_alarms(entries(alarm(24 * 53), alarm(24 * 13)), NOW)
        assert len(alarms) == 1 and counts["stale"] == 1

    def test_gate_fields_come_from_the_plant_and_the_record(self):
        a = sa.select_alarms(entries(alarm(7)), NOW)[0][0]
        assert a["pid"] == "155" and a["plant"] == "Test Plant"
        assert a["sn"] == "MC206TK3871049" and a["vendor_code"] == "3" and a["open"] is True
        assert a["id"] == f"3@{alarm(7)['alarmStartTime']}"

    def test_start_time_is_read_as_plant_local(self):
        """'11:00' in Sri Lanka is 05:30 UTC; read as UTC it would be 5.5 h late."""
        a = sa.to_gate_alarm({**alarm(1), "alarmStartTime": "2026-11-12 11:00:00"}, PLANT)
        assert a["started_at_utc"] == int(datetime(2026, 11, 12, 5, 30, tzinfo=timezone.utc).timestamp())

    def test_two_faults_with_one_code_are_two_alarms(self):
        a, b = (sa.to_gate_alarm(alarm(h), PLANT) for h in (7, 9))
        assert gda.create_alarm_key(a) != gda.create_alarm_key(b)


class TestGate:
    def test_a_three_hour_cut_is_silent(self, gate_on):
        to_send, state = run_gate([alarm(3)])
        assert to_send == []
        held = next(iter(state.values()))
        assert held["class"] == "GRID" and "less than 6 hours" in held["suppressed_reason"]

    def test_a_seven_hour_grid_fault_mails(self, gate_on):
        assert len(run_gate([alarm(7)])[0]) == 1

    def test_the_six_hour_line(self, gate_on):
        assert run_gate([alarm(5.98)])[0] == []
        assert len(run_gate([alarm(6.02)])[0]) == 1

    def test_every_fleet_grid_alarm_is_grid_class(self, gate_on):
        names = {"GridVoltFault": "2", "GridFreqFault": "3", "GridLostFault": "1",
                 "GridVolt10MFault": "7", "Ac10Mins_Voltage_Fault": "7", "GRID_PHS_LOSS": "102",
                 "GRID_LOSS": "91", "GRID_UNBLC": "103", "GRID_UVP3": "108"}
        for name, code in names.items():
            _, state = run_gate([alarm(3, name=name, code=code)])
            assert next(iter(state.values()))["class"] == "GRID", name

    def test_a_leakage_current_fault_is_hardware_and_rides_the_same_clock(self, gate_on):
        _, state = run_gate([alarm(3, name="ResidualOCP", code="11")])
        assert next(iter(state.values()))["class"] == "HARDWARE"
        assert len(run_gate([alarm(7, name="ResidualOCP", code="11")])[0]) == 1

    def test_an_unknown_alarm_still_mails(self, gate_on):
        to_send, _ = run_gate([alarm(7, name="BrandNewFault", code="999")])
        assert len(to_send) == 1

    def test_three_sends_four_hours_apart(self, gate_on):
        to_send, state = run_gate([alarm(7)])
        gda.update_alarm_state(state, to_send)
        assert run_gate([alarm(7)], state)[0] == []
        key = to_send[0]["alarm_key"]
        state[key]["send_count"] = 3
        state[key]["last_sent"] = (datetime.now() - timedelta(hours=5)).isoformat()
        assert run_gate([alarm(7)], state)[0] == []

    def test_cleared_alarms_are_forgotten(self, gate_on):
        _, state = run_gate([alarm(3)])
        assert len(state) == 1
        _, state = run_gate([alarm(3, state=0)], state)
        assert state == {}


class TestDaytimeWindow:
    def test_inside_the_window(self):
        assert sa.in_daytime(datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc))      # 10:30 local

    def test_dawn_dusk_and_night_are_outside(self):
        for hour_utc, minute in ((2, 0), (11, 30), (14, 0), (20, 0)):                # 07:30 17:00 19:30 01:30
            assert not sa.in_daytime(datetime(2026, 11, 12, hour_utc, minute, tzinfo=timezone.utc))

    def test_the_window_is_plant_local_not_utc(self):
        # 04:00 UTC is 09:30 in Sri Lanka: inside, though it is night in UTC terms.
        assert sa.in_daytime(datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc))


class TestEmail:
    def test_names_the_plant_the_fault_and_the_first_check(self, gate_on):
        to_send, _ = run_gate([alarm(7.5)], plant={"plantId": "1", "plantName": "Umesh 8kv"})
        body = sa.format_email(to_send, NOW)
        assert "Umesh 8kv - model 16 (MC206TK3871049)" in body
        assert "GridFreqFault (code 3, failure, class GRID)" in body
        assert "7.5 h" in body and "Send 1/3" in body
        assert gda.ALARM_TRIAGE_CONFIG["escalation_hint"]["GRID"] in body

    def test_start_time_is_shown_as_plant_local(self, gate_on):
        to_send, _ = run_gate([alarm(7)])
        assert f"Open since {alarm(7)['alarmStartTime']}" in sa.format_email(to_send, NOW)


class FakeClient:
    """Answers plants and alarms from canned lists, paging like SolaX does."""

    def __init__(self, plants=(), alarms=None, size=2):
        self.plants, self.alarms, self.size, self.calls = list(plants), alarms or {}, size, []

    def paged(self, path, params):
        self.calls.append((path, params))
        if path == sa.PLANT_PATH:
            return self.plants
        return self.alarms.get(params["plantId"], [])


class TestFetch:
    def test_only_ongoing_alarms_are_asked_for_per_plant(self):
        client = FakeClient(alarms={"1": [alarm(7)], "2": [alarm(8)]})
        out = sa.fetch_alarms(client, [{"plantId": "1", "plantName": "A"},
                                       {"plantId": "2", "plantName": "B"}])
        assert [p["plantName"] for p, _ in out] == ["A", "B"]
        assert all(p["alarmState"] == 1 for _, p in client.calls)

    def test_plants_are_residential_only(self):
        client = FakeClient(plants=[PLANT])
        assert sa.fetch_plants(client) == [PLANT]
        assert client.calls[0][1] == {"businessType": 1}


class FakeTransport:
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, method, url, form, headers, timeout):
        self.requests.append((method, url, form, headers))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


TOKEN = (200, {"code": 0, "result": {"access_token": "tok-1", "expires_in": 2592000}})
PAGE = "/openapi/v2/alarm/page_alarm_info"


def ok(records, pages=1):
    return (200, {"code": 10000, "message": "Success",
                  "result": {"records": records, "pages": pages, "total": len(records)}})


CFG = {"client_id": "cid", "client_secret": "s3cret-solax"}


def client(*replies):
    transport = FakeTransport(*replies)
    return sc.SolaxClient.from_config(CFG, transport=transport, sleep=lambda s: None), transport


class TestClient:
    def test_other_paths_are_refused_before_any_request(self):
        c, transport = client()
        for path in ("/openapi/v2/device/control", "/openapi/v2/plant/realtime_data", "/x"):
            with pytest.raises(sc.SolaxError, match="read-only"):
                c.get(path)
        assert transport.requests == []

    def test_logs_in_once_with_the_client_credentials_grant(self):
        c, transport = client(TOKEN, ok([]), ok([]))
        c.get(PAGE, {"pageNo": 1})
        c.get(PAGE, {"pageNo": 2})
        method, url, form, _ = transport.requests[0]
        assert method == "POST" and url.endswith("/openapi/auth/oauth/token")
        assert form["grant_type"] == "client_credentials" and form["client_id"] == "cid"
        assert [r[3].get("Authorization") for r in transport.requests[1:]] == ["bearer tok-1"] * 2

    def test_a_refused_token_logs_in_again_once(self):
        refused = (200, {"code": 10401, "message": "token invalid"})
        c, transport = client(TOKEN, refused, TOKEN, ok([{"a": 1}]))
        assert c.get(PAGE)["records"] == [{"a": 1}]
        assert len(transport.requests) == 4

    def test_a_rate_limit_is_retried_once(self):
        c, _ = client(TOKEN, (429, None), ok([]))
        assert c.get(PAGE)["records"] == []

    def test_a_network_error_is_retried_once_then_raised(self):
        down = sc.SolaxError("network error: timed out")
        c, _ = client(TOKEN, down, down)
        with pytest.raises(sc.SolaxError, match="network"):
            c.get(PAGE)

    def test_a_failed_login_never_names_the_secret(self):
        c, _ = client((200, {"code": 10400, "message": "bad client"}))
        with pytest.raises(sc.SolaxError) as err:
            c.get(PAGE)
        assert "s3cret-solax" not in str(err.value)

    def test_paged_follows_the_page_count(self):
        c, transport = client(TOKEN, ok([1, 2], pages=2), ok([3], pages=2))
        assert c.paged(PAGE, {"plantId": "1"}) == [1, 2, 3]
        assert [r[1].split("pageNo=")[1][0] for r in transport.requests[1:]] == ["1", "2"]
