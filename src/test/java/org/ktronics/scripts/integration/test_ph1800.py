#!/usr/bin/env python3
"""
check_ph1800.py — generic ShineMonitor pass-through tests.

Verifies the multi-tenant "PH1800" path: ph1800-flag account selection, the
credential-derived data filename, raw live/history pass-through (no computed/estimate
fields), and per-plant page-shell generation. No network (pure logic on mocked shapes).
The PH1000 tests are unaffected — this is an additive, separate module.
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
        assert ck == "ck"
        assert [a[0] for a in accs] == ["A"]            # not B (no flag), not C (ph1000)

    def test_customer_overrides_flag(self, tmp_path):
        _ck, accs = b.load_ph1800_accounts(self._creds(tmp_path), "B")
        assert [a[0] for a in accs] == ["B"]            # --customer includes an unflagged account


class TestFilename:
    def test_deterministic_credential_hash(self):
        assert b.account_file("user", "pass") == \
            hashlib.sha256(b"user:pass").hexdigest() + ".json"

    def test_distinct_per_credentials(self):
        assert b.account_file("u", "p1") != b.account_file("u", "p2")


class TestRawPassthrough:
    def test_live_passes_through_every_field(self, monkeypatch):
        fake = {"err": 0, "dat": {
            "ts": {"par": "Timestamp", "val": "2026-06-30 10:00:00"},
            "bv": {"par": "Battery Voltage", "val": "26.6", "unit": "V"},
            "pl": {"par": "PLoad", "val": "168", "unit": "W"},
            "ws": {"par": "work state", "val": "Grid-Tie"}}}
        monkeypatch.setattr(b, "api_call", lambda *a, **k: fake)
        fields, last, _ = b.fetch_live_raw(None, None, DEV)
        assert last == "2026-06-30 10:00:00"
        assert "timestamp" not in fields                # timestamp is lifted out, not a field
        assert fields["battery-voltage"]["value"] == "26.6"
        assert fields["pload"]["label"] == "PLoad" and fields["pload"]["unit"] == "W"
        assert fields["work-state"]["value"] == "Grid-Tie"

    def test_no_estimate_or_computed_keys(self, monkeypatch):
        fake = {"err": 0, "dat": {"x": {"par": "PInverter", "val": "0", "unit": "W"}}}
        monkeypatch.setattr(b, "api_call", lambda *a, **k: fake)
        fields, _l, _r = b.fetch_live_raw(None, None, DEV)
        assert list(fields) == ["pinverter"]
        assert all("est" not in k and "soc" not in k for k in fields)  # nothing derived

    def test_history_passes_through_columns(self, monkeypatch):
        page = {"err": 0, "dat": {
            "title": [{"title": "Timestamp"}, {"title": "Battery Voltage"}, {"title": "PLoad"}],
            "row": [{"field": ["2026-06-30 10:00:00", "26.6", "168"]}]}}
        n = {"i": 0}

        def fake(token, secret, action, params=""):
            if "OneDay" in action:
                n["i"] += 1
                return page if n["i"] == 1 else {"err": 0, "dat": {"title": [], "row": []}}
            return {"err": 0, "dat": {}}
        monkeypatch.setattr(b, "api_call", fake)
        rows, fields = b.fetch_history_raw(None, None, DEV, days=1)
        assert rows and rows[0]["battery-voltage"] == 26.6 and rows[0]["pload"] == 168.0
        assert {f["key"] for f in fields} == {"battery-voltage", "pload"}   # timestamp excluded


class TestPageShell:
    def test_creates_and_embeds_username(self, tmp_path):
        assert b.ensure_page(str(tmp_path), "Gayan-IMH", "pcgayan.imbulgoda") is True
        html = (tmp_path / "Gayan-IMH" / "index.html").read_text(encoding="utf-8")
        assert '"pcgayan.imbulgoda"' in html and '"Gayan-IMH"' in html
        assert "../app.js" in html and "../app.css" in html

    def test_does_not_overwrite(self, tmp_path):
        b.ensure_page(str(tmp_path), "X", "u")
        assert b.ensure_page(str(tmp_path), "X", "u") is False
