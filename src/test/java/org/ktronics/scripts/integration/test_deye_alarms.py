#!/usr/bin/env python3
"""
Deye Cloud alarms through the six-hour gate (UC19) - tests.

No network. The alert records are shaped like the fleet's live
/station/alertList of 5 Nov 2026: epoch-second start and end times, status 0
for a closed alarm (all 59 of that month's were closed, each with an end time),
alert names with Deye's own no-break spaces, and an alertCode that is a bit
number inside a protocol word (ERR3 code 12 is F13, ERR5 code 12 is F45).
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[6]
ROOT = SRC.parent
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_deye_alarms as da  # noqa: E402
import deye_cloud as dc  # noqa: E402
import generate_device_alarms as gda  # noqa: E402

NOW = datetime(2026, 11, 5, 14, 0, tzinfo=timezone.utc)
HYBRID, OFF_GRID, GRID_TIE = "0_5407_1", "0_5434_1", "0_5406_1"
STATION = {"id": 61950873, "name": "Test Plant"}
PRODUCTS = {"2501215093": HYBRID, "2502000001": OFF_GRID, "2503000002": GRID_TIE}


def alert(hours_open, sn="2501215093", name="F35 AC_NoUtillity_Fault", code="2",
          protocol="ERR5", status=1, level=1):
    start = int((NOW - timedelta(hours=hours_open)).timestamp())
    raw = {"deviceSn": sn, "deviceType": "INVERTER", "alertName": name,
           "protocolName": protocol, "alertCode": code, "level": level, "impact": 1,
           "alertStartTime": start, "status": status, "alertId": "x"}
    if status == 0:
        raw["alertEndTime"] = start + 300
    return raw


def entries(*raws, station=STATION):
    return [(station, raw) for raw in raws]


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "on")


def run_gate(raws, state=None, station=STATION):
    """select -> prune -> six-hour gate -> 3x/4h filter, as main() runs them."""
    state = {} if state is None else state
    alarms, _ = da.select_alarms(entries(*raws, station=station), PRODUCTS)
    da.prune_state(state, {gda.create_alarm_key(a) for a in alarms})
    passed = gda.triage_alarms(alarms, state, da.PLATFORM, NOW)
    return gda.filter_alarms_to_send(passed, state), state


class TestProduct:
    @pytest.mark.parametrize("pid", ["0_5407_1", "0_5411_1", "0_5412_1", "0_5434_1"])
    def test_hybrids_and_off_grids_are_kept(self, pid):
        assert da.is_grid_tie(pid) is False

    def test_the_grid_tie_product(self):
        assert da.is_grid_tie(GRID_TIE) is True

    def test_missing_or_new_product_is_unknown(self):
        for pid in (None, "", "0_9999_1"):
            assert da.is_grid_tie(pid) is None


class TestOpen:
    def test_status_one_is_open_and_zero_closed(self):
        assert da.is_open({"status": 1}) and da.is_open({"status": "1"})
        assert not da.is_open({"status": 0, "alertEndTime": 1})

    def test_without_a_status_an_alarm_with_no_end_is_open(self):
        assert da.is_open({}) and not da.is_open({"alertEndTime": 1793579512})

    def test_open_alarm_is_not_read_as_resolved_by_the_gate(self, gate_on):
        """The gate's generic reader takes status 1 as closed (ShineMonitor's
        convention); the adapter must say open plainly or nothing is ever mailed."""
        a = da.to_gate_alarm(alert(7), STATION, HYBRID)
        record = gda.alarm_triage.normalize(a, "Deye", gda.ALARM_TRIAGE_CONFIG)
        assert record["open"] is True


class TestNames:
    def test_no_break_and_double_spaces_are_cleaned(self):
        assert da.clean_name("W3\xa0Grid_Phase_Wrong") == "W3 Grid_Phase_Wrong"
        assert da.clean_name("BusUnbalance_Fault   F26") == "BusUnbalance_Fault F26"

    @pytest.mark.parametrize("name,cls", [
        ("F13Grid_Mode_changed", "GRID"), ("W3\xa0Grid_Phase_Wrong", "GRID"),
        ("AC_NoUtillity_Fault  F35", "GRID"), ("F47AC_OverFreq_Fault", "GRID"),
        ("AC_UnderFreq_Fault F48", "GRID"), ("F45AC_UV_OverVolt_Fault", "GRID"),
        ("AC_WU_U nderVolt_Fault F42", "GRID"),
        ("F56DC_VoltLow_Fault", "SOC"), ("F46Backup_Battery_Fault", "SOC"),
        ("F25AC_Active_Batt_Fault", "SOC"),
        ("BusUnbalance_Fault   F26", "HARDWARE"), ("F55DC_VoltHigh_Fault", "HARDWARE"),
        ("F34\xa0AC_Overload_Fault", "LOAD"),
        ("F59AC_V_GridCurr_High_Fault", "UNKNOWN"),
    ])
    def test_the_fleets_alarm_names_classify(self, name, cls):
        a = da.to_gate_alarm(alert(1, name=name), STATION, HYBRID)
        assert gda.alarm_triage.classify(a["message"], gda.ALARM_TRIAGE_CONFIG) == cls

    def test_no_deye_alarm_is_taken_for_a_dead_logger(self):
        """COMMS is never mailed, so a real fault read as COMMS would be silenced."""
        for name in ("F13", "AC_BackFeed_Fault F57", "F58 BMS_Comm_Fault"):
            assert gda.alarm_triage.classify(name, gda.ALARM_TRIAGE_CONFIG) != "COMMS"


class TestSelect:
    def test_keeps_open_alarms_off_grid_tie_inverters(self):
        alarms, counts = da.select_alarms(entries(
            alert(7), alert(7, status=0), alert(7, sn="2503000002"),
            alert(7, sn="2502000001")), PRODUCTS)
        assert [a["sn"] for a in alarms] == ["2501215093", "2502000001"]
        assert counts["closed"] == 1 and counts["grid_tie"] == 1

    def test_unknown_product_is_kept(self):
        """A gap in the feed must never silence alerting."""
        alarms, counts = da.select_alarms(entries(alert(7, sn="9999")), PRODUCTS)
        assert len(alarms) == 1 and counts["unknown_product"] == 1
        assert alarms[0]["alias"] == "inverter"

    def test_excluded_plant_is_dropped(self, monkeypatch):
        monkeypatch.setattr(da.exclusions, "is_plant_excluded", lambda name: name == "Gone")
        alarms, counts = da.select_alarms(
            entries(alert(7), station={"id": 1, "name": "Gone"}) + entries(alert(7)), PRODUCTS)
        assert len(alarms) == 1 and counts["excluded"] == 1

    def test_one_occurrence_keeps_one_key(self):
        a = da.to_gate_alarm(alert(7), STATION, HYBRID)
        assert gda.create_alarm_key(a) == f"61950873:2501215093:ERR5-2@{a['alertStartTime']}"
        assert a["plant"] == "Test Plant" and a["alias"] == "1-phase hybrid"

    def test_same_code_in_two_protocol_words_is_two_alarms(self):
        f13 = da.to_gate_alarm(alert(7, name="F13Grid_Mode_changed", code="12",
                                     protocol="ERR3"), STATION, HYBRID)
        f45 = da.to_gate_alarm(alert(7, name="F45AC_UV_OverVolt_Fault", code="12",
                                     protocol="ERR5"), STATION, HYBRID)
        assert gda.create_alarm_key(f13) != gda.create_alarm_key(f45)


class TestGate:
    def test_a_three_hour_cut_is_silent(self, gate_on):
        to_send, state = run_gate([alert(3)])
        assert to_send == []
        held = next(iter(state.values()))
        assert held["class"] == "GRID" and "less than 6 hours" in held["suppressed_reason"]

    def test_a_seven_hour_fault_mails(self, gate_on):
        assert len(run_gate([alert(7)])[0]) == 1

    def test_the_six_hour_line(self, gate_on):
        assert run_gate([alert(5.98)])[0] == []
        assert len(run_gate([alert(6.02)])[0]) == 1

    def test_the_start_time_is_read_as_utc_epoch(self, gate_on):
        """Epoch seconds carry no zone to misread; a plant-local reading would
        shift every alarm by 5.5 hours."""
        _, state = run_gate([alert(3)])
        held = next(iter(state.values()))
        assert held["started_at_utc"] == (NOW - timedelta(hours=3)).isoformat()

    def test_an_off_grid_battery_alarm_rides_the_same_clock(self, gate_on):
        low = dict(sn="2502000001", name="F56DC_VoltLow_Fault", code="7", protocol="ERR6")
        assert run_gate([alert(5, **low)])[0] == []
        assert len(run_gate([alert(7, **low)])[0]) == 1

    def test_grid_tie_overnight_alarm_is_never_mailed(self, gate_on):
        assert run_gate([alert(16, sn="2503000002")])[0] == []

    def test_a_missing_start_time_passes_rather_than_hides(self, gate_on):
        raw = alert(1)
        del raw["alertStartTime"]
        assert len(run_gate([raw])[0]) == 1

    def test_three_sends_four_hours_apart(self, gate_on):
        to_send, state = run_gate([alert(7)])
        gda.update_alarm_state(state, to_send)
        assert run_gate([alert(7)], state)[0] == []          # same hour: too soon
        key = to_send[0]["alarm_key"]
        state[key]["send_count"] = 3
        state[key]["last_sent"] = (datetime.now() - timedelta(hours=5)).isoformat()
        assert run_gate([alert(7)], state)[0] == []          # spent its three sends


class TestState:
    def test_cleared_alarms_are_forgotten(self, gate_on):
        _, state = run_gate([alert(3)])
        assert len(state) == 1
        _, state = run_gate([alert(3, status=0)], state)
        assert state == {}


class TestEmail:
    def test_names_the_plant_the_fault_and_the_first_check(self, gate_on):
        to_send, _ = run_gate([alert(7.5)], station={"id": 1, "name": "Umesh 8kv"})
        body = da.format_email(to_send, NOW)
        assert "Umesh 8kv - 1-phase hybrid (2501215093)" in body
        assert "AC_NoUtillity_Fault" in body and "ERR5 code 2" in body
        assert "warning, class GRID" in body
        assert "7.5 h" in body and "Send 1/3" in body
        assert gda.ALARM_TRIAGE_CONFIG["escalation_hint"]["GRID"] in body

    def test_start_time_is_plant_local(self, gate_on):
        to_send, _ = run_gate([alert(7)])
        start_local = (NOW - timedelta(hours=7)).astimezone(timezone(timedelta(minutes=330)))
        assert f"Open since {start_local:%Y-%m-%d %H:%M}" in da.format_email(to_send, NOW)

    def test_a_missing_start_time_still_mails(self, gate_on):
        raw = alert(1)
        del raw["alertStartTime"]
        to_send, _ = run_gate([raw])
        assert "Start time missing" in da.format_email(to_send, NOW)


class FakeClient:
    """Answers the three read paths from canned lists, paging like Deye does."""

    def __init__(self, stations=(), devices=None, alerts=None, page_size=None):
        self.stations, self.devices, self.alerts = list(stations), devices or {}, alerts or {}
        self.calls = []

    def post(self, path, body):
        self.calls.append((path, body))
        if path == "/station/list":
            items, key = self.stations, "stationList"
        elif path == "/station/device":
            if len(body["stationIds"]) > 10:
                raise dc.DeyeError("/station/device failed (HTTP 200): list too long")
            items = [d for s in body["stationIds"] for d in self.devices.get(s, [])]
            key = "deviceListItems"
        else:
            items, key = self.alerts.get(body["stationId"], []), "stationAlertItems"
        size, page = body["size"], body["page"]
        return {"success": True, "code": "1000000", "total": len(items),
                key: items[(page - 1) * size: page * size]}


class TestFetch:
    def test_devices_are_asked_ten_plants_at_a_time(self):
        ids = list(range(1, 36))
        client = FakeClient(devices={i: [{"deviceSn": f"S{i}", "productId": HYBRID}] for i in ids})
        products = da.fetch_products(client, ids)
        assert len(products) == 35 and products["S35"] == HYBRID
        assert [len(b["stationIds"]) for _, b in client.calls] == [10, 10, 10, 5]

    def test_alerts_page_through_a_seven_day_window_per_plant(self):
        many = [alert(1)] * 250
        client = FakeClient(alerts={1: many, 2: [alert(2)]})
        out = da.fetch_alarms(client, [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
                              NOW.timestamp())
        assert len(out) == 251 and out[-1][0]["name"] == "B"
        path, body = client.calls[0]
        assert path == "/station/alertList" and body["stationId"] == 1
        assert body["endTimestamp"] - body["startTimestamp"] == 7 * 86400
        assert len(client.calls) == 3          # two pages for plant 1, one for plant 2

    def test_stations_are_paged(self):
        client = FakeClient(stations=[{"id": i, "name": str(i)} for i in range(205)])
        assert len(da.fetch_stations(client)) == 205


class FakeTransport:
    """Records each request and answers from a queue of (status, body)."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, url, body, headers, timeout):
        self.requests.append((url, body, headers))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


TOKEN = (200, {"success": True, "code": "1000000", "accessToken": "tok-1", "expiresIn": 5184000})
OK = (200, {"success": True, "code": "1000000", "stationList": [], "total": 0})
CFG = {"app_id": "app", "app_secret": "s3cret-app", "password_sha256": "ab" * 32,
       "email": "ops@example.com", "company_id": "42", "data_center": "eu"}


def client(*replies):
    transport = FakeTransport(*replies)
    return dc.DeyeClient.from_config(CFG, transport=transport, sleep=lambda s: None), transport


class TestClient:
    def test_control_paths_are_refused_before_any_request(self):
        c, transport = client()
        for path in ("/order/sys/energyPattern/update", "/order/customControl",
                     "/order/battery/parameter/update", "/strategy/dynamicControl"):
            with pytest.raises(dc.DeyeError, match="read-only"):
                c.post(path, {})
        assert transport.requests == []

    def test_logs_in_once_then_sends_the_bearer_token(self):
        c, transport = client(TOKEN, OK, OK)
        c.post("/station/list", {})
        c.post("/station/list", {})
        login_url, login_body, _ = transport.requests[0]
        assert login_url.endswith("/account/token?appId=app")
        assert login_body["companyId"] == 42 and login_body["email"] == "ops@example.com"
        assert [r[2].get("Authorization") for r in transport.requests[1:]] == ["bearer tok-1"] * 2

    def test_a_refused_token_logs_in_again_once(self):
        refused = (200, {"success": False, "code": "2101019", "msg": "token invalid"})
        c, transport = client(TOKEN, refused, TOKEN, OK)
        assert c.post("/station/list", {})["total"] == 0
        assert len(transport.requests) == 4

    def test_a_rate_limit_is_retried_once(self):
        c, transport = client(TOKEN, (429, None), OK)
        assert c.post("/station/list", {})["total"] == 0

    def test_a_network_error_is_retried_once_then_raised(self):
        down = dc.DeyeError("network error: timed out")
        c, _ = client(TOKEN, down, down)
        with pytest.raises(dc.DeyeError, match="network"):
            c.post("/station/list", {})

    def test_a_plain_failure_is_not_retried(self):
        c, transport = client(TOKEN, (200, {"success": False, "code": "1", "msg": "list too long"}))
        with pytest.raises(dc.DeyeError, match="list too long"):
            c.post("/station/device", {"stationIds": list(range(20))})
        assert len(transport.requests) == 2

    def test_errors_never_carry_the_secrets(self):
        c, _ = client((200, {"success": False, "code": "2101006", "msg": "auth failed"}))
        with pytest.raises(dc.DeyeError) as e:
            c.post("/station/list", {})
        assert "s3cret-app" not in str(e.value) and "ab" * 32 not in str(e.value)

    def test_missing_config_is_a_clear_error(self):
        with pytest.raises(dc.DeyeError, match="required"):
            dc.DeyeClient.from_config({"app_id": "app"})


class TestMain:
    def test_a_failed_read_saves_no_state(self, monkeypatch, tmp_path):
        """A half-read fleet must not prune the plants it missed: their sent
        alarms would start again at Send 1/3."""
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"1:2:ERR5-2@1": {"send_count": 2}}))

        class Broken:
            def post(self, path, body):
                raise dc.DeyeError("/station/alertList failed (HTTP 500): busy")

        monkeypatch.setattr(da, "load_deye_config", lambda: CFG)
        monkeypatch.setattr(da.DeyeClient, "from_config", classmethod(lambda cls, cfg: Broken()))
        monkeypatch.setattr(sys, "argv", ["check_deye_alarms.py", "--state-file", str(state_file)])
        assert da.main() == 1
        assert json.loads(state_file.read_text()) == {"1:2:ERR5-2@1": {"send_count": 2}}

    def test_no_stations_is_a_failure_not_an_all_clear(self, monkeypatch, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("{}")
        monkeypatch.setattr(da, "load_deye_config", lambda: CFG)
        monkeypatch.setattr(da.DeyeClient, "from_config",
                            classmethod(lambda cls, cfg: FakeClient(stations=[])))
        monkeypatch.setattr(sys, "argv", ["check_deye_alarms.py", "--state-file", str(state_file)])
        assert da.main() == 1


class TestWiring:
    def test_workflow_runs_hourly_and_keeps_its_state(self):
        text = (ROOT / ".github" / "workflows" / "trigger-deye-alarms.yml").read_text(encoding="utf-8")
        assert "check_deye_alarms.py" in text
        assert 'cron: "27 * * * *"' in text
        assert "state/deye_alarms_state.json" in text
        assert "push_data_commit.sh --resolve-conflicts" in text

    def test_email_channel_has_a_flag(self):
        flags = json.loads((SRC / "main" / "java" / "org" / "ktronics" / "config" / "features.json")
                           .read_text(encoding="utf-8"))
        assert flags["emails"]["deye_alarms_admin"] is True
        assert da.EMAIL_CHANNEL == "emails.deye_alarms_admin"

    def test_the_gate_knows_the_vendor(self):
        """Without its own name a Deye alarm would be read under ShineMonitor's
        code map, where Deye's small bit numbers could collide."""
        import inspect
        assert "'deye': 'Deye'" in inspect.getsource(gda.triage_alarms)
