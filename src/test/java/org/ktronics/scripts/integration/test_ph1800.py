#!/usr/bin/env python3
"""
check_ph1800.py — generic ShineMonitor tests (multi-plant, PH1000-schema mapping).

Verifies ph1800-flag account selection, the credential-derived data filename, the field
mapping that lets the PH1000 dashboard layout render real cloud data (PLoad/PInverter used
DIRECTLY, no estimation), and per-plant page-shell generation. No network. The PH1000 tests
are unaffected — this is an additive, separate module.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest  # noqa: F401

SRC = Path(__file__).resolve().parents[6]
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_ph1800 as b  # noqa: E402

DEV = {"pn": "p", "devcode": "1", "devaddr": "1", "sn": "s"}


class TestAccountSelection:
    def _creds(self, tmp_path):
        creds = {"company_key": "ck", "accounts": [
            {"label": "A", "username": "ua", "password": "pa", "ph1800": True},
            {"label": "B", "username": "ub", "password": "pb"},
            {"label": "C", "username": "uc", "password": "pc", "ph1000": True}]}
        f = tmp_path / "c.json"
        f.write_text(json.dumps(creds), encoding="utf-8")
        return str(f)

    def test_only_ph1800_flagged(self, tmp_path):
        ck, accs = b.load_ph1800_accounts(self._creds(tmp_path))
        assert ck == "ck" and [a[0] for a in accs] == ["A"]

    def test_customer_overrides_flag(self, tmp_path):
        _ck, accs = b.load_ph1800_accounts(self._creds(tmp_path), "B")
        assert [a[0] for a in accs] == ["B"]


class TestFilename:
    def test_deterministic_credential_hash(self):
        assert b.account_file("user", "pass") == hashlib.sha256(b"user:pass").hexdigest() + ".json"

    def test_distinct_per_credentials(self):
        assert b.account_file("u", "p1") != b.account_file("u", "p2")


class TestFieldMapping:
    # On this inverter: Charger Power = PV power; PInverter = battery power; Batt Current = battery A.
    def test_live_maps_to_dashboard_keys(self, monkeypatch):
        fake = {"err": 0, "dat": {
            "ts": {"par": "Timestamp", "val": "2026-06-30 10:00:00"},
            "bv": {"par": "Battery Voltage", "val": "26.6", "unit": "V"},
            "bc": {"par": "Batt Current", "val": "-3", "unit": "A"},
            "cp": {"par": "Charger Power", "val": "88", "unit": "W"},
            "pi": {"par": "PInverter", "val": "-80", "unit": "W"},
            "pl": {"par": "PLoad", "val": "168", "unit": "W"},
            "ws": {"par": "work state", "val": "Grid-Tie"}}}
        monkeypatch.setattr(b, "api_call", lambda *a, **k: fake)
        f, last, _ = b.fetch_live(None, None, DEV)
        assert last == "2026-06-30 10:00:00"
        assert f["battery_v"]["value"] == "26.6"
        assert f["battery_a"]["value"] == "-3"               # from Batt Current
        assert f["pinverter_w"]["value"] == "88"             # Charger Power = PV power
        assert f["pv_power_w_est"]["value"] == "88"
        assert f["battery_w"]["value"] == "-80"              # PInverter = battery power
        assert f["load_power_w_est"]["value"] == "168"       # REAL PLoad, used directly
        assert f["work_state"]["value"] == "Grid-Tie"

    def test_charger_current_is_not_battery(self, monkeypatch):
        # Charger Current is the MPPT/PV current, not the battery — must NOT become battery_a.
        fake = {"err": 0, "dat": {"cc": {"par": "Charger Current", "val": "0.5", "unit": "A"}}}
        monkeypatch.setattr(b, "api_call", lambda *a, **k: fake)
        f, _l, _r = b.fetch_live(None, None, DEV)
        assert "battery_a" not in f

    def test_history_maps_and_flags_charge_state(self, monkeypatch):
        page = {"err": 0, "dat": {
            "title": [{"title": "Timestamp"}, {"title": "Battery Voltage"}, {"title": "Batt Current"},
                      {"title": "Charger Power"}, {"title": "PInverter"}, {"title": "PLoad"}],
            "row": [{"field": ["2026-06-30 10:00:00", "26.6", "-3", "88", "-80", "168"]}]}}
        n = {"i": 0}

        def fake(t, s, a, p=""):
            if "OneDay" in a:
                n["i"] += 1
                return page if n["i"] == 1 else {"err": 0, "dat": {"title": [], "row": []}}
            return {"err": 0, "dat": {}}
        monkeypatch.setattr(b, "api_call", fake)
        rows = b.fetch_history(None, None, DEV, days=1)
        r = rows[0]
        assert r["battery_v"] == 26.6 and r["battery_a"] == -3.0 and r["load_power_w_est"] == 168.0
        assert r["pinverter_w"] == 88.0 and r["pv_power_w_est"] == 88.0      # Charger Power = PV
        assert r["battery_w"] == -80.0 and r["charge_state"] == "charging"   # PInverter, <0 = charging


class TestPageShell:
    def test_copies_template(self, tmp_path):
        (tmp_path / "_app.html").write_text("<html>DASH</html>", encoding="utf-8")
        assert b.ensure_page(str(tmp_path), "Gayan-IMH") is True
        assert (tmp_path / "Gayan-IMH" / "index.html").read_text(encoding="utf-8") == "<html>DASH</html>"

    def test_no_template_no_create(self, tmp_path):
        assert b.ensure_page(str(tmp_path), "X") is False   # no _app.html -> nothing created

    def test_does_not_overwrite(self, tmp_path):
        (tmp_path / "_app.html").write_text("X", encoding="utf-8")
        b.ensure_page(str(tmp_path), "Y")
        assert b.ensure_page(str(tmp_path), "Y") is False
