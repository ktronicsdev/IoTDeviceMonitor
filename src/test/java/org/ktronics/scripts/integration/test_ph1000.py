#!/usr/bin/env python3
"""
PH1000 inverter module — integration tests.

Covers the reliable-field mapping, the energy-balance PV/Load estimates, the
charge-state logic, CSV append/merge, account opt-in, and the dashboard JSON.
No network: pure logic against captured ShineMonitor shapes.
"""

import json
import sys
from pathlib import Path

import pytest

# Import the module under test from the MAIN scripts dir.
SRC = Path(__file__).resolve().parents[6]
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_ph1000 as m  # noqa: E402

# The real Data Details title order observed for this PH1000 (dat.title).
PH1000_TITLES = [
    {"title": "id"}, {"title": "Timestamp"},
    {"title": "Battery Voltage", "unit": "V"}, {"title": "PV Voltage", "unit": "V"},
    {"title": "Inverter Voltage", "unit": "V"}, {"title": "Batt Current", "unit": "A"},
    {"title": "Charger Current", "unit": "A"}, {"title": "Charger Power", "unit": "W"},
    {"title": "PLoad", "unit": "W"}, {"title": "Grid Voltage", "unit": "V"},
    {"title": "PInverter", "unit": "W"}, {"title": "Accumulated Sell Power", "unit": "kWh"},
    {"title": "Accumulated Load Power", "unit": "kWh"},
    {"title": "Accumulated Self_Use Power", "unit": "kWh"},
    {"title": "charger work enable"}, {"title": "Accumulated PV Power", "unit": "kWh"},
]


class TestColumnMapping:
    def test_history_columns_map_to_correct_indices(self):
        idx = m.map_history_columns(PH1000_TITLES)
        assert idx["timestamp"] == 1
        assert idx["battery_v"] == 2
        assert idx["battery_a"] == 6      # "Charger Current" is the real battery current
        assert idx["battery_w"] == 7      # "Charger Power" is the real battery power
        assert idx["pinverter_w"] == 10

    def test_garbage_batt_current_column_is_ignored(self):
        # "Batt Current" (index 5) is broken garbage and must NOT map to any field.
        idx = m.map_history_columns(PH1000_TITLES)
        assert 5 not in idx.values()

    def test_missing_titles_raises(self):
        with pytest.raises(ValueError):
            m.map_history_columns([])


class TestEnergyBalanceDerivation:
    # load = max(0, PInverter - ChargerPower - 10W inverter self-use); PV = PInverter
    @pytest.mark.parametrize("battery_w,pinverter,exp_pv,exp_load", [
        (-1806, 2534, 2534, 4330),   # midday: PV + battery discharge feed a big load
        (-707, 1008, 1008, 1705),    # afternoon
        (388, 0, 0, 0),              # evening grid-charge: load clamps to 0
        (-700, 0, 0, 690),           # night discharge: load = |discharge| - self-use
        (500, 2000, 2000, 1490),     # daytime PV charging battery + load
    ])
    def test_pv_and_load_estimates(self, battery_w, pinverter, exp_pv, exp_load):
        pv, load = m.derive_energy_balance(battery_w, pinverter)
        assert pv == exp_pv
        assert load == exp_load

    def test_none_inputs_return_none(self):
        assert m.derive_energy_balance(None, 0) == (None, None)
        assert m.derive_energy_balance(0, None) == (None, None)

    def test_load_never_negative(self):
        _, load = m.derive_energy_balance(1000, 0)  # charging, PInverter 0
        assert load == 0


class TestChargeState:
    def test_states(self):
        assert m.charge_state(336) == "charging"
        assert m.charge_state(-707) == "discharging"
        assert m.charge_state(0) == "idle"
        assert m.charge_state(None) == "unknown"


class TestStatusLabel:
    def test_online_offline(self):
        assert m.status_label("0") == "Online"
        assert m.status_label(0) == "Online"
        assert "Offline" in m.status_label(1)


class TestMeasuredRowAndMerge:
    def test_measured_row_from_live_includes_estimates(self):
        fields = {
            "battery_v": {"value": "52.5"}, "battery_a": {"value": "6.4"},
            "battery_w": {"value": "-700"}, "pinverter_w": {"value": "0"},
            "work_state": {"value": "OffGrid"},
        }
        row = m.measured_row_from_live(fields, "2026-06-27 20:00:00")
        assert row["source"] == "measured"
        assert row["charge_state"] == "discharging"
        assert row["pv_power_w_est"] == 0
        assert row["load_power_w_est"] == 690  # night discharge: 700 - 10W self-use

    def test_merge_measured_supersedes_derived(self):
        history = [{"timestamp": "2026-06-27 12:00:00", "source": "derived", "battery_w": 100}]
        measured = [{"timestamp": "2026-06-27 12:00:00", "source": "measured", "battery_w": 200}]
        merged = m.merge_series(history, measured)
        assert len(merged) == 1
        assert merged[0]["source"] == "measured"
        assert merged[0]["battery_w"] == 200


class TestCsvRoundTrip:
    def test_append_and_reload(self, tmp_path):
        device = {"alias": "08B40001", "sn": "08B40001"}
        row1 = m.measured_row_from_live(
            {"battery_w": {"value": "100"}, "pinverter_w": {"value": "0"}},
            "2026-06-28 00:00:00")
        row2 = m.measured_row_from_live(
            {"battery_w": {"value": "-200"}, "pinverter_w": {"value": "300"}},
            "2026-06-28 00:05:00")
        m.append_measured_csv(tmp_path, "Mifanza", device, row1)
        m.append_measured_csv(tmp_path, "Mifanza", device, row2)
        # Re-appending the same timestamp must dedupe, not duplicate.
        m.append_measured_csv(tmp_path, "Mifanza", device, row1)

        loaded = m.load_measured_history(tmp_path, "Mifanza", device)
        # filter to our two timestamps (load reads whole month files)
        ours = [r for r in loaded if r["timestamp"].startswith("2026-06-28 00:0")]
        assert len(ours) == 2
        assert all(r["source"] == "measured" for r in ours)


class TestAccountOptIn:
    def _creds(self, tmp_path):
        p = tmp_path / "credentials.json"
        p.write_text(json.dumps({
            "company_key": "k",
            "accounts": [
                {"label": "Mifanza", "username": "Mifanza", "password": "x", "ph1000": True},
                {"label": "Other", "username": "o", "password": "y"},
            ],
        }), encoding="utf-8")
        return p

    def test_only_ph1000_flagged_accounts(self, tmp_path):
        _, accts = m.load_ph1000_accounts(self._creds(tmp_path))
        labels = [a[0] for a in accts]
        assert labels == ["Mifanza"]

    def test_customer_filter_overrides_flag(self, tmp_path):
        _, accts = m.load_ph1000_accounts(self._creds(tmp_path), customer="Other")
        assert [a[0] for a in accts] == ["Other"]


class TestLiveParsing:
    def test_fetch_live_parses_reliable_fields(self, monkeypatch):
        # Shape of queryDeviceLastData (flat par dict under dat).
        fake = {"err": 0, "dat": {
            "Timestamp": {"par": "Timestamp", "val": "2026-06-28 04:20:05"},
            "bv": {"par": "Battery Voltage", "val": "52.5", "unit": "V"},
            "cc": {"par": "Charger Current", "val": "6.4", "unit": "A"},
            "cp": {"par": "Charger Power", "val": "336", "unit": "W"},
            "pi": {"par": "PInverter", "val": "0", "unit": "W"},
            "ws": {"par": "work state", "val": "PowerOn"},
            "junk": {"par": "Batt Current", "val": "100", "unit": "A"},
        }}
        monkeypatch.setattr(m, "api_call", lambda *a, **k: fake)
        fields, last_update, _ = m.fetch_live(None, None,
                                              {"pn": "p", "devcode": "697", "devaddr": "4", "sn": "s"})
        assert last_update == "2026-06-28 04:20:05"
        assert fields["battery_v"]["value"] == "52.5"
        assert fields["battery_w"]["value"] == "336"
        assert "battery_a" in fields and fields["battery_a"]["value"] == "6.4"
        # garbage "Batt Current" must not leak into a reliable field
        assert all(f["value"] != "100" for f in fields.values())
