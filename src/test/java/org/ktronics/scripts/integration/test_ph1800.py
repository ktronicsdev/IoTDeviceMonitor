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
    # On this inverter: Charger Power = PV power; battery power = Battery V x Batt Current.
    def test_live_maps_to_dashboard_keys(self, monkeypatch):
        fake = {"err": 0, "dat": {
            "ts": {"par": "Timestamp", "val": "2026-06-30 10:00:00"},
            "bv": {"par": "Battery Voltage", "val": "26.6", "unit": "V"},
            "bc": {"par": "Batt Current", "val": "-3", "unit": "A"},
            "cp": {"par": "Charger Power", "val": "88", "unit": "W"},
            "pl": {"par": "PLoad", "val": "168", "unit": "W"},
            "ws": {"par": "work state", "val": "Grid-Tie"}}}
        monkeypatch.setattr(b, "api_call", lambda *a, **k: fake)
        f, last, _ = b.fetch_live(None, None, DEV)
        assert last == "2026-06-30 10:00:00"
        assert f["battery_v"]["value"] == "26.6"
        assert f["battery_a"]["value"] == "-3"               # from Batt Current
        assert f["pinverter_w"]["value"] == "88"             # Charger Power = PV power
        assert f["pv_power_w_est"]["value"] == "88"
        assert f["battery_w"]["value"] == round(26.6 * -3)   # Battery V x Batt Current = -80
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
                      {"title": "Charger Power"}, {"title": "PLoad"}],
            "row": [{"field": ["2026-06-30 10:00:00", "26.6", "-3", "88", "168"]}]}}
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
        assert r["battery_w"] == round(26.6 * -3) and r["charge_state"] == "charging"  # V*I < 0


class TestPageShell:
    def test_copies_template(self, tmp_path):
        (tmp_path / "_app.html").write_text("<html>DASH</html>", encoding="utf-8")
        assert b.ensure_page(str(tmp_path), "Gayan-IMH") is True
        assert (tmp_path / "Gayan-IMH" / "index.html").read_text(encoding="utf-8") == "<html>DASH</html>"

    def test_no_template_no_create(self, tmp_path):
        assert b.ensure_page(str(tmp_path), "X") is False   # no _app.html -> nothing created

    def test_no_change_when_up_to_date(self, tmp_path):
        (tmp_path / "_app.html").write_text("X", encoding="utf-8")
        b.ensure_page(str(tmp_path), "Y")
        assert b.ensure_page(str(tmp_path), "Y") is False       # identical -> no re-stamp

    def test_restamps_when_template_changes(self, tmp_path):
        (tmp_path / "_app.html").write_text("v1", encoding="utf-8")
        b.ensure_page(str(tmp_path), "Y")
        (tmp_path / "_app.html").write_text("v2 with variant", encoding="utf-8")
        assert b.ensure_page(str(tmp_path), "Y") is True        # differs -> re-stamped
        assert (tmp_path / "Y" / "index.html").read_text(encoding="utf-8") == "v2 with variant"


class TestVariant:
    def test_default_variant_is_pro(self, tmp_path):
        creds = {"company_key": "ck", "accounts": [
            {"label": "A", "username": "ua", "password": "pa", "ph1800": True}]}
        f = tmp_path / "c.json"; f.write_text(json.dumps(creds), encoding="utf-8")
        _ck, accs = b.load_ph1800_accounts(str(f))
        assert accs[0][3]["variant"] == "pro"                   # caps dict

    def test_vhm_variant_read_and_lowercased(self, tmp_path):
        creds = {"company_key": "ck", "accounts": [
            {"label": "A", "username": "ua", "password": "pa", "ph1800": True, "variant": "VHM"}]}
        f = tmp_path / "c.json"; f.write_text(json.dumps(creds), encoding="utf-8")
        _ck, accs = b.load_ph1800_accounts(str(f))
        assert accs[0][3]["variant"] == "vhm"

    def test_plant_block_carries_variant(self):
        blk = b.build_plant_block({}, {"alias": "x"}, [], caps={"variant": "vhm", "pv_kw": 0.5})
        assert blk["variant"] == "vhm"

    def test_plant_block_defaults_variant_pro(self):
        blk = b.build_plant_block({}, {"alias": "x"}, [], caps={})
        assert blk["variant"] == "pro"


class TestHistorySplit:
    # The main file keeps only recent days (small, polled); the full window goes to .hist.json,
    # which the dashboard background-loads to extend History.
    S = [{"timestamp": f"2026-07-{d:02d} 10:00:00", "pgrid_w": d} for d in range(1, 31)]

    def test_recent_days_trims_to_last_n_dates(self):
        r = b._recent_days(self.S, 7)
        assert len({x["timestamp"][:10] for x in r}) == 7
        assert r[-1]["timestamp"].startswith("2026-07-30")   # newest kept
        assert all(x["timestamp"] >= "2026-07-24" for x in r)

    def test_recent_days_full_window_unchanged(self):
        assert len(b._recent_days(self.S, 30)) == 30
        assert b._recent_days(self.S, 0) == self.S            # 0 -> no trim
        assert b._recent_days([], 7) == []


def _day(date, blocks, step_min=5):
    """blocks = [(hours, amps)] -> consecutive samples at the device's real ~5-min cadence.

    Sampling matters: battery_balance() only integrates intervals of 15 min or less (the same
    cap energy_by_date_kwh uses), so a datalogger outage is never extrapolated into hours of
    phantom current. Fixtures must therefore look like the real feed, not hourly points.
    """
    rows, t = [], 0
    for hours, amps in blocks:
        for _ in range(int(hours * 60 / step_min)):
            rows.append({"timestamp": f"{date} {t // 60:02d}:{t % 60:02d}:00", "battery_a": amps})
            t += step_min
    return rows


def _week(blocks):
    s = []
    for d in range(1, 8):
        s += _day(f"2026-09-{d:02d}", blocks)
    return s


class TestBatteryBalance:
    """The coulomb count: does what goes into the battery come back out?

    A battery's net Ah must average ~zero over time. Verified against production data
    2026-09: Chandika nets +5 Ah/day (healthy) while Gayan-IMH books +146 Ah/day on a
    ~215 Ah pack whose voltage completes a full cycle daily — impossible, so the current
    reading is at fault. This is published as a data-quality signal, never silently
    "corrected": `Batt Current` is the only battery figure the inverter exposes, and
    deriving the battery from the energy balance measured WORSE (it charges conversion
    losses to the battery and degrades the plants that currently read correctly).
    """

    def test_charge_and_discharge_are_split_by_sign(self):
        # negative = charging, matching charge_state(): 5 h at -20 A then 5 h at +20 A
        bal = b.battery_balance(_day("2026-09-01", [(5, -20), (5, 20)]))["2026-09-01"]
        assert bal["ah_in"] > 95 and bal["ah_out"] > 95        # ~100 Ah each way
        assert bal["imbalance_pct"] <= 2                       # balances to within a sample

    def test_long_gaps_are_not_integrated(self):
        """A datalogger outage must not be extrapolated into hours of phantom current."""
        s = [{"timestamp": "2026-09-01 00:00:00", "battery_a": -10},
             {"timestamp": "2026-09-01 06:00:00", "battery_a": -10}]
        # Nothing is integrated at all, so the day never appears in the result.
        assert b.battery_balance(s).get("2026-09-01", {}).get("ah_in", 0) == 0.0

    def test_missing_current_is_skipped(self):
        s = [{"timestamp": "2026-09-01 00:00:00", "battery_a": None},
             {"timestamp": "2026-09-01 00:05:00", "battery_a": -12}]
        assert b.battery_balance(s).get("2026-09-01", {}).get("ah_in", 0) == 0.0

    def test_imbalance_pct_is_share_of_throughput(self):
        # 5 h charging at -40 A (~200 Ah in), 5 h discharging at +10 A (~50 Ah out)
        bal = b.battery_balance(_day("2026-09-01", [(5, -40), (5, 10)]))["2026-09-01"]
        assert bal["net_ah"] > 140
        assert 70 <= bal["imbalance_pct"] <= 80                # ~150/200


class TestBatteryHealth:
    BALANCED = [(5, -20), (5, 20)]          # in == out
    ACCUMULATING = [(5, -40), (5, 5)]       # ~200 Ah in, ~25 Ah out — impossible sustained
    DRAINING = [(5, -5), (5, 40)]           # the mirror image

    def test_balanced_pack_is_ok(self):
        h = b.battery_health(b.battery_balance(_week(self.BALANCED)), "lfp")
        assert h["ok"] is True and "balances" in h["reason"]

    def test_accumulating_pack_is_flagged(self):
        h = b.battery_health(b.battery_balance(_week(self.ACCUMULATING)), "lfp")
        assert h["ok"] is False
        assert h["mean_net_ah_per_day"] > 0
        assert "sensor" in h["reason"]          # LFP cannot absorb charge it never returns

    def test_too_few_days_is_undecided_not_a_flag(self):
        """A brand-new or mostly-offline plant must not be accused."""
        s = _day("2026-09-01", self.ACCUMULATING) + _day("2026-09-02", self.ACCUMULATING)
        h = b.battery_health(b.battery_balance(s), "lfp")
        assert h["ok"] is None and "not enough" in h["reason"]

    def test_lead_acid_gets_a_wider_tolerance_and_its_own_diagnosis(self):
        """Float/gassing genuinely consumes charge on a lead bank, so the same imbalance
        that condemns an LFP sensor may just be an ageing battery."""
        bal = b.battery_balance(_week(self.ACCUMULATING))
        lfp, lead = b.battery_health(bal, "lfp"), b.battery_health(bal, "lead")
        assert lead["threshold_pct"] > lfp["threshold_pct"]
        assert lead["ok"] is False and "sulfated" in lead["reason"]
        assert "LFP" not in lead["reason"]      # don't blame LFP chemistry on a lead bank

    def test_a_mild_imbalance_passes_on_lead_but_fails_on_lfp(self):
        """The tolerance split has to actually change a verdict, or it is decoration."""
        mild = _week([(5, -22), (5, 18)])       # ~18% imbalance: over 10%, under 25%
        bal = b.battery_balance(mild)
        assert b.battery_health(bal, "lfp")["ok"] is False
        assert b.battery_health(bal, "lead")["ok"] is True

    def test_draining_pack_reports_the_opposite_problem(self):
        h = b.battery_health(b.battery_balance(_week(self.DRAINING)), "lead")
        assert h["ok"] is False and h["mean_net_ah_per_day"] < 0
        assert "losing charge" in h["reason"]

    def test_idle_plant_is_not_flagged(self):
        """Negligible-throughput days are ignored, so a plant that simply isn't cycling
        doesn't get reported as broken."""
        h = b.battery_health(b.battery_balance(_week([(5, -0.2)])), "lfp")
        assert h["ok"] is None


class TestAccumulatorMapping:
    """The inverter's lifetime kWh counters are the only AUTHORITATIVE energy figures it
    reports — no integration, so none of the integration error. Three of the four were
    fetched and discarded until 2026-09."""

    @pytest.mark.parametrize("title,key", [
        ("Accumulated PV Power", "acc_pv_kwh"),
        ("Accumulated Load Power", "acc_load_kwh"),
        ("Accumulated Sell Power", "acc_sell_kwh"),
        ("Accumulated Self_Use Power", "acc_selfuse_kwh"),
    ])
    def test_each_accumulator_maps(self, title, key):
        assert b._map_key(title)[0] == key

    def test_accumulators_do_not_collide_with_instantaneous_fields(self):
        """'Accumulated Load Power' must not be swallowed by the PLoad rule, etc."""
        live = ["Battery Voltage", "Batt Current", "Charger Power", "PLoad", "PGrid",
                "Grid Voltage", "Inverter Voltage", "rated power", "work state",
                "Accumulated PV Power", "Accumulated Load Power",
                "Accumulated Sell Power", "Accumulated Self_Use Power"]
        keys = [b._map_key(t)[0] for t in live]
        assert all(k is not None for k in keys)
        assert len(keys) == len(set(keys)), f"duplicate mapping: {keys}"


class TestChemistry:
    def test_plant_block_carries_chem(self):
        assert b.build_plant_block({}, {"alias": "x"}, [], caps={"chem": "lead"})["chem"] == "lead"

    def test_plant_block_defaults_chem_to_lfp(self):
        """Unflagged accounts keep the previous behaviour exactly."""
        assert b.build_plant_block({}, {"alias": "x"}, [], caps={})["chem"] == "lfp"

    def test_accounts_loader_reads_chem(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text(json.dumps({"company_key": "ck", "accounts": [
            {"label": "L", "username": "u", "password": "p", "ph1800": True, "chem": "LEAD"}]}),
            encoding="utf-8")
        _ck, accts = b.load_ph1800_accounts(p)
        assert accts[0][3]["chem"] == "lead"       # normalised to lowercase
