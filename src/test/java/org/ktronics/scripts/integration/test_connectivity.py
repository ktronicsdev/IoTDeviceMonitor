#!/usr/bin/env python3
"""
Integration tests for the CONNECTIVITY check and the plant exclusion list.

The problem these guard: for months the platform could not tell "the solar
system stopped producing" from "the customer's WiFi died". Both write 0.0000
into the CSVs, so a plant with a dead dongle was reported to the owner as a
failing system — and a plant deliberately dropped from the platform simply
vanished with no trace in the logs.

Three things must hold, and each has tests below:

  1. A plant whose data has not advanced for N days (configurable, default 3)
     raises a CONNECTIVITY alert that says the LINK is down, and is NOT reported
     as a production fault or a dead system.
  2. A plant on the exclusion list is skipped by alarms, reports and counts,
     and the exclusion is visible in the logs.
  3. The admin summary states three counts plainly: reporting, dead link,
     excluded.

Written in the style of test_admin_alerts.py: temporary data directories, dates
built relative to check_anomaly.utc_today() so the suite never goes stale, and
the production code driven through its real entry points.
"""

import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[7]
SCRIPTS_DIR = PROJECT_ROOT / "src" / "main" / "java" / "org" / "ktronics" / "scripts"
CONFIG_DIR = PROJECT_ROOT / "src" / "main" / "java" / "org" / "ktronics" / "config"
WORKFLOWS_DIR = PROJECT_ROOT / ".github" / "workflows"
EXCLUSIONS_FILE = CONFIG_DIR / "excluded_plants.json"

sys.path.insert(0, str(SCRIPTS_DIR))

import check_connectivity  # noqa: E402
import connectivity  # noqa: E402
import exclusions  # noqa: E402
from check_anomaly import utc_today  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def write_monthly_csv(data_dir, plant_name, month, rows):
    """Create data/<plant>-<YYYY-MM>.csv the way the collectors do."""
    csv_file = Path(data_dir) / f"{plant_name}-{month}.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "kwh"])
        for date_str, kwh in rows:
            writer.writerow([date_str, kwh])
    return csv_file


def write_series(data_dir, plant_name, days_back_to_value):
    """
    Write a daily series from a {days_ago: kwh} map, split across month files.

    Written relative to today so the tests stay honest as the calendar moves.
    """
    today = utc_today()
    by_month = {}
    for days_ago, kwh in sorted(days_back_to_value.items(), reverse=True):
        day = today - timedelta(days=days_ago)
        by_month.setdefault(day.strftime("%Y-%m"), []).append((day.strftime("%Y-%m-%d"), kwh))
    for month, rows in by_month.items():
        write_monthly_csv(data_dir, plant_name, month, rows)


def healthy_series(days=20, kwh=10.0):
    """A plant producing normally right up to today."""
    return {i: kwh for i in range(days, -1, -1)}


def write_exclusions(path, entries):
    """Write an excluded_plants.json and point the module at it."""
    Path(path).write_text(
        json.dumps({"version": 1, "excluded": entries}, indent=2), encoding="utf-8"
    )
    exclusions.EXCLUSIONS_PATH = Path(path)
    exclusions.reset_cache()


def write_credentials(path, accounts):
    Path(path).write_text(
        json.dumps({"company_key": "test", "accounts": accounts}, indent=2), encoding="utf-8"
    )
    return Path(path)


@pytest.fixture(autouse=True)
def _isolate_exclusions():
    """
    Never let a test read (or leave behind) the real exclusion list.

    exclusions.py caches the parsed file, so both the path and the cache have to
    be put back or the next test inherits this one's fixture.
    """
    original_path = exclusions.EXCLUSIONS_PATH
    exclusions.reset_cache()
    yield
    exclusions.EXCLUSIONS_PATH = original_path
    exclusions.reset_cache()


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


# --------------------------------------------------------------------------
# 1. Classification: is the link down, and why?
# --------------------------------------------------------------------------

class TestLinkClassification:
    """connectivity.plant_link_status — the one signal that separates the two faults."""

    def test_plant_reporting_today_is_not_link_down(self, data_dir):
        write_series(data_dir, "live-plant", healthy_series())
        from check_anomaly import load_daily_series
        series = load_daily_series(data_dir)["live-plant"]

        status = connectivity.plant_link_status("live-plant", series, utc_today())

        assert status.link_down is False
        assert status.kind == connectivity.KIND_REPORTING
        assert status.days_stale == 0

    def test_plant_silent_for_stale_days_is_link_down(self, data_dir):
        # Produced normally until 4 days ago, zeros ever since — dongle offline.
        series = {i: 10.0 for i in range(20, 3, -1)}
        series.update({3: 0.0, 2: 0.0, 1: 0.0, 0: 0.0})
        write_series(data_dir, "dark-plant", series)
        from check_anomaly import load_daily_series
        loaded = load_daily_series(data_dir)["dark-plant"]

        status = connectivity.plant_link_status("dark-plant", loaded, utc_today())

        assert status.link_down is True
        assert status.days_stale == 4

    def test_stale_days_threshold_is_configurable(self, data_dir):
        """Default is 3 days; the owner can widen it without touching code."""
        series = {i: 10.0 for i in range(20, 4, -1)}
        series.update({4: 0.0, 3: 0.0, 2: 0.0, 1: 0.0, 0: 0.0})
        write_series(data_dir, "quiet-plant", series)
        from check_anomaly import load_daily_series
        loaded = load_daily_series(data_dir)["quiet-plant"]

        assert connectivity.plant_link_status(
            "quiet-plant", loaded, utc_today(), stale_days=3).link_down is True
        assert connectivity.plant_link_status(
            "quiet-plant", loaded, utc_today(), stale_days=7).link_down is False

    def test_zeros_only_is_distinguished_from_no_rows(self, data_dir):
        """
        Rows still arriving but every value 0 = the dongle is offline.
        No rows at all = the portal has nothing for us, a different fix.
        """
        zeros = {i: 10.0 for i in range(20, 9, -1)}
        zeros.update({i: 0.0 for i in range(9, -1, -1)})
        write_series(data_dir, "zeros-plant", zeros)

        # Rows simply stop 10 days ago.
        write_series(data_dir, "gone-plant", {i: 10.0 for i in range(20, 9, -1)})

        from check_anomaly import load_daily_series
        loaded = load_daily_series(data_dir)

        zeros_status = connectivity.plant_link_status("zeros-plant", loaded["zeros-plant"], utc_today())
        gone_status = connectivity.plant_link_status("gone-plant", loaded["gone-plant"], utc_today())

        assert zeros_status.kind == connectivity.KIND_ZEROS_ONLY
        assert gone_status.kind == connectivity.KIND_NO_ROWS
        assert zeros_status.link_down and gone_status.link_down

    def test_never_reported_plant_is_flagged(self, data_dir):
        write_series(data_dir, "silent-plant", {i: 0.0 for i in range(30, -1, -1)})
        from check_anomaly import load_daily_series
        loaded = load_daily_series(data_dir)["silent-plant"]

        status = connectivity.plant_link_status("silent-plant", loaded, utc_today())

        assert status.link_down is True
        assert status.kind == connectivity.KIND_NEVER_REPORTED
        assert status.last_nonzero_date is None

    def test_future_rows_do_not_hide_a_dead_link(self, data_dir):
        """
        REGRESSION: the collectors pre-seed the whole month with 0.0000 rows, so
        a plant's CSV holds dates in the future. If those counted as "data", a
        dead plant would look like it had reported and the alert would never fire.
        """
        today = utc_today()
        series = {i: 10.0 for i in range(20, 9, -1)}
        series.update({i: 0.0 for i in range(9, -1, -1)})
        write_series(data_dir, "preseeded-plant", series)

        # Add rows dated into the future, exactly as the real files have.
        future_month = (today + timedelta(days=5)).strftime("%Y-%m")
        future_rows = [((today + timedelta(days=d)).strftime("%Y-%m-%d"), 0.0)
                       for d in range(1, 6)]
        existing = Path(data_dir) / f"preseeded-plant-{future_month}.csv"
        if existing.exists():
            with open(existing, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(future_rows)
        else:
            write_monthly_csv(data_dir, "preseeded-plant", future_month, future_rows)

        from check_anomaly import load_daily_series
        loaded = load_daily_series(data_dir)["preseeded-plant"]
        status = connectivity.plant_link_status("preseeded-plant", loaded, today)

        assert status.link_down is True, "future-dated zero rows must not count as fresh data"
        assert status.last_data_date <= today


# --------------------------------------------------------------------------
# 2. The three counts
# --------------------------------------------------------------------------

class TestFleetCounts:
    """The numbers the owner quotes: reporting, dead link, excluded."""

    def _fleet(self, data_dir, exclusion_entries=None, tmp_path=None, stale_days=3):
        if exclusion_entries is not None:
            write_exclusions(tmp_path / "excluded_plants.json", exclusion_entries)
        from check_anomaly import load_daily_series
        return connectivity.classify_fleet(
            load_daily_series(data_dir), utc_today(), stale_days=stale_days)

    def test_counts_split_reporting_link_down_and_excluded(self, data_dir, tmp_path):
        write_series(data_dir, "alive-one", healthy_series())
        write_series(data_dir, "alive-two", healthy_series())
        write_series(data_dir, "dead-link", {i: 10.0 for i in range(20, 9, -1)})
        write_series(data_dir, "dropped-plant", healthy_series())

        fleet = self._fleet(
            data_dir,
            [{"plant": "dropped-plant", "reason": "Customer refused to fix the link",
              "since": "2026-09-01"}],
            tmp_path,
        )

        assert fleet.counts["reporting"] == 2
        assert fleet.counts["link_down"] == 1
        assert fleet.counts["excluded"] == 1
        assert fleet.counts["monitored"] == 3, "excluded plants are not monitored"

    def test_counts_lines_state_all_three_numbers(self, data_dir, tmp_path):
        write_series(data_dir, "alive-one", healthy_series())
        write_series(data_dir, "dead-link", {i: 10.0 for i in range(20, 9, -1)})
        fleet = self._fleet(data_dir, [], tmp_path)

        text = "\n".join(fleet.counts_lines()).lower()

        assert "plants reporting" in text
        assert "dead link" in text
        assert "excluded" in text

    def test_excluded_plant_is_in_neither_other_bucket(self, data_dir, tmp_path):
        """An excluded plant must not quietly reappear as a problem to chase."""
        write_series(data_dir, "dropped-plant", {i: 0.0 for i in range(30, -1, -1)})
        fleet = self._fleet(
            data_dir,
            [{"plant": "dropped-plant", "reason": "Off the platform", "since": "2026-09-01"}],
            tmp_path,
        )

        assert fleet.counts["link_down"] == 0
        assert fleet.counts["reporting"] == 0
        assert fleet.counts["excluded"] == 1

    def test_exclusion_without_data_files_still_counts(self, data_dir, tmp_path):
        """CSVs age out of the repo; the plant is still off the platform."""
        write_series(data_dir, "alive-one", healthy_series())
        fleet = self._fleet(
            data_dir,
            [{"plant": "long-gone-plant", "reason": "Removed in 2025", "since": "2025-06-01"}],
            tmp_path,
        )

        assert fleet.counts["excluded"] == 1
        assert fleet.excluded[0]["in_data"] is False

    def test_customer_exclusion_covers_all_their_plants(self, data_dir, tmp_path):
        write_series(data_dir, "someone-plant-a", healthy_series())
        write_series(data_dir, "someone-plant-b", healthy_series())
        write_series(data_dir, "other-plant", healthy_series())

        fleet = self._fleet(
            data_dir,
            [{"customer": "Someone", "reason": "Dropped, link never restored",
              "since": "2026-09-01"}],
            tmp_path,
        )

        assert fleet.counts["excluded"] == 2
        assert fleet.counts["reporting"] == 1

    def test_fleet_wide_outage_is_not_blamed_on_the_customers(self, data_dir, tmp_path):
        """
        Every plant silent at once is our collector or the portal API, not 20
        customers unplugging their routers on the same afternoon.
        """
        for name in ("plant-a", "plant-b", "plant-c"):
            write_series(data_dir, name, {i: 10.0 for i in range(20, 9, -1)})
        fleet = self._fleet(data_dir, [], tmp_path)

        assert fleet.fleet_wide_outage is True

    def test_one_plant_reporting_means_it_is_not_fleet_wide(self, data_dir, tmp_path):
        write_series(data_dir, "plant-a", {i: 10.0 for i in range(20, 9, -1)})
        write_series(data_dir, "plant-b", healthy_series())
        fleet = self._fleet(data_dir, [], tmp_path)

        assert fleet.fleet_wide_outage is False


class TestPlatformSelection:
    """
    REGRESSION: counting the wrong fleet, or none at all.

    check_anomaly.load_daily_series(platform_filter=...) globs "<platform>-*.csv".
    DessMonitor files carry that prefix, ShineMonitor files carry no prefix, so
    asking it for "shinemonitor" returns an EMPTY fleet — and every headline
    count on the admin summary would read 0 while 24 plants were being watched.
    """

    def test_shinemonitor_selection_is_everything_not_dessmonitor(self, data_dir):
        write_series(data_dir, "ordinary-plant", healthy_series())
        write_series(data_dir, "dessmonitor-other-plant", healthy_series())
        from check_anomaly import load_daily_series
        loaded = load_daily_series(data_dir)

        selected = connectivity.select_platform(loaded, "shinemonitor")

        assert set(selected) == {"ordinary-plant"}
        assert selected, "ShineMonitor must never select an empty fleet"

    def test_dessmonitor_selection_takes_only_the_prefixed_files(self, data_dir):
        write_series(data_dir, "ordinary-plant", healthy_series())
        write_series(data_dir, "dessmonitor-other-plant", healthy_series())
        from check_anomaly import load_daily_series

        selected = connectivity.select_platform(load_daily_series(data_dir), "dessmonitor")

        assert set(selected) == {"dessmonitor-other-plant"}

    def test_no_platform_defaults_to_shinemonitor(self, data_dir):
        write_series(data_dir, "ordinary-plant", healthy_series())
        write_series(data_dir, "dessmonitor-other-plant", healthy_series())
        from check_anomaly import load_daily_series

        selected = connectivity.select_platform(load_daily_series(data_dir), None)

        assert set(selected) == {"ordinary-plant"}

    def test_connectivity_cli_counts_shinemonitor_plants(self, data_dir, tmp_path):
        """End to end: --platform shinemonitor must not report an empty fleet."""
        write_series(data_dir, "ordinary-plant", healthy_series())
        write_series(data_dir, "dessmonitor-other-plant", healthy_series())
        out_dir = tmp_path / "alerts"
        sys.argv = [
            "check_connectivity.py",
            "--data-dir", str(data_dir),
            "--out-dir", str(out_dir),
            "--state-file", str(tmp_path / "state.json"),
            "--platform", "shinemonitor",
        ]
        check_connectivity.main()

        report = json.loads((out_dir / "connectivity.json").read_text(encoding="utf-8"))

        assert report["counts"]["monitored"] == 1
        assert [s["plant_key"] for s in report["reporting"]] == ["ordinary-plant"]

    def test_admin_summary_counts_shinemonitor_plants(self, data_dir, tmp_path):
        write_series(data_dir, "alpha-plant", healthy_series())
        write_series(data_dir, "dessmonitor-other-plant", healthy_series())
        creds = write_credentials(tmp_path / "creds.json", [
            {"label": "Alpha", "username": "a", "password": "p", "email": "a@example.com"},
        ])
        from generate_admin_summary import generate_admin_summary

        result = generate_admin_summary(str(creds), str(data_dir),
                                        str(tmp_path / "admin_summary.txt"))

        assert result["plants_monitored"] == 1, \
            "the ShineMonitor summary must count its own plants, not zero"


# --------------------------------------------------------------------------
# 3. The exclusion list itself
# --------------------------------------------------------------------------

class TestExclusionList:
    """exclusions.py and excluded_plants.json."""

    def test_config_file_exists_and_is_valid_json(self):
        assert EXCLUSIONS_FILE.exists(), "excluded_plants.json must exist"
        data = json.loads(EXCLUSIONS_FILE.read_text(encoding="utf-8"))
        assert isinstance(data.get("excluded"), list), "'excluded' must be a list"

    def test_config_file_is_committed(self):
        """Like features.json, the exclusion list is reviewable config, not a secret."""
        import subprocess
        result = subprocess.run(
            ["git", "check-ignore", str(EXCLUSIONS_FILE)],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        )
        assert result.returncode != 0, \
            "excluded_plants.json must be tracked in git — dropping a customer is a reviewable decision"

    def test_matches_plant_key(self, tmp_path):
        write_exclusions(tmp_path / "ex.json",
                         [{"plant": "abeetha-plant", "reason": "No link", "since": "2026-09-01"}])

        assert exclusions.is_plant_excluded("abeetha-plant") is True
        assert exclusions.is_plant_excluded("chandika-plant") is False
        assert "No link" in exclusions.reason_for_plant("abeetha-plant")

    def test_customer_exclusion_reaches_their_plants_and_their_reports(self, tmp_path):
        write_exclusions(tmp_path / "ex.json",
                         [{"customer": "Abeetha", "reason": "Dropped", "since": "2026-09-01"}])

        assert exclusions.is_customer_excluded("Abeetha") is True
        assert exclusions.is_plant_excluded("abeetha-plant") is True
        assert exclusions.is_customer_excluded("Chandika") is False

    def test_plant_exclusion_also_stops_that_customers_report(self, tmp_path):
        """Excluding 'abeetha-plant' must also stop the 'Abeetha' weekly email."""
        write_exclusions(tmp_path / "ex.json",
                         [{"plant": "abeetha-plant", "reason": "Dropped", "since": "2026-09-01"}])

        assert exclusions.is_customer_excluded("Abeetha") is True

    def test_matching_ignores_case_and_punctuation(self, tmp_path):
        write_exclusions(tmp_path / "ex.json",
                         [{"customer": "Gayan-IMH", "reason": "Test", "since": "2026-09-01"}])

        assert exclusions.is_customer_excluded("gayan imh") is True
        assert exclusions.is_customer_excluded("GAYANIMH") is True

    def test_missing_file_excludes_nothing(self, tmp_path):
        """
        Fails CLOSED. A lost config file must never drop a paying customer off
        the platform — an excluded plant briefly reappearing is visible, a
        monitored plant silently vanishing is not.
        """
        exclusions.EXCLUSIONS_PATH = tmp_path / "does-not-exist.json"
        exclusions.reset_cache()

        assert exclusions.entries() == []
        assert exclusions.is_plant_excluded("anything-at-all") is False

    def test_malformed_file_excludes_nothing(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ not json", encoding="utf-8")
        exclusions.EXCLUSIONS_PATH = bad
        exclusions.reset_cache()

        assert exclusions.is_plant_excluded("anything-at-all") is False

    def test_entry_naming_nothing_matches_nothing(self, tmp_path):
        """An empty entry must not normalise to '' and swallow the whole fleet."""
        write_exclusions(tmp_path / "ex.json", [{"reason": "oops", "since": "2026-09-01"}])

        assert exclusions.entries() == []
        assert exclusions.is_plant_excluded("some-plant") is False

    def test_log_lines_name_the_plant_and_the_reason(self, tmp_path):
        """Requirement: nobody should have to wonder where a plant went."""
        write_exclusions(tmp_path / "ex.json",
                         [{"plant": "dropped-plant", "reason": "Customer refused to fix the link",
                           "since": "2026-09-01"}])

        text = "\n".join(exclusions.log_lines())

        assert "dropped-plant" in text
        assert "Customer refused to fix the link" in text
        assert "2026-09-01" in text

    def test_log_lines_say_so_when_nothing_is_excluded(self, tmp_path):
        write_exclusions(tmp_path / "ex.json", [])
        assert "none" in "\n".join(exclusions.log_lines()).lower()


# --------------------------------------------------------------------------
# 4. The alert: 3 sends, 4 hours apart, reset on recovery
# --------------------------------------------------------------------------

class TestConnectivityAlertLifecycle:
    """check_connectivity.py follows the same discipline as the device alarms."""

    def _run(self, data_dir, tmp_path, extra_args=()):
        state_file = tmp_path / "state" / "connectivity_state.json"
        out_dir = tmp_path / "alerts"
        creds = write_credentials(tmp_path / "creds.json",
                                  [{"label": "Dark", "username": "u", "password": "p",
                                    "email": "dark@example.com"}])
        sys.argv = [
            "check_connectivity.py",
            "--data-dir", str(data_dir),
            "--out-dir", str(out_dir),
            "--state-file", str(state_file),
            "--credentials", str(creds),
            *extra_args,
        ]
        code = check_connectivity.main()
        return code, state_file, out_dir

    def _two_plant_fleet(self, data_dir):
        """One healthy, one dark — so this is never a fleet-wide outage."""
        write_series(data_dir, "healthy-plant", healthy_series())
        write_series(data_dir, "dark-plant", {i: 10.0 for i in range(20, 9, -1)})

    def test_first_run_alerts_and_exits_2(self, data_dir, tmp_path):
        self._two_plant_fleet(data_dir)
        code, state_file, out_dir = self._run(data_dir, tmp_path)

        assert code == 2, "exit 2 tells the workflow to send the connectivity email"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["dark-plant"]["send_count"] == 1
        assert "healthy-plant" not in state
        assert (out_dir / "connectivity.txt").exists()

    def test_second_run_inside_four_hours_is_held(self, data_dir, tmp_path):
        self._two_plant_fleet(data_dir)
        code, state_file, _ = self._run(data_dir, tmp_path)
        assert code == 2

        code, state_file, _ = self._run(data_dir, tmp_path)

        assert code == 0, "a repeat inside 4 hours must not mail again"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["dark-plant"]["send_count"] == 1

    def test_four_hours_later_it_sends_again(self, data_dir, tmp_path):
        self._two_plant_fleet(data_dir)
        self._run(data_dir, tmp_path)

        state_file = tmp_path / "state" / "connectivity_state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["dark-plant"]["last_sent"] = (datetime.now() - timedelta(hours=5)).isoformat()
        state_file.write_text(json.dumps(state), encoding="utf-8")

        code, state_file, _ = self._run(data_dir, tmp_path)

        assert code == 2
        assert json.loads(state_file.read_text(encoding="utf-8"))["dark-plant"]["send_count"] == 2

    def test_auto_ignored_after_three_sends(self, data_dir, tmp_path):
        """A customer who never fixes their WiFi cannot flood the inbox for ever."""
        self._two_plant_fleet(data_dir)
        state_file = tmp_path / "state" / "connectivity_state.json"

        for expected in (1, 2, 3):
            code, state_file, _ = self._run(data_dir, tmp_path)
            assert code == 2, f"send #{expected} should go out"
            state = json.loads(state_file.read_text(encoding="utf-8"))
            assert state["dark-plant"]["send_count"] == expected
            state["dark-plant"]["last_sent"] = (datetime.now() - timedelta(hours=5)).isoformat()
            state_file.write_text(json.dumps(state), encoding="utf-8")

        code, state_file, _ = self._run(data_dir, tmp_path)

        assert code == 0, "the 4th run must be silent"
        assert json.loads(state_file.read_text(encoding="utf-8"))["dark-plant"]["ignored"] is True

    def test_recovery_clears_the_state(self, data_dir, tmp_path):
        """Otherwise a plant that recovers stays auto-ignored and its next outage is silent."""
        self._two_plant_fleet(data_dir)
        _, state_file, _ = self._run(data_dir, tmp_path)
        assert "dark-plant" in json.loads(state_file.read_text(encoding="utf-8"))

        # The dongle comes back online.
        write_series(data_dir, "dark-plant", healthy_series())
        code, state_file, _ = self._run(data_dir, tmp_path)

        assert code == 0
        assert json.loads(state_file.read_text(encoding="utf-8")) == {}

    def test_state_file_is_readable_without_cross_referencing(self, data_dir, tmp_path):
        """UC7 convention: the state JSON names the customer and the plant."""
        self._two_plant_fleet(data_dir)
        _, state_file, _ = self._run(data_dir, tmp_path)

        entry = json.loads(state_file.read_text(encoding="utf-8"))["dark-plant"]

        assert entry["plant"] == "dark-plant"
        assert "customer" in entry
        assert entry["days_stale"] >= 3
        assert entry["kind"]

    def test_excluded_plant_raises_no_connectivity_alert(self, data_dir, tmp_path):
        write_series(data_dir, "healthy-plant", healthy_series())
        write_series(data_dir, "dropped-plant", {i: 10.0 for i in range(20, 9, -1)})
        write_exclusions(tmp_path / "ex.json",
                         [{"plant": "dropped-plant", "reason": "Off the platform",
                           "since": "2026-09-01"}])

        code, state_file, _ = self._run(data_dir, tmp_path)

        assert code == 0, "an excluded plant must not raise alerts"
        assert not state_file.exists() or json.loads(state_file.read_text(encoding="utf-8")) == {}

    def test_stale_days_flag_changes_the_verdict(self, data_dir, tmp_path):
        write_series(data_dir, "healthy-plant", healthy_series())
        series = {i: 10.0 for i in range(20, 4, -1)}
        series.update({i: 0.0 for i in range(4, -1, -1)})
        write_series(data_dir, "dark-plant", series)

        code, _, _ = self._run(data_dir, tmp_path, ["--stale-days", "30"])

        assert code == 0, "--stale-days must be honoured"

    def test_fleet_wide_outage_alerts_once_not_once_per_plant(self, data_dir, tmp_path):
        """A platform-side failure must not burn every customer's alert budget."""
        for name in ("plant-a", "plant-b", "plant-c"):
            write_series(data_dir, name, {i: 10.0 for i in range(20, 9, -1)})

        code, state_file, _ = self._run(data_dir, tmp_path)

        assert code == 2
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert list(state) == [check_connectivity.FLEET_KEY]
        assert state[check_connectivity.FLEET_KEY]["plants_down"] == 3


# --------------------------------------------------------------------------
# 5. The wording — the whole point of the exercise
# --------------------------------------------------------------------------

class TestConnectivityEmailWording:
    """
    A connectivity alert must read differently from a production alert.

    These are assertions about words because the words are the feature: the
    owner's complaint was that a dead WiFi link was reported to him as a dead
    solar system.
    """

    def _report(self, data_dir, tmp_path, plants=None):
        plants = plants or {
            "healthy-plant": healthy_series(),
            "dark-plant": {i: 10.0 for i in range(20, 9, -1)},
        }
        for name, series in plants.items():
            write_series(data_dir, name, series)
        creds = write_credentials(tmp_path / "creds.json",
                                  [{"label": "Dark", "username": "u", "password": "p",
                                    "email": "dark@example.com"}])
        sys.argv = [
            "check_connectivity.py",
            "--data-dir", str(data_dir),
            "--out-dir", str(tmp_path / "alerts"),
            "--state-file", str(tmp_path / "state" / "connectivity_state.json"),
            "--credentials", str(creds),
        ]
        check_connectivity.main()
        return (tmp_path / "alerts" / "connectivity.txt").read_text(encoding="utf-8")

    def test_email_says_it_is_not_a_production_fault(self, data_dir, tmp_path):
        text = self._report(data_dir, tmp_path).lower()

        assert "not a production fault" in text
        assert "data link" in text or "link down" in text

    def test_email_does_not_write_the_system_off_as_dead(self, data_dir, tmp_path):
        text = self._report(data_dir, tmp_path).lower()

        assert "do not record these as" in text or "not failed systems" in text
        assert "running normally" in text, \
            "the email must say the solar system is probably fine"

    def test_email_points_at_wifi_and_the_dongle(self, data_dir, tmp_path):
        text = self._report(data_dir, tmp_path).lower()

        assert "wifi" in text
        assert "dongle" in text

    def test_email_states_the_three_counts(self, data_dir, tmp_path):
        text = self._report(data_dir, tmp_path).lower()

        assert "plants reporting" in text
        assert "dead link" in text
        assert "plants excluded" in text

    def test_email_names_the_plant_and_how_long_it_has_been_silent(self, data_dir, tmp_path):
        text = self._report(data_dir, tmp_path)

        assert "dark-plant" in text
        assert "days ago" in text
        assert "Send #1/3" in text

    def test_fleet_wide_email_points_at_our_own_collector(self, data_dir, tmp_path):
        text = self._report(data_dir, tmp_path, plants={
            "plant-a": {i: 10.0 for i in range(20, 9, -1)},
            "plant-b": {i: 10.0 for i in range(20, 9, -1)},
        }).lower()

        assert "fleet-wide" in text
        assert "workflow" in text or "credentials" in text or "api" in text

    def test_all_clear_report_is_still_written(self, data_dir, tmp_path):
        text = self._report(data_dir, tmp_path, plants={"healthy-plant": healthy_series()})

        assert "ALL LINKS UP" in text or "sending data" in text


# --------------------------------------------------------------------------
# 6. Separation from the production alert (the headline regression)
# --------------------------------------------------------------------------

class TestProductionAlertsStayProduction:
    """check_anomaly.py must never dress a dead link up as a RED production alert."""

    def _run_anomaly(self, data_dir, tmp_path, extra_args=()):
        out_dir = tmp_path / "alerts"
        out_dir.mkdir(exist_ok=True)
        sys.argv = [
            "check_anomaly.py",
            "--data-dir", str(data_dir),
            "--out-dir", str(out_dir),
            "--state-file", str(tmp_path / "alerts_state.json"),
            *extra_args,
        ]
        from check_anomaly import main as check_anomaly_main
        code = check_anomaly_main()
        return code, (out_dir / "alerts.txt").read_text(encoding="utf-8"), \
            json.loads((out_dir / "alerts.json").read_text(encoding="utf-8"))

    def test_dead_link_does_not_raise_a_red_alert(self, data_dir, tmp_path):
        """
        THE regression this whole change exists for.

        Normal production for a fortnight, then nothing at all. The old code saw
        "< 20% of baseline for 3 days" and told the owner the system had failed.
        It had not — the dongle went offline.
        """
        series = {i: 10.0 for i in range(17, 3, -1)}
        series.update({3: 0.0, 2: 0.0, 1: 0.0, 0: 0.0})
        write_series(data_dir, "dark-plant", series)

        code, text, payload = self._run_anomaly(data_dir, tmp_path)

        red_plants = [a["plant_key"] for a in payload["alerts"] if a["severity"] == "RED"]
        assert "dark-plant" not in red_plants, \
            "a plant with a dead data link must not be reported as a production failure"
        assert any(s["plant_key"] == "dark-plant" and s.get("link_down")
                   for s in payload["suppressed"]), \
            "it must be recorded as a connectivity reclassification, not silently dropped"
        assert "NOT A PRODUCTION FAULT" in text

    def test_a_reporting_plant_still_raises_red(self, data_dir, tmp_path):
        """
        The counterpart: the connectivity rule must not muffle real faults.
        This plant is still talking to us, it is just producing almost nothing.
        """
        series = {i: 10.0 for i in range(17, 2, -1)}
        series.update({2: 1.5, 1: 1.5, 0: 1.5})
        write_series(data_dir, "failing-plant", series)

        code, text, payload = self._run_anomaly(data_dir, tmp_path)

        red_plants = [a["plant_key"] for a in payload["alerts"] if a["severity"] == "RED"]
        assert "failing-plant" in red_plants
        assert code == 2

    def test_report_lists_dead_links_as_connectivity(self, data_dir, tmp_path):
        write_series(data_dir, "healthy-plant", healthy_series())
        write_series(data_dir, "dark-plant", {i: 10.0 for i in range(20, 9, -1)})

        _, text, payload = self._run_anomaly(data_dir, tmp_path)

        assert "DATA LINK DOWN" in text
        assert "dark-plant" in text
        assert payload["counts"]["link_down"] == 1
        assert payload["counts"]["reporting"] == 1

    def test_header_states_the_three_counts(self, data_dir, tmp_path):
        write_series(data_dir, "healthy-plant", healthy_series())
        write_series(data_dir, "dark-plant", {i: 10.0 for i in range(20, 9, -1)})
        write_exclusions(tmp_path / "ex.json",
                         [{"plant": "gone-plant", "reason": "Dropped", "since": "2026-09-01"}])

        _, text, _ = self._run_anomaly(data_dir, tmp_path)

        assert "Reporting: 1" in text
        assert "Dead link: 1" in text
        assert "Excluded: 1" in text

    def test_excluded_plant_is_skipped_entirely(self, data_dir, tmp_path):
        """An excluded plant raises no alert even when it looks catastrophic."""
        series = {i: 10.0 for i in range(17, 2, -1)}
        series.update({2: 1.5, 1: 1.5, 0: 1.5})
        write_series(data_dir, "dropped-plant", series)
        write_exclusions(tmp_path / "ex.json",
                         [{"plant": "dropped-plant", "reason": "Off the platform",
                           "since": "2026-09-01"}])

        code, text, payload = self._run_anomaly(data_dir, tmp_path)

        assert payload["alerts"] == []
        assert code == 0
        assert "EXCLUDED PLANTS" in text
        assert "Off the platform" in text, "the reason must be visible, not just the absence"

    def test_ignored_plant_says_why_it_is_silent(self, data_dir, tmp_path):
        """
        A month of zeros used to be filed under "ignored" with no explanation.
        It should say the link is down and that this is not a production fault.
        """
        write_series(data_dir, "silent-plant", {i: 0.0 for i in range(40, -1, -1)})

        _, text, payload = self._run_anomaly(data_dir, tmp_path, ["--ignore-zero-months", "1"])

        ignored = [x for x in payload["ignored"] if x["plant_key"] == "silent-plant"]
        assert ignored, "the zero-month ignore rule must still apply"
        assert ignored[0]["link_down"] is True
        assert "not a production fault" in text.lower()


# --------------------------------------------------------------------------
# 7. Admin summary and device alarms
# --------------------------------------------------------------------------

class TestAdminSummaryCoverage:
    """Requirement 3: the admin summary states the three counts plainly."""

    def _summary(self, data_dir, tmp_path, accounts):
        creds = write_credentials(tmp_path / "creds.json", accounts)
        out = tmp_path / "admin_summary.txt"
        from generate_admin_summary import generate_admin_summary
        result = generate_admin_summary(str(creds), str(data_dir), str(out))
        return result, out.read_text(encoding="utf-8")

    def test_summary_states_reporting_dead_link_and_excluded(self, data_dir, tmp_path):
        write_series(data_dir, "alpha-plant", healthy_series())
        write_series(data_dir, "beta-plant", {i: 10.0 for i in range(20, 9, -1)})
        write_series(data_dir, "gamma-plant", healthy_series())
        write_exclusions(tmp_path / "ex.json",
                         [{"plant": "gamma-plant", "customer": "Gamma",
                           "reason": "Link never restored", "since": "2026-09-01"}])

        result, text = self._summary(data_dir, tmp_path, [
            {"label": "Alpha", "username": "a", "password": "p", "email": "a@example.com"},
            {"label": "Beta", "username": "b", "password": "p", "email": "b@example.com"},
            {"label": "Gamma", "username": "g", "password": "p", "email": "g@example.com"},
        ])

        assert result["plants_reporting"] == 1
        assert result["plants_link_down"] == 1
        assert result["plants_excluded"] == 1
        assert "PLATFORM COVERAGE" in text
        assert "Plants reporting:" in text
        assert "Plants with a dead link:" in text
        assert "Plants excluded:" in text

    def test_summary_explains_a_dead_link_is_not_a_fault(self, data_dir, tmp_path):
        write_series(data_dir, "alpha-plant", healthy_series())
        write_series(data_dir, "beta-plant", {i: 10.0 for i in range(20, 9, -1)})

        _, text = self._summary(data_dir, tmp_path, [
            {"label": "Alpha", "username": "a", "password": "p", "email": "a@example.com"},
            {"label": "Beta", "username": "b", "password": "p", "email": "b@example.com"},
        ])

        assert "not a fault" in text.lower() or "NOT a fault" in text
        assert "beta-plant" in text, "the dead-link plant must be named"

    def test_excluded_customer_absent_from_the_breakdown(self, data_dir, tmp_path):
        write_series(data_dir, "alpha-plant", healthy_series())
        write_series(data_dir, "gamma-plant", healthy_series())
        write_exclusions(tmp_path / "ex.json",
                         [{"customer": "Gamma", "reason": "Dropped", "since": "2026-09-01"}])

        result, text = self._summary(data_dir, tmp_path, [
            {"label": "Alpha", "username": "a", "password": "p", "email": "a@example.com"},
            {"label": "Gamma", "username": "g", "password": "p", "email": "g@example.com"},
        ])

        assert result["customers_count"] == 1
        assert "EXCLUDED PLANTS" in text
        assert "Dropped" in text, "the exclusion must be visible, not a silent gap"


class TestDeviceAlarmsRespectExclusions:
    """Requirement 2: excluded plants are skipped by alarms too."""

    def test_excluded_customers_alarms_are_dropped(self, tmp_path):
        from generate_device_alarms import drop_excluded_alarms
        write_exclusions(tmp_path / "ex.json",
                         [{"customer": "Dropped-Customer", "reason": "Off the platform",
                           "since": "2026-09-01"}])

        alarms = [
            {"customer_label": "Dropped-Customer", "plant": "Their Plant", "desc": "Fault"},
            {"customer_label": "Active-Customer", "plant": "Good Plant", "desc": "Fault"},
        ]
        kept, dropped = drop_excluded_alarms(alarms)

        assert len(kept) == 1
        assert kept[0]["customer_label"] == "Active-Customer"
        assert dropped[0]["reason"] == "Off the platform"

    def test_nothing_is_dropped_when_nothing_is_excluded(self, tmp_path):
        from generate_device_alarms import drop_excluded_alarms
        write_exclusions(tmp_path / "ex.json", [])

        alarms = [{"customer_label": "Active-Customer", "plant": "Good Plant", "desc": "Fault"}]
        kept, dropped = drop_excluded_alarms(alarms)

        assert len(kept) == 1
        assert dropped == []


class TestWeeklyReportsRespectExclusions:
    """Requirement 2: excluded plants are skipped by the customer reports."""

    def test_excluded_customer_gets_no_weekly_report(self, data_dir, tmp_path):
        import generate_weekly_report
        write_series(data_dir, "alpha-plant", healthy_series())
        write_series(data_dir, "gamma-plant", healthy_series())
        write_exclusions(tmp_path / "ex.json",
                         [{"customer": "Gamma", "reason": "Dropped", "since": "2026-09-01"}])
        creds = write_credentials(tmp_path / "creds.json", [
            {"label": "Alpha", "username": "a", "password": "p", "email": "a@example.com"},
            {"label": "Gamma", "username": "g", "password": "p", "email": "g@example.com"},
        ])
        reports = tmp_path / "reports"

        sys.argv = [
            "generate_weekly_report.py",
            "--credentials", str(creds),
            "--data-dir", str(data_dir),
            "--output-dir", str(reports),
            "--alerts-state", str(tmp_path / "nonexistent_state.json"),
        ]
        generate_weekly_report.main()

        produced = {p.name for p in reports.glob("*.txt")}
        assert any("alpha" in name for name in produced)
        assert not any("gamma" in name for name in produced), \
            "an excluded customer must not receive a weekly report"


# --------------------------------------------------------------------------
# 8. Wiring (BVT)
# --------------------------------------------------------------------------

class TestWorkflowWiring:
    """BVT: the check runs in CI and its email is feature-flag gated."""

    def test_connectivity_flags_exist(self):
        data = json.loads((CONFIG_DIR / "features.json").read_text(encoding="utf-8"))
        assert "connectivity_admin" in data["emails"]
        assert "connectivity_link_down" in data["alerts"]

    def test_connectivity_step_runs_in_both_platform_workflows(self):
        for name in ("trigger-shinemonitor.yml", "trigger-dessmonitor.yml"):
            doc = yaml.safe_load((WORKFLOWS_DIR / name).read_text(encoding="utf-8"))
            runs = [str(step.get("run", ""))
                    for job in doc["jobs"].values()
                    for step in (job.get("steps") or [])]
            assert any("check_connectivity.py" in r for r in runs), \
                f"{name} must run the connectivity check"

    def test_connectivity_email_is_gated_by_its_flag(self):
        """The repo's rule: no email step without a flag condition."""
        for name in ("trigger-shinemonitor.yml", "trigger-dessmonitor.yml"):
            doc = yaml.safe_load((WORKFLOWS_DIR / name).read_text(encoding="utf-8"))
            for job in doc["jobs"].values():
                for step in (job.get("steps") or []):
                    if "connectivity.txt" not in str(step.get("run", "")):
                        continue
                    assert "emails_connectivity_admin" in str(step.get("if", "")), \
                        f"{name}: the connectivity email must be gated by its feature flag"

    def test_connectivity_uses_centralized_config(self):
        content = (SCRIPTS_DIR / "check_connectivity.py").read_text(encoding="utf-8")
        assert "from config import CREDENTIALS_PATH" in content
        assert 'Path("src/main/java/org/ktronics/config/credentials.json")' not in content

    def test_exclusions_module_is_the_single_source_of_truth(self):
        """Only exclusions.py may know where the exclusion list lives."""
        for py_file in SCRIPTS_DIR.glob("*.py"):
            if py_file.name == "exclusions.py":
                continue
            content = py_file.read_text(encoding="utf-8")
            assert "excluded_plants.json" not in content or "import exclusions" in content, \
                f"{py_file.name} must go through exclusions.py, not the file directly"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
