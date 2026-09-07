#!/usr/bin/env python3
"""
Fleet benchmark — integration tests.

No network. Builds synthetic fleets on disk and checks the parts that decide
whether a customer dispute is answered correctly: the weather index, the
partial-day guard, and the expectation baseline.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[6]
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fleet_benchmark as fb  # noqa: E402


def write_csv(path, rows, header="date,kwh"):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{d},{v}" for d, v in rows)
    path.write_text(f"{header}\n{body}\n", encoding="utf-8")


def make_fleet(tmp_path, plants, month="2026-08"):
    """plants: {name: {date: kwh}} -> monthly CSVs in a data dir."""
    data = tmp_path / "data"
    for name, series in plants.items():
        write_csv(data / f"{name}-{month}.csv", sorted(series.items()))
    return data


# --------------------------------------------------------------------------- #
# CSV reading
# --------------------------------------------------------------------------- #
class TestReadDailyCsv:
    def test_reads_date_and_kwh(self, tmp_path):
        f = tmp_path / "p.csv"
        write_csv(f, [("2026-08-15", 6.1), ("2026-08-16", 5.0)])
        assert fb.read_daily_csv(f) == {"2026-08-15": 6.1, "2026-08-16": 5.0}

    def test_accepts_energy_kwh_column(self, tmp_path):
        # A portal export may not use the repo's own column name.
        f = tmp_path / "p.csv"
        write_csv(f, [("2026-08-15", 6.1)], header="date,energy_kwh")
        assert fb.read_daily_csv(f) == {"2026-08-15": 6.1}

    def test_trims_timestamp_to_date(self, tmp_path):
        f = tmp_path / "p.csv"
        write_csv(f, [("2026-08-15 13:45:00", 6.1)])
        assert fb.read_daily_csv(f) == {"2026-08-15": 6.1}

    def test_skips_unparseable_rows(self, tmp_path):
        f = tmp_path / "p.csv"
        write_csv(f, [("2026-08-15", "n/a"), ("2026-08-16", 5.0)])
        assert fb.read_daily_csv(f) == {"2026-08-16": 5.0}


# --------------------------------------------------------------------------- #
# Fleet loading
# --------------------------------------------------------------------------- #
class TestLoadFleet:
    def test_ignores_yearly_files(self, tmp_path):
        # Yearly files are month,kwh — reading them as daily rows would inject
        # 12 bogus "days" per plant and wreck the index.
        data = tmp_path / "data"
        write_csv(data / "plant-a-2026-08.csv", [("2026-08-15", 6.0)])
        write_csv(data / "plant-a-2026.csv", [("2026-08", 180.0)], header="month,kwh")
        fleet = fb.load_fleet(data, "2026-08-01", "2026-08-31")
        assert fleet == {"plant-a": {"2026-08-15": 6.0}}

    def test_strips_period_suffix_to_group_plants(self, tmp_path):
        data = tmp_path / "data"
        write_csv(data / "plant-a-2026-08.csv", [("2026-08-31", 6.0)])
        write_csv(data / "plant-a-2026-09.csv", [("2026-09-01", 7.0)])
        fleet = fb.load_fleet(data, "2026-08-01", "2026-09-30")
        assert set(fleet) == {"plant-a"}
        assert len(fleet["plant-a"]) == 2

    def test_window_is_respected(self, tmp_path):
        data = make_fleet(tmp_path, {"p": {"2026-08-14": 5.0, "2026-08-15": 6.0}})
        assert list(fb.load_fleet(data, "2026-08-15", "2026-08-31")["p"]) == ["2026-08-15"]


# --------------------------------------------------------------------------- #
# The weather index
# --------------------------------------------------------------------------- #
class TestFleetIndex:
    def test_normalises_per_plant_so_size_does_not_matter(self):
        # A 10 kW and a 3 kW plant both at half their best must give 50%, not a
        # number dominated by the larger array.
        series = {"big": {"d1": 40.0, "d2": 20.0}, "small": {"d1": 6.0, "d2": 3.0}}
        index, _, n = fb.fleet_index(series)
        assert n == 2
        assert index["d1"] == pytest.approx(100)
        assert index["d2"] == pytest.approx(50)

    def test_excludes_dead_plants_rather_than_scoring_them_zero(self):
        # An ignored/dead plant counted as 0% would fake a cloudy day — the
        # direction that loses the argument with a customer.
        series = {"live": {"d1": 10.0, "d2": 5.0}, "dead": {"d1": 0.0, "d2": 0.0}}
        index, _, n = fb.fleet_index(series)
        assert n == 1
        assert index["d2"] == pytest.approx(50)

    def test_counts_reporting_plants_per_day(self):
        series = {"a": {"d1": 10.0, "d2": 8.0}, "b": {"d1": 10.0}}
        _, counts, _ = fb.fleet_index(series)
        assert counts["d1"] == 2 and counts["d2"] == 1


# --------------------------------------------------------------------------- #
# Partial-day guard
# --------------------------------------------------------------------------- #
class TestPartialDays:
    def test_today_is_partial(self):
        # Collection runs mid-day, so today's rows are half-filled and would
        # read as a cloudy day that never happened.
        index = {"2026-09-06": 84.0, "2026-09-07": 57.0}
        assert fb.partial_days(index, {}, today="2026-09-07") == ["2026-09-07"]

    def test_stale_data_drops_nothing(self):
        index = {"2026-09-05": 84.0, "2026-09-06": 88.0}
        assert fb.partial_days(index, {}, today="2026-09-08") == []

    def test_plant_count_alone_cannot_detect_it(self):
        # Regression: the first cut looked for days where not every plant had
        # reported. Every plant gets a row the moment collection runs, so the
        # counts are identical and the partial day sailed through.
        index = {"2026-09-06": 84.0, "2026-09-07": 57.0}
        counts = {"2026-09-06": 17, "2026-09-07": 17}
        assert fb.partial_days(index, counts, today="2026-09-07") == ["2026-09-07"]


# --------------------------------------------------------------------------- #
# Expectation baseline
# --------------------------------------------------------------------------- #
class TestEstimateCapability:
    def test_self_calibration_does_not_invent_a_shortfall(self):
        # A healthy plant tracking the weather exactly must show ~zero shortfall.
        # Regression: multiplying the *relative* fleet index by an absolute
        # kWh/kWp constant mixed two baselines and charged a sound 3 kWp plant
        # with a 3.6 kWh/day loss.
        index = {"d1": 100.0, "d2": 80.0, "d3": 60.0}
        target = {"d1": 8.0, "d2": 6.4, "d3": 4.8}
        cap = fb.estimate_capability(index, target)
        rows = fb.build_report(index, target, cap)
        assert all(abs(r["shortfall"]) < 0.1 for r in rows)

    def test_clamped_plant_shows_a_loss_on_the_best_days(self):
        # Fleet has a good week; the plant is pinned at a ceiling regardless.
        index = {"d1": 90.0, "d2": 85.0, "d3": 88.0, "d4": 45.0}
        target = {"d1": 6.0, "d2": 6.0, "d3": 6.0, "d4": 6.0}
        rows = fb.build_report(index, target, fb.estimate_capability(index, target))
        # Only the best-weather day is a reliable signal. On a dull day a
        # clamped plant looks like it OVER-performs, because its ceiling sits
        # above what the weather alone would have produced - which is precisely
        # why the cumulative figure cannot be trusted here.
        best = max(rows, key=lambda r: r["index"])
        worst = min(rows, key=lambda r: r["index"])
        assert best["shortfall"] > 0
        assert worst["shortfall"] < 0

    def test_self_calibration_understates_a_fully_clamped_plant(self):
        # Known limitation, documented so nobody trusts the number blindly: a
        # plant clamped across the entire window never demonstrates its ceiling,
        # so the self-calibrated shortfall is a floor, not the true loss. This is
        # why weather_correlation() exists and why --kwp remains available.
        index = {"d1": 90.0, "d2": 85.0, "d3": 88.0, "d4": 45.0}
        target = dict.fromkeys(index, 6.0)
        rows = fb.build_report(index, target, fb.estimate_capability(index, target))
        true_capability = 16.0                      # what the array can really do
        true_loss = sum(true_capability * r["index"] / 100 - 6.0 for r in rows)
        assert sum(r["shortfall"] for r in rows) < true_loss

    def test_ignores_days_the_target_never_reported(self):
        index = {"d1": 100.0, "d2": 80.0}
        assert fb.estimate_capability(index, {"d1": 8.0}) == pytest.approx(8.0)

    def test_returns_none_with_no_overlap(self):
        assert fb.estimate_capability({"d1": 100.0}, {"d9": 5.0}) is None

    def test_percentile_resists_a_single_freak_day(self):
        # Cloud-edge enhancement can briefly beat clear sky; one such day must
        # not become the yardstick every other day is judged against.
        index = {f"d{i}": 80.0 for i in range(10)}
        target = {f"d{i}": 8.0 for i in range(10)}
        target["d9"] = 30.0
        assert fb.estimate_capability(index, target) < 15.0


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #
class TestBuildReport:
    def test_shortfall_is_expected_minus_actual(self):
        rows = fb.build_report({"d1": 50.0}, {"d1": 3.0}, capability=10.0)
        assert rows[0]["expected"] == pytest.approx(5.0)
        assert rows[0]["shortfall"] == pytest.approx(2.0)

    def test_missing_target_day_leaves_gaps_not_zeros(self):
        # A day the plant did not report is unknown, not a total loss.
        rows = fb.build_report({"d1": 50.0}, {}, capability=10.0)
        assert rows[0]["actual"] is None and rows[0]["shortfall"] is None

    def test_rows_are_date_ordered(self):
        index = {"2026-08-17": 70.0, "2026-08-15": 80.0, "2026-08-16": 75.0}
        rows = fb.build_report(index, {}, capability=10.0)
        assert [r["date"] for r in rows] == sorted(index)


# --------------------------------------------------------------------------- #
# HTML output
# --------------------------------------------------------------------------- #
class TestRenderHtml:
    def _rows(self):
        index = {"2026-09-01": 80.0, "2026-09-02": 90.0}
        target = {"2026-09-01": 6.0, "2026-09-02": 6.2}
        return fb.build_report(index, target, fb.estimate_capability(index, target))

    def test_is_self_contained(self):
        html = fb.render_html(self._rows(), "Surath 5KV", 17, 7.0, "test basis")
        assert "<svg" in html and "Surath 5KV" in html
        # Must open from a file on a customer's phone with no network.
        assert "http://" not in html and "https://" not in html

    def test_handles_a_missing_day_without_crashing(self):
        rows = fb.build_report({"2026-09-01": 80.0, "2026-09-02": 90.0},
                               {"2026-09-01": 6.0}, capability=7.0)
        assert "<svg" in fb.render_html(rows, "P", 3, 7.0, "basis")


# --------------------------------------------------------------------------- #
# Weather correlation - the number that settles a dispute
# --------------------------------------------------------------------------- #
class TestWeatherCorrelation:
    def test_healthy_plant_tracks_the_weather(self):
        index = {"d1": 90.0, "d2": 60.0, "d3": 75.0, "d4": 85.0}
        target = {"d1": 9.0, "d2": 6.0, "d3": 7.5, "d4": 8.5}
        assert fb.weather_correlation(index, target) > 0.9

    def test_clamped_plant_does_not(self):
        # Output barely moves while the sky swings - this is what an export
        # block, a hard limit or a clipped inverter looks like.
        index = {"d1": 90.0, "d2": 60.0, "d3": 75.0, "d4": 85.0, "d5": 65.0}
        target = {"d1": 6.0, "d2": 6.1, "d3": 5.9, "d4": 6.0, "d5": 6.1}
        r = fb.weather_correlation(index, target)
        assert r is not None and r < fb.WEATHER_FOLLOWING_R

    def test_none_when_too_few_days(self):
        assert fb.weather_correlation({"d1": 90.0}, {"d1": 6.0}) is None

    def test_none_when_output_is_perfectly_flat(self):
        # Zero variance cannot be correlated; must not raise.
        index = {"d1": 90.0, "d2": 60.0, "d3": 75.0}
        assert fb.weather_correlation(index, dict.fromkeys(index, 6.0)) is None
