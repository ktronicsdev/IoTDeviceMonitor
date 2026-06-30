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
import check_ph1000_bms as bms  # noqa: E402
import check_ph1000_weather as wx  # noqa: E402

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
    # PV = PInverter; Load = max(0, PInverter + ChargerPower - 40W self-use)
    @pytest.mark.parametrize("battery_w,pinverter,exp_pv,exp_load", [
        (-542, 974, 974, 392),       # 974 + (-542) - 40 = 392
        (-1806, 2534, 2534, 688),    # midday: PV charging hard, modest load
        (-707, 1008, 1008, 261),     # afternoon
        (388, 0, 0, 348),            # night: battery discharge - self-use
        (-700, 0, 0, 0),             # grid-charging at night, no PV/load
        (500, 2000, 2000, 2460),     # PV + battery discharge both feed load
    ])
    def test_pv_and_load_estimates(self, battery_w, pinverter, exp_pv, exp_load):
        pv, load = m.derive_energy_balance(battery_w, pinverter)
        assert pv == exp_pv
        assert load == exp_load

    def test_none_inputs_return_none(self):
        assert m.derive_energy_balance(None, 0) == (None, None)
        assert m.derive_energy_balance(0, None) == (None, None)

    def test_load_never_negative(self):
        _, load = m.derive_energy_balance(-1000, 0)  # charging (ChargerPower<0), PInverter 0
        assert load == 0


class TestChargeState:
    def test_states(self):
        assert m.charge_state(-707) == "charging"      # ChargerPower < 0 -> into battery
        assert m.charge_state(336) == "discharging"    # ChargerPower > 0 -> battery out
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
            "battery_v": {"value": "52.8"}, "battery_a": {"value": "-10.2"},
            "battery_w": {"value": "-542"}, "pinverter_w": {"value": "974"},
            "work_state": {"value": "OffGrid"},
        }
        row = m.measured_row_from_live(fields, "2026-06-28 07:59:47")
        assert row["source"] == "measured"
        assert row["charge_state"] == "charging"          # ChargerPower -542 -> charging
        assert row["pv_power_w_est"] == 974               # PV = PInverter
        assert row["load_power_w_est"] == 392             # 974 + (-542) - 40 self-use

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


class TestBMSDecode:
    # captured Hystorix WIFI_Band frame; offsets validated against the live PACEEX app.
    FRAME = ("9A00000A000000330100000485000014ED0000156F0000271000002710376400000001"
             "000000000000000001090D1501100D1101020BF701040BF55F2B9D")

    def test_decode_wifi_band(self):
        d = bms.decode_wifi_band(self.FRAME)
        assert d["soc"] == 55
        assert d["soh"] == 100
        assert d["voltage"] == 53.57
        assert d["remaining_ah"] == 54.87
        assert d["full_ah"] == 100.0
        assert d["current"] == 11.57
        assert d["cycles"] == 1                   # u16@33 (was mis-read at @31)
        assert d["state"] == "charging"          # current > 0
        assert d["high_cell_mv"] == 3349
        assert d["low_cell_mv"] == 3345
        assert d["max_temp"] == 33.3
        assert d["min_temp"] == 33.1

    # second frame, captured live with the PACEEX "Summary data" screen open:
    # app showed SOC 80, cycles 1, remaining 79.76 Ah, cells 3355/3352 mV, temps 34.7/34.1 C.
    FRAME2 = ("9A00000A0000003301000003CE0000150100001F420000271000002710506400000001"
              "000000000000000001090D2101100D1D01020C0501040BFF041E9D")

    def test_decode_wifi_band_live_groundtruth(self):
        d = bms.decode_wifi_band(self.FRAME2)
        assert d["soc"] == 80                     # app: SOC 80%
        assert d["soh"] == 100                    # app: SOH 100%
        assert d["cycles"] == 1                   # app: Cycles 1
        assert d["full_ah"] == 100.0
        assert d["designed_ah"] == 100.0
        assert d["high_cell_mv"] == 3361          # app: 3355 mV (drifts slightly between samples)
        assert d["low_cell_mv"] == 3357           # app: 3352 mV
        assert d["max_temp"] == 34.7              # app: 34.7 C
        assert d["min_temp"] == 34.1              # app: 34.1 C
        assert d["state"] == "charging"

    def test_decode_short_frame_returns_none(self):
        assert bms.decode_wifi_band("9A00") is None


class TestAuthFailedDetector:
    """auth_failed must flag ONLY a dead cloud session (re-login needed), not an offline pack."""

    def _run(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bms, "REFRESH_TOKEN", "rt")
        monkeypatch.setattr(bms, "IDENTITY_ID", "id")
        out = tmp_path / "b.json"
        monkeypatch.setattr(sys, "argv", ["check_ph1000_bms.py", "--out", str(out)])
        bms.main()
        return json.load(open(out, encoding="utf-8"))

    def test_refresh_rejected_sets_auth_failed(self, monkeypatch, tmp_path):
        def boom(*a, **k):
            raise RuntimeError("checkOrRefreshSession code 2401")
        monkeypatch.setattr(bms, "refresh_iot_token", boom)
        d = self._run(monkeypatch, tmp_path)
        assert d["auth_failed"] is True and d["ok"] is False

    def test_transient_network_error_does_not_alert(self, monkeypatch, tmp_path):
        import urllib.error
        def neterr(*a, **k):
            raise urllib.error.URLError("temporary DNS hiccup")
        monkeypatch.setattr(bms, "refresh_iot_token", neterr)
        d = self._run(monkeypatch, tmp_path)
        assert d["auth_failed"] is False        # transient -> no false re-login alert

    def test_datalogger_offline_is_not_auth_failed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bms, "refresh_iot_token", lambda *a, **k: ("tok", "rt"))
        monkeypatch.setattr(bms, "fetch_pack",
                            lambda p, t: {"name": p["name"], "offline": True})
        d = self._run(monkeypatch, tmp_path)
        assert d["auth_failed"] is False and d["ok"] is False   # session fine -> no alert

    def test_stale_frame_sets_data_stale_min(self, monkeypatch, tmp_path):
        import time
        old_ms = int((time.time() - 1000 * 60) * 1000)         # newest frame ~1000 min old
        monkeypatch.setattr(bms, "refresh_iot_token", lambda *a, **k: ("tok", "rt"))
        monkeypatch.setattr(bms, "fetch_pack", lambda p, t: {
            "name": p["name"], "soc": 50, "voltage": 53.0, "current": 0.0, "state": "idle",
            "max_temp": 30.0, "min_temp": 30.0, "cycles": 1, "reported_ms": old_ms})
        d = self._run(monkeypatch, tmp_path)
        # healthy session, but the workflow should alert on a long-silent datalogger
        assert d["ok"] is True and d["auth_failed"] is False
        assert 995 <= d["data_stale_min"] <= 1006


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


class TestWeatherCodes:
    def test_known_codes(self):
        assert wx.weather_label(0) == ("Clear", "clear")
        assert wx.weather_label(3) == ("Overcast", "cloudy")
        assert wx.weather_label(63)[1] == "rain"
        assert wx.weather_label(95)[1] == "storm"
        assert wx.weather_label(2)[1] == "partly"

    def test_unknown_and_bad_input(self):
        assert wx.weather_label(123)[1] == "unknown"
        assert wx.weather_label(None) == ("Unknown", "unknown")
        assert wx.weather_label("x") == ("Unknown", "unknown")


class TestWeatherAggregation:
    def _hourly(self):
        # 2 days x 4 hours. Day 1 = clear (GHI == clear-sky envelope), Day 2 = heavy cloud
        # (GHI ~30% of envelope) + rain. terrestrial_radiation is the clear-sky reference;
        # clear-sky surface = 0.75 x terrestrial, so day-1 GHI is set to exactly 0.75 x tr.
        return {
            "time": ["2026-06-27T06:00", "2026-06-27T09:00", "2026-06-27T12:00", "2026-06-27T18:00",
                     "2026-06-28T06:00", "2026-06-28T09:00", "2026-06-28T12:00", "2026-06-28T18:00"],
            "terrestrial_radiation": [0, 400, 800, 0,   0, 400, 800, 0],
            "shortwave_radiation":   [0, 300, 600, 0,   0, 90, 180, 0],   # d1=0.75xtr, d2~0.225xtr
            "cloud_cover":           [0, 0,   0,   0,   90, 95, 100, 80],
            "precipitation":         [0, 0,   0,   0,   0,  2,  6,   1],
            "weather_code":          [0, 1,   0,   0,   3,  63, 65,  3],
        }

    def test_clear_day_zero_loss(self):
        daily = wx.aggregate_daily(self._hourly())
        d1 = daily[0]
        assert d1["date"] == "2026-06-27"
        # GHI == 0.75 x terrestrial == clear-sky -> ~0% loss, factor ~1.0
        assert d1["loss_pct"] == 0
        assert d1["harvest_factor"] == 1.0
        assert d1["category"] == "clear"
        assert d1["rain_mm"] == 0.0

    def test_cloudy_day_high_loss(self):
        daily = wx.aggregate_daily(self._hourly())
        d2 = daily[1]
        assert d2["date"] == "2026-06-28"
        assert d2["loss_pct"] > 50            # heavy cloud -> big harvest loss
        assert d2["rain_mm"] == 9.0           # 2 + 6 + 1
        assert d2["cloud_pct"] >= 90
        assert d2["category"] == "rain"       # dominant daylight code is rain (63/65)

    def test_ghi_and_clear_kwh_units(self):
        d1 = wx.aggregate_daily(self._hourly())[0]
        # clear_kwh = 0.75 * (0+400+800+0) Wh /1000 = 0.9 kWh/m^2
        assert d1["clear_kwh_m2"] == 0.9
        # ghi_kwh = (0+300+600+0)/1000 = 0.9
        assert d1["ghi_kwh_m2"] == 0.9

    def test_hourly_ghi_series_shape(self):
        s = wx.hourly_ghi_series(self._hourly())
        assert s[1]["timestamp"] == "2026-06-27 09:00"
        assert s[1]["ghi"] == 300
        assert s[1]["cloud"] == 0          # cloud/rain now included for the weather chart
        assert s[1]["rain"] == 0.0
        assert len(s) == 8

    def test_hourly_series_carries_cloud_and_rain(self):
        s = wx.hourly_ghi_series(self._hourly())
        wet = s[5]                          # 2026-06-28 09:00 -> cloud 95, rain 2
        assert wet["cloud"] == 95
        assert wet["rain"] == 2.0

    def test_empty_hourly(self):
        assert wx.aggregate_daily({}) == []
        assert wx.hourly_ghi_series({}) == []


class TestWeatherCurrentAndLocation:
    def test_build_current(self):
        cur = {"temperature_2m": 29.3, "cloud_cover": 75, "precipitation": 0.4,
               "weather_code": 3, "shortwave_radiation": 410.0}
        c = wx.build_current(cur)
        assert c["temp_c"] == 29.3
        assert c["cloud_pct"] == 75
        assert c["ghi_wm2"] == 410
        assert c["condition"] == "Overcast"
        assert c["category"] == "cloudy"

    def test_location_from_cli(self):
        assert wx.resolve_location(None, 6.5, 80.1) == (6.5, 80.1, "cli")

    def test_location_from_plant_json(self, tmp_path):
        p = tmp_path / "live.json"
        p.write_text(json.dumps({"plant": {"lat": 7.1, "lon": 80.5}}), encoding="utf-8")
        assert wx.resolve_location(str(p), None, None) == (7.1, 80.5, "plant")

    def test_location_falls_back_to_default(self, tmp_path):
        # plant present but no coords -> default
        p = tmp_path / "live.json"
        p.write_text(json.dumps({"plant": {"country": "LK"}}), encoding="utf-8")
        lat, lon, src = wx.resolve_location(str(p), None, None)
        assert src == "default"
        assert (lat, lon) == (wx.DEFAULT_LAT, wx.DEFAULT_LON)

    def test_location_missing_file_defaults(self):
        lat, lon, src = wx.resolve_location("/no/such/file.json", None, None)
        assert src == "default"
