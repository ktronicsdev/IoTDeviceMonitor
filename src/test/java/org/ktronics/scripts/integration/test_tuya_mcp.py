#!/usr/bin/env python3
"""
Tuya Cloud MCP server - unit tests.

No network: checks the request signing against Tuya's published worked
examples, value scaling and 3-phase decoding, token refresh, and that local
keys and the control tool stay out of reach by default.
"""

import base64
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[6]
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

# tuya_mcp imports `requests` and the `mcp` server package. CI installs only
# `pytest pyyaml`, because this is a workstation tool - it talks to the user's
# own home devices and nothing in the pipeline runs it.
#
# importorskip rather than a bare import: a module-level ImportError in ANY
# test file aborts collection for the WHOLE suite, not just that file. On
# 2026-10-21 this one took all 661 tests down ("Interrupted: 1 error during
# collection") and every scheduled ShineMonitor run went red with nothing
# actually run. A missing optional dependency must skip, never interrupt.
pytest.importorskip("requests", reason="workstation-only dependency",
                    exc_type=ImportError)
pytest.importorskip("mcp.server.mcpserver", reason="workstation-only dependency",
                    exc_type=ImportError)

import tuya_mcp as tm  # noqa: E402

# Worked example from developer.tuya.com/en/docs/iot/new-singnature
DOC_ID = "1KAD46OrT9HafiKdsXeg"
DOC_SECRET = "4OHBOnWOqaEC1mWXOpVL3yV50s0qGSRC"
DOC_T = "1588925778000"
DOC_NONCE = "5138cc3a9033d69856923fd07b491173"
DOC_HEADERS = {"area_id": "29a33e8796834b1efa6", "call_id": "8afdb70ab2ed11eb85290242ac130003"}


class TestSigning:
    def test_token_request_matches_tuya_example(self):
        sts = tm.string_to_sign("GET", "/v1.0/token?grant_type=1",
                                signature_headers=DOC_HEADERS)
        assert tm.sign(DOC_ID, DOC_SECRET, DOC_T, sts, nonce=DOC_NONCE) == (
            "9E48A3E93B302EEECC803C7241985D0A34EB944F40FB573C7B5C2A82158AF13E")

    def test_business_request_matches_tuya_example(self):
        url = tm.build_url("/v2.0/apps/schema/users", {"page_size": 50, "page_no": 1})
        sts = tm.string_to_sign("GET", url, signature_headers=DOC_HEADERS)
        assert tm.sign(DOC_ID, DOC_SECRET, DOC_T, sts,
                       access_token="3f4eda2bdec17232f67c0b188af3eec1", nonce=DOC_NONCE) == (
            "AE4481C692AA80B25F3A7E12C3A5FD9BBF6251539DD78E565A1A72A508A88784")

    def test_build_url_sorts_and_drops_empty(self):
        assert tm.build_url("/p", {"size": 50, "last_row_key": "", "a": 1}) == "/p?a=1&size=50"
        assert tm.build_url("/p", {}) == "/p"

    def test_no_signature_headers_leaves_blank_line(self):
        sts = tm.string_to_sign("GET", "/v1.0/devices/x")
        assert sts.split("\n") == ["GET", tm.hashlib.sha256(b"").hexdigest(), "",
                                   "/v1.0/devices/x"]


class TestDecoding:
    INT_KW = {"code": "cur_power", "type": "Integer", "values": '{"unit":"kW","scale":3}'}

    def test_scale_integer(self):
        assert tm.scale_value(704, self.INT_KW) == 0.704

    def test_scale_leaves_booleans_and_unknown_codes(self):
        assert tm.scale_value(True, self.INT_KW) is True
        assert tm.scale_value(205, None) == 205

    def test_decode_phase_matches_app_reading(self):
        # Phase A as the app showed it on 2026-10-20: 224.6 V, 3.828 A, 0.704 kW.
        raw = (2246).to_bytes(2, "big") + (3828).to_bytes(3, "big") + (704).to_bytes(3, "big")
        d = tm.decode_phase(base64.b64encode(raw).decode())
        assert (d["voltage_v"], d["current_a"], d["power_kw"]) == (224.6, 3.828, 0.704)

    def test_decode_phase_rejects_short_or_bad_input(self):
        assert tm.decode_phase(base64.b64encode(b"\x01\x02").decode()) is None
        assert tm.decode_phase("not base64!") is None
        assert tm.decode_phase('{"voltage": 224}') is None

    def test_negative_phase_power_from_log_bytes(self):
        # PC321-Z-TY log of 2026-10-20 14:01: phase B power bytes 99 97 BA = -479 W.
        d = tm.decode_phase("CMIADYmZl7o=")
        assert (d["voltage_v"], d["current_a"], d["power_kw"]) == (224.2, 3.465, -0.479)

    def test_logged_phases_sum_to_power_total(self):
        # Same sample: A, B, C as logged; the clamp's power_total was 0.193 kW.
        phases = ["CL0ADqkAAo4=", "CMIADYmZl7o=", "CLUADCMAABE="]
        total = sum(tm.decode_phase(p)["power_kw"] for p in phases)
        assert total == pytest.approx(0.193, abs=0.002)

    def test_negative_phase_power_from_cloud_json(self):
        # The cloud's own JSON passes the same bytes on as 10065.843 kW.
        d = tm.decode_phase('{"electricCurrent":3.48,"power":10065.843,"voltage":224.1}')
        assert (d["voltage_v"], d["current_a"], d["power_kw"]) == (224.1, 3.48, -0.486)

    def test_decode_status_adds_units_and_phase(self):
        rows = tm.decode_status(
            [{"code": "cur_power", "value": 1500},
             {"code": "phase_a", "value": '{"electricCurrent":3.671,"power":0.635,"voltage":223.2}'}],
            {"cur_power": self.INT_KW})
        assert rows[0] == {"code": "cur_power", "value": 1.5, "unit": "kW"}
        assert rows[1]["value"] == {"voltage_v": 223.2, "current_a": 3.671, "power_kw": 0.635}

    def test_fmt_time_accepts_seconds_and_millis(self):
        assert tm.fmt_time(1760000000) == tm.fmt_time(1760000000000)
        assert tm.fmt_time(None) is None


class TestEnergyBuckets:
    T = __import__("datetime").datetime

    def test_cumulative_counter_rise_lands_in_later_bucket(self):
        readings = [(self.T(2026, 10, 20, 9, 58), 100.0), (self.T(2026, 10, 20, 10, 1), 100.5),
                    (self.T(2026, 10, 20, 10, 59), 101.0), (self.T(2026, 10, 20, 11, 2), 101.25)]
        assert tm.bucket_energy(readings, "hours", cumulative=True) == {
            "2026-10-20 10:00": 1.0, "2026-10-20 11:00": 0.25}

    def test_counter_reset_is_skipped(self):
        readings = [(self.T(2026, 10, 20, 1), 50.0), (self.T(2026, 10, 20, 2), 0.5),
                    (self.T(2026, 10, 20, 3), 1.5)]
        assert tm.bucket_energy(readings, "days", cumulative=True) == {"2026-10-20": 1.0}

    def test_increments_are_summed(self):
        readings = [(self.T(2026, 10, 19, 23), 0.2), (self.T(2026, 10, 20, 0, 30), 0.1),
                    (self.T(2026, 10, 20, 8), 0.3)]
        assert tm.bucket_energy(readings, "days", cumulative=False) == {
            "2026-10-19": 0.2, "2026-10-20": 0.4}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    """Replays queued responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append((method, url, headers))
        return FakeResponse(self.responses.pop(0))


def token_ok(value):
    return {"success": True, "result": {"access_token": value, "expire_time": 7200}}


class TestClient:
    def test_token_is_cached(self):
        s = FakeSession([token_ok("A"), {"success": True, "result": 1},
                         {"success": True, "result": 2}])
        c = tm.TuyaClient("id", "secret", "https://example.test", session=s)
        c.request("GET", "/x")
        c.request("GET", "/y")
        assert [u for _, u, _ in s.calls] == ["https://example.test/v1.0/token?grant_type=1",
                                              "https://example.test/x",
                                              "https://example.test/y"]

    def test_invalid_token_refreshes_and_retries_once(self):
        s = FakeSession([token_ok("OLD"), {"success": False, "code": 1010, "msg": "token invalid"},
                         token_ok("NEW"), {"success": True, "result": "ok"}])
        c = tm.TuyaClient("id", "secret", "https://example.test", session=s)
        assert c.request("GET", "/x") == {"success": True, "result": "ok"}
        assert s.calls[-1][2]["access_token"] == "NEW"

    def test_token_failure_raises(self):
        s = FakeSession([{"success": False, "code": 1004, "msg": "sign invalid"}])
        c = tm.TuyaClient("id", "secret", "https://example.test", session=s)
        with pytest.raises(tm.ToolError, match="1004"):
            c.request("GET", "/x")


class TestTools:
    def test_list_devices_pages_and_hides_local_key(self, monkeypatch):
        pages = [
            {"devices": [{"id": "d1", "name": "Power Clamp", "online": True,
                          "local_key": "SECRET", "ip": "1.2.3.4", "update_time": 1760000000}],
             "has_more": True, "last_row_key": "k1"},
            {"devices": [{"id": "d2", "name": "Wood Boiler TRV", "online": False,
                          "local_key": "SECRET2"}], "has_more": False},
        ]
        monkeypatch.setattr(tm, "_call", lambda *a, **k: pages.pop(0))
        devices = tm.list_devices()
        assert [d["id"] for d in devices] == ["d1", "d2"]
        assert all("local_key" not in d and "ip" not in d for d in devices)

    def test_list_devices_filters(self, monkeypatch):
        page = {"devices": [{"id": "d1", "name": "Power Clamp", "online": True},
                            {"id": "d2", "name": "Wood Boiler TRV", "online": False}]}
        monkeypatch.setattr(tm, "_call", lambda *a, **k: page)
        assert [d["id"] for d in tm.list_devices(online_only=True)] == ["d1"]
        assert [d["id"] for d in tm.list_devices(name_contains="trv")] == ["d2"]

    def test_missing_credentials_explain_setup(self, monkeypatch):
        monkeypatch.setattr(tm, "_client", None)
        monkeypatch.setattr(tm, "_load_cfg", lambda: {"api_base": tm.REGIONS["eu"]})
        with pytest.raises(tm.ToolError, match="iot.tuya.com"):
            tm.client()

    def test_control_tool_off_by_default(self):
        assert not hasattr(tm, "send_command")
