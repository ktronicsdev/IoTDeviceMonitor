#!/usr/bin/env python3
"""
SolisCloud inverter ON/OFF watchdog — integration tests.

No network: exercises the request signing, detection logic (authoritative
control-read vs. daylight heuristic), freshness gating, and the act/notify
decision against captured SolisCloud response shapes.
"""

import base64
import hashlib
import hmac
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[6]
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import solis_common as sc  # noqa: E402
import check_solis_switch as w  # noqa: E402


# --------------------------------------------------------------------------- #
# Signing
# --------------------------------------------------------------------------- #
class TestSigning:
    def test_md5_base64_matches_manual(self):
        body = b'{"id":"123"}'
        expected = base64.b64encode(hashlib.md5(body).digest()).decode()
        assert sc._md5_base64(body) == expected

    def test_hmac_sha1_base64_matches_manual(self):
        secret, msg = "topsecret", "POST\nabc\napplication/json\nDATE\n/v1/api/x"
        expected = base64.b64encode(
            hmac.new(secret.encode(), msg.encode(), hashlib.sha1).digest()
        ).decode()
        assert sc._hmac_sha1_base64(secret, msg) == expected

    def test_gmt_date_format_is_rfc1123_english(self):
        d = sc._gmt_date()
        assert d.endswith(" GMT")
        # weekday + comma + space + 2-digit day
        assert d[3] == "," and d[4] == " "
        assert any(m in d for m in
                   ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])

    def test_post_builds_signed_request(self, monkeypatch):
        captured = {}

        class FakeResp:
            def __init__(self): self._b = b'{"success":true,"code":"0","data":{}}'
            def read(self): return self._b
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            captured["headers"] = dict(req.header_items())
            captured["url"] = req.full_url
            captured["body"] = req.data
            return FakeResp()

        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)
        client = sc.SolisClient("KEYID", "KEYSECRET")
        resp = client.inverter_detail("123", "SN1")

        assert sc.is_success(resp)
        # Authorization header shape: "API <keyid>:<sign>"
        auth = captured["headers"].get("Authorization")
        assert auth.startswith("API KEYID:")
        assert "Content-md5" in captured["headers"] or "Content-MD5" in captured["headers"]
        assert captured["url"].endswith("/v1/api/inverterDetail")

    def test_client_requires_credentials(self):
        with pytest.raises(ValueError):
            sc.SolisClient("", "")


class TestIsSuccess:
    def test_success_true(self):
        assert sc.is_success({"success": True})

    def test_code_zero_string(self):
        assert sc.is_success({"code": "0"})

    def test_failure(self):
        assert not sc.is_success({"success": False, "code": "1"})
        assert not sc.is_success("nope")


# --------------------------------------------------------------------------- #
# Detail parsing + freshness
# --------------------------------------------------------------------------- #
def _detail_resp(pac, state=1, ts=None):
    if ts is None:
        ts = int(w.now_utc().timestamp() * 1000)
    return {"success": True, "code": "0",
            "data": {"pac": pac, "state": state, "dataTimestamp": str(ts), "pacStr": "kW"}}


class TestParseAndFreshness:
    def test_parse_detail_numbers(self):
        d = w.parse_detail(_detail_resp(3.2))
        assert d["pac"] == 3.2 and d["state"] == 1

    def test_parse_detail_handles_bad_pac(self):
        d = w.parse_detail({"data": {"pac": "n/a"}})
        assert d["pac"] is None

    def test_fresh_recent_timestamp(self):
        assert w.data_is_fresh(w.parse_detail(_detail_resp(0)))

    def test_stale_old_timestamp(self):
        old = int((w.now_utc() - timedelta(hours=5)).timestamp() * 1000)
        assert not w.data_is_fresh(w.parse_detail(_detail_resp(0, ts=old)))

    def test_missing_timestamp_not_fresh(self):
        assert not w.data_is_fresh({"data_timestamp": None})


# --------------------------------------------------------------------------- #
# Daylight gate
# --------------------------------------------------------------------------- #
class TestDaylight:
    def test_core_daylight_true_at_noon(self, monkeypatch):
        cfg = {"tz_offset_minutes": 330, "core_daylight_start_hour": 9,
               "core_daylight_end_hour": 15}
        # Force local_now to noon
        monkeypatch.setattr(w, "local_now", lambda c: datetime(2026, 6, 29, 12, 0))
        assert w.in_core_daylight(cfg)

    def test_core_daylight_false_at_night(self, monkeypatch):
        cfg = {"core_daylight_start_hour": 9, "core_daylight_end_hour": 15}
        monkeypatch.setattr(w, "local_now", lambda c: datetime(2026, 6, 29, 22, 0))
        assert not w.in_core_daylight(cfg)


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
class _FakeClient:
    """Stub SolisClient for detect_off control-read path."""
    def __init__(self, atread=None):
        self._atread = atread or {"success": False}
    def at_read(self, inverter_id, cid):
        return self._atread


INV = {"inverter_id": "123", "sn": "SN1", "station_id": "999", "label": "Test"}


class TestDetectOff:
    def test_authoritative_off(self):
        cfg = {"onoff_cid": 5001, "off_value": "222"}
        client = _FakeClient({"success": True, "data": {"value": "222"}})
        is_off, reason, mode = w.detect_off(client, cfg, INV, w.parse_detail(_detail_resp(0)))
        assert is_off and mode == "control"

    def test_authoritative_on(self):
        cfg = {"onoff_cid": 5001, "off_value": "222"}
        client = _FakeClient({"success": True, "data": {"value": "190"}})
        is_off, reason, mode = w.detect_off(client, cfg, INV, w.parse_detail(_detail_resp(0)))
        assert not is_off and mode == "control"

    def test_heuristic_off_zero_pac_in_daylight(self, monkeypatch):
        monkeypatch.setattr(w, "in_core_daylight", lambda cfg: True)
        is_off, reason, mode = w.detect_off(
            _FakeClient(), {}, INV, w.parse_detail(_detail_resp(0)))
        assert is_off and mode == "heuristic"

    def test_heuristic_on_when_producing(self, monkeypatch):
        monkeypatch.setattr(w, "in_core_daylight", lambda cfg: True)
        is_off, reason, mode = w.detect_off(
            _FakeClient(), {}, INV, w.parse_detail(_detail_resp(2.5)))
        assert not is_off and mode == "heuristic"

    def test_heuristic_skipped_at_night(self, monkeypatch):
        monkeypatch.setattr(w, "in_core_daylight", lambda cfg: False)
        is_off, reason, mode = w.detect_off(
            _FakeClient(), {}, INV, w.parse_detail(_detail_resp(0)))
        assert not is_off and mode == "unknown"

    def test_heuristic_skipped_when_stale(self, monkeypatch):
        monkeypatch.setattr(w, "in_core_daylight", lambda cfg: True)
        old = int((w.now_utc() - timedelta(hours=6)).timestamp() * 1000)
        is_off, reason, mode = w.detect_off(
            _FakeClient(), {}, INV, w.parse_detail(_detail_resp(0, ts=old)))
        assert not is_off and mode == "unknown"


# --------------------------------------------------------------------------- #
# Enable / control
# --------------------------------------------------------------------------- #
class TestTryEnable:
    def test_no_config_means_manual(self):
        attempted, ok, msg = w.try_enable(_FakeClient(), {}, INV, dry_run=False)
        assert not attempted and not ok and "manual" in msg.lower()

    def test_dry_run_does_not_send(self):
        cfg = {"onoff_cid": 5001, "on_value": "190"}
        attempted, ok, msg = w.try_enable(_FakeClient(), cfg, INV, dry_run=True)
        assert attempted and not ok and "dry-run" in msg.lower()

    def test_control_success(self):
        cfg = {"onoff_cid": 5001, "on_value": "190"}

        class C:
            def control(self, inverter_id, cid, value):
                return {"success": True, "code": "0"}
        attempted, ok, msg = w.try_enable(C(), cfg, INV, dry_run=False)
        assert attempted and ok

    def test_control_failure_reports_permission_hint(self):
        cfg = {"onoff_cid": 5001, "on_value": "190"}

        class C:
            def control(self, inverter_id, cid, value):
                return {"success": False, "msg": "no permission"}
        attempted, ok, msg = w.try_enable(C(), cfg, INV, dry_run=False)
        assert attempted and not ok and "Control API" in msg


# --------------------------------------------------------------------------- #
# Email body content
# --------------------------------------------------------------------------- #
class TestEmailBodies:
    def test_enabled_body_has_link_and_cause(self):
        body = w._enabled_body("Test", INV, w.parse_detail(_detail_resp(0)),
                               "reason", "action")
        assert "soliscloud.com" in body and "Uac-Unstable" in body

    def test_manual_body_flags_action_needed(self):
        body = w._manual_body("Test", INV, w.parse_detail(_detail_resp(0)),
                              "reason", "why", dry_run=False)
        assert "ACTION NEEDED" in body and "soliscloud.com" in body


# --------------------------------------------------------------------------- #
# handle_inverter end-to-end (mocked client + email)
# --------------------------------------------------------------------------- #
class TestHandleInverter:
    def test_producing_inverter_no_email(self, monkeypatch):
        monkeypatch.setattr(w, "in_core_daylight", lambda cfg: True)
        sent = []
        monkeypatch.setattr(w, "send_admin_email", lambda *a, **k: sent.append(a))

        class C:
            def inverter_detail(self, *a): return _detail_resp(3.0)
        state = {}
        res = w.handle_inverter(C(), {}, INV, state, dry_run=False)
        assert "OK" in res and not sent
        assert state["123"]["off"] is False

    def test_off_inverter_notifies_when_no_autocontrol(self, monkeypatch):
        monkeypatch.setattr(w, "in_core_daylight", lambda cfg: True)
        sent = []
        monkeypatch.setattr(w, "send_admin_email", lambda cfg, subj, body: sent.append(subj))

        class C:
            def inverter_detail(self, *a): return _detail_resp(0)
        state = {}
        res = w.handle_inverter(C(), {}, INV, state, dry_run=False)
        assert "manual action" in res and len(sent) == 1
        assert "manual switch-on" in sent[0]

    def test_off_inverter_autoenables_when_configured(self, monkeypatch):
        monkeypatch.setattr(w, "in_core_daylight", lambda cfg: True)
        sent = []
        monkeypatch.setattr(w, "send_admin_email", lambda cfg, subj, body: sent.append(subj))
        cfg = {"onoff_cid": 5001, "on_value": "190", "off_value": "222"}

        class C:
            def inverter_detail(self, *a): return _detail_resp(0)
            def at_read(self, *a): return {"success": True, "data": {"value": "222"}}
            def control(self, *a): return {"success": True, "code": "0"}
        state = {}
        res = w.handle_inverter(C(), cfg, INV, state, dry_run=False)
        assert "auto-enabled" in res and len(sent) == 1
        assert "auto re-enabled" in sent[0]
        assert "last_enabled_at" in state["123"]

    def test_manual_email_throttled(self, monkeypatch):
        monkeypatch.setattr(w, "in_core_daylight", lambda cfg: True)
        sent = []
        monkeypatch.setattr(w, "send_admin_email", lambda cfg, subj, body: sent.append(subj))

        class C:
            def inverter_detail(self, *a): return _detail_resp(0)
        # recent manual email -> should throttle
        state = {"123": {"last_manual_email_at": w.now_utc().isoformat()}}
        res = w.handle_inverter(C(), {}, INV, state, dry_run=False)
        assert "throttled" in res and not sent
