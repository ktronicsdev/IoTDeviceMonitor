#!/usr/bin/env python3
"""
SolisCloud battery health report — tests.

No network: exercises the row extraction, unit handling and the fade/SOH
classification against a captured inverterDetail shape.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[6]
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_battery_health as bh  # noqa: E402

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)
# Commissioned 2025-10-02 (fisGenerateTime in ms), as on a real hybrid detail.
FIS_2025_10_02 = int(datetime(2025, 10, 2, tzinfo=timezone.utc).timestamp() * 1000)


def detail(**over):
    d = {
        "stationName": "Test Plant", "name": "Inv 1", "sn": "1033000000000001",
        "batteryType": "BYD-HVS/HVM/HVL/MaxLite", "batteryHealthSoh": 98.0,
        "batteryCapacitySoc": 9.0, "fisGenerateTime": FIS_2025_10_02,
        "batteryTotalChargeEnergy": 1.824, "batteryTotalChargeEnergyStr": "MWh",
        "batteryTotalDischargeEnergy": 1.733, "batteryTotalDischargeEnergyStr": "MWh",
    }
    d.update(over)
    return d


class TestUnits:
    def test_mwh_to_kwh(self):
        assert bh.to_kwh(1.824, "MWh") == 1824.0

    def test_kwh_default(self):
        assert bh.to_kwh("94", None) == 94.0

    def test_bad_value(self):
        assert bh.to_kwh("n/a", "kWh") is None


class TestClassify:
    def test_healthy_lfp(self):
        fade, status = bh.classify(98.0, 1.04)
        assert status == "OK" and round(fade, 1) == 1.9

    def test_fast_fade_flagged(self):
        assert bh.classify(92.0, 2.0)[1] == "WARN"      # 4 %/yr

    def test_low_soh_flagged_even_if_slow(self):
        assert bh.classify(80.0, 10.0)[1] == "WARN"     # 2 %/yr but under 85 %

    def test_new_battery_not_judged(self):
        assert bh.classify(99.0, 0.1) == (None, "NEW")


class TestBatteryRow:
    def test_row_from_hybrid(self):
        r = bh.battery_row(detail(), now=NOW)
        assert r["status"] == "OK"
        assert r["commissioned"] == "2025-10-02"
        assert r["discharged_kwh"] == 1733.0
        assert 1.0 < r["years"] < 1.1

    def test_no_battery_skipped(self):
        assert bh.battery_row(detail(batteryHealthSoh=0), now=NOW) is None
        assert bh.battery_row(detail(batteryHealthSoh=None), now=NOW) is None

    def test_report_lists_warnings_first(self):
        rows = [bh.battery_row(detail(), now=NOW),
                bh.battery_row(detail(name="Old", batteryHealthSoh=80.0), now=NOW)]
        report = bh.format_report(rows)
        assert "2 batteries, 1 need attention" in report
        body = report.splitlines()
        assert body.index(next(l for l in body if l.startswith("WARN"))) < \
            body.index(next(l for l in body if l.startswith("OK")))
