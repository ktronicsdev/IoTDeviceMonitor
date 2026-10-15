"""
UC1: a production alert must stop repeating itself.

The device-alarm path has enforced this for a year — three sends, four hours
apart, then silence. The production-alert path in check_anomaly.py records the
same fields (`send_count`, `last_sent`) and consults neither: it re-sends on
every run that still sees the condition, and only stops after
`auto_ignore_days = 3` DAYS have passed.

That was survivable at six runs a day. Since the alarm poll went hourly
(issue #10, merged 2026-10-11) it is up to thirty, so a single RED alert now
arrives ~90 times before it auto-ignores. thusharasameera-mendis-house hit
send_count=4 within nine hours on 2026-10-15.

These tests pin the behaviour UC1 should have. They are deliberately written
against the observable output — how many runs actually emit the alert — rather
than against any particular implementation, so the fix is free to use a send
cap, an interval, or both.
"""

import csv
import json
import sys
import tempfile
import shutil
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent.parent.parent / \
    "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from check_anomaly import utc_today  # noqa: E402

MAX_SENDS = 3


class TestProductionAlertStopsRepeating:

    @pytest.fixture
    def data_dir(self):
        d = tempfile.mkdtemp()
        yield Path(d)
        shutil.rmtree(d)

    @pytest.fixture
    def state_file(self, tmp_path):
        return tmp_path / "alerts_state.json"

    @pytest.fixture
    def out_dir(self, tmp_path):
        d = tmp_path / "alerts"
        d.mkdir()
        return d

    def _write_red_plant(self, data_dir, plant):
        """14 days at 10 kWh, then three days at 1.5 — a textbook RED."""
        today = utc_today()
        rows = [((today - timedelta(days=i)).strftime("%Y-%m-%d"), 10.0)
                for i in range(17, 2, -1)]
        rows += [((today - timedelta(days=i)).strftime("%Y-%m-%d"), 1.5)
                 for i in range(2, -1, -1)]

        by_month = defaultdict(list)
        for day, kwh in rows:
            by_month[day[:7]].append((day, kwh))

        for month, month_rows in by_month.items():
            with open(data_dir / f"{plant}-{month}.csv", "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["date", "kwh"])
                w.writerows(month_rows)

    def _run_once(self, data_dir, out_dir, state_file):
        """One scheduled run. Returns the alerts it would have emailed."""
        from check_anomaly import main as check_anomaly_main
        sys.argv = [
            "check_anomaly.py",
            "--data-dir", str(data_dir),
            "--out-dir", str(out_dir),
            "--state-file", str(state_file),
        ]
        check_anomaly_main()
        payload = json.loads(
            (out_dir / "alerts.json").read_text(encoding="utf-8"))
        return [a for a in payload.get("alerts", [])
                if a.get("severity") == "RED"]

    def test_the_same_red_alert_is_not_sent_more_than_three_times(
            self, data_dir, out_dir, state_file):
        """
        THE regression this file exists for.

        Ten runs in one day, nothing about the plant changing. The alert must
        be emitted at most three times. Today it is emitted ten, because
        `send_count` is incremented and never read.
        """
        plant = "cap-test-plant"
        self._write_red_plant(data_dir, plant)

        sent = sum(
            1 for _ in range(10)
            if any(a["plant_key"] == plant
                   for a in self._run_once(data_dir, out_dir, state_file))
        )

        assert sent <= MAX_SENDS, (
            f"the same RED alert was emitted {sent} times in one day. "
            f"A repeated alert teaches the reader to ignore the channel — "
            f"cap it at {MAX_SENDS} like the device-alarm path does, and let "
            f"the state file remember that it has already been said."
        )

    def test_send_count_is_actually_consulted_not_just_recorded(
            self, data_dir, out_dir, state_file):
        """
        The narrower statement of the same bug, so a failure says which half
        is missing. State already carries send_count; nothing reads it.
        """
        plant = "cap-test-counted"
        self._write_red_plant(data_dir, plant)

        for _ in range(MAX_SENDS + 2):
            self._run_once(data_dir, out_dir, state_file)

        state = json.loads(state_file.read_text(encoding="utf-8"))
        entry = state.get(f"{plant}:RED")
        assert entry is not None, "the alert must be tracked in state"

        assert entry["send_count"] <= MAX_SENDS, (
            f"send_count reached {entry['send_count']}: it is being "
            f"incremented on every run rather than used as a limit"
        )

    def test_two_runs_in_quick_succession_send_once(
            self, data_dir, out_dir, state_file):
        """
        A cap alone is not enough at hourly polling: three sends would arrive
        in three consecutive hours and still read as a flood. The device-alarm
        path spaces its three sends four hours apart; this one should too.
        """
        plant = "cap-test-interval"
        self._write_red_plant(data_dir, plant)

        first = self._run_once(data_dir, out_dir, state_file)
        second = self._run_once(data_dir, out_dir, state_file)

        assert any(a["plant_key"] == plant for a in first), \
            "the first run must send"
        assert not any(a["plant_key"] == plant for a in second), (
            "a second run minutes later re-sent the same alert — space the "
            "sends out, otherwise the cap just compresses the flood instead "
            "of preventing it"
        )

    def test_a_first_time_alert_still_goes_out_immediately(
            self, data_dir, out_dir, state_file):
        """
        The counterpart, so a cap cannot be 'fixed' by muffling real faults.
        A plant that has just failed must be reported on the very first run.
        """
        plant = "cap-test-first"
        self._write_red_plant(data_dir, plant)

        first = self._run_once(data_dir, out_dir, state_file)

        assert any(a["plant_key"] == plant for a in first), (
            "a newly detected RED alert must be sent at once — the cap is "
            "about repetition, never about delaying the first warning"
        )
