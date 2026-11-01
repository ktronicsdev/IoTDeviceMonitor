"""
The data committed to main must agree with itself.

test_fetch_never_destroys_data.py proves the fetch scripts behave against a
fake vendor. This reads the real CSVs, so it also catches damage from a path
nobody has thought of yet. The `tests` job runs on the hourly schedule as well
as on a push, so a failure here reaches the admin as a TESTS FAILED email
within hours of the bad data being committed.

Each month is recorded twice, by two different vendor calls: the plant's daily
rows and its yearly row. When one says a month produced and the other says it
did not, one of them lost data. Both directions have really happened:

  * Daily rows all zero, yearly row real. Troy's two plants share a filename,
    and the dead one's zeros stayed in the file whenever the live one's call
    failed, so a healthy plant was mailed DATA LINK DOWN 9 times in 12 days.
  * Yearly row zero, daily rows real. A failed call wrote kwh=0. On 2026-11-01
    that was 54 months across 17 plants and 9,132 kWh, in the totals customers
    are sent.

Daily files only go back to 2025-12, and 31 of those 54 rows were older than
that. For such a month there is no second record, so the check falls back to the
shape the damage leaves: a zero between two producing months. That rule is only
safe there. A plant really can be off for a whole month, and that will show as
zero both ways in the months that do have daily files.

If a test here fails, do not edit the CSV to make it pass. Find what wrote it:
`git log -p -- data/<file>`. The next successful fetch repairs the row itself.
"""

import csv
import json
import re
from datetime import date, timedelta
from pathlib import Path

import yaml

DATA_REL = Path("data")
MONTHLY_CSV = re.compile(r"^(?P<plant>.+)-(?P<month>\d{4}-\d{2})\.csv$")
YEARLY_CSV = re.compile(r"^(?P<plant>.+)-(?P<year>\d{4})\.csv$")

# Below this the two vendor calls can honestly disagree through rounding.
MIN_KWH = 1.0

# A new month's first days can legitimately disagree: one call may have failed
# on the only run so far. connectivity.py gives a plant the same 3 days.
GRACE_DAYS = 3

# Both neighbours must clear this before a zero between them looks like damage.
# A fresh vendor read of the whole fleet has zeros next to near-dead months
# (3.6 and 8.4 kWh, 120.5 and 23.9 kWh) and none at all above this.
PRODUCING_KWH = 30.0


def _repo_root():
    """Walk up to the repo root. Counting .parent lands outside the tree."""
    for parent in Path(__file__).resolve().parents:
        if (parent / DATA_REL).is_dir() and (parent / ".github").is_dir():
            return parent
    raise AssertionError(f"could not find {DATA_REL} above {__file__}")


def _read_figures(path):
    """{first column: kwh} for one CSV; a blank or bad cell counts as 0."""
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    figures = {}
    for row in rows[1:]:
        if len(row) < 2:
            continue
        try:
            figures[row[0]] = float(row[1])
        except ValueError:
            figures[row[0]] = 0.0
    return figures


def load_fleet(data_dir):
    """{(plant, 'YYYY-MM'): sum of daily rows} and the same for yearly rows."""
    daily, yearly = {}, {}
    for path in sorted(data_dir.glob("*.csv")):
        match = MONTHLY_CSV.match(path.name)
        if match:
            daily[(match["plant"], match["month"])] = sum(
                _read_figures(path).values())
            continue
        match = YEARLY_CSV.match(path.name)
        if match:
            for month, kwh in _read_figures(path).items():
                yearly[(match["plant"], month)] = kwh
    return daily, yearly


def months_to_check(daily, yearly, today):
    """Months held both ways, leaving out one that has only just begun."""
    newest = (today - timedelta(days=GRACE_DAYS)).strftime("%Y-%m")
    return sorted(key for key in daily if key in yearly and key[1] <= newest)


def daily_rows_lost(daily, yearly, today):
    """Yearly row says it produced; the daily rows are all zero (the Troy bug)."""
    return [(plant, month, yearly[(plant, month)])
            for plant, month in months_to_check(daily, yearly, today)
            if daily[(plant, month)] == 0 and yearly[(plant, month)] >= MIN_KWH]


def yearly_row_lost(daily, yearly, today):
    """Daily rows say it produced; the yearly row is zero (the failed-call bug)."""
    return [(plant, month, round(daily[(plant, month)], 1))
            for plant, month in months_to_check(daily, yearly, today)
            if yearly[(plant, month)] == 0 and daily[(plant, month)] >= MIN_KWH]


def _next_month(month, step):
    """'2026-01', -1 -> '2025-12'."""
    year, number = map(int, month.split("-"))
    index = year * 12 + (number - 1) + step
    return f"{index // 12}-{index % 12 + 1:02d}"


def older_month_zeroed(daily, yearly, today):
    """No daily file to compare with, and a zero between two producing months."""
    newest = (today - timedelta(days=GRACE_DAYS)).strftime("%Y-%m")
    lost = []
    for (plant, month), kwh in sorted(yearly.items()):
        if kwh != 0 or month > newest or (plant, month) in daily:
            continue
        before = yearly.get((plant, _next_month(month, -1)), 0)
        after = yearly.get((plant, _next_month(month, 1)), 0)
        if before >= PRODUCING_KWH and after >= PRODUCING_KWH:
            lost.append((plant, month, kwh))
    return lost


def _report(lost):
    return "\n".join(f"  {plant}  {month}  ({kwh} kWh)" for plant, month, kwh in lost)


# --- the real data ----------------------------------------------------------


def test_the_check_is_looking_at_real_data():
    """Guards the two below: a broken loader would pass them by finding nothing."""
    daily, yearly = load_fleet(_repo_root() / DATA_REL)
    checked = months_to_check(daily, yearly, date.today())
    assert len(checked) >= 20, (
        f"only {len(checked)} months could be compared. Have the data files "
        f"been renamed, or has the retention step started deleting them?")


def test_no_month_has_lost_its_daily_rows():
    daily, yearly = load_fleet(_repo_root() / DATA_REL)
    lost = daily_rows_lost(daily, yearly, date.today())
    assert not lost, (
        "These months read all zero day by day, yet the yearly row shows they "
        "produced. Connectivity will mail them as DATA LINK DOWN. Check for two "
        "plants sharing a filename, and see the guard in "
        "check_shinemonitor_monthly.sh:\n" + _report(lost))


def test_no_month_has_lost_its_yearly_row():
    daily, yearly = load_fleet(_repo_root() / DATA_REL)
    lost = yearly_row_lost(daily, yearly, date.today())
    assert not lost, (
        "These months have real daily rows, yet the yearly row reads 0, so the "
        "yearly totals in customer reports are too low. See the month loop in "
        "check_shinemonitor_yearly.sh:\n" + _report(lost))


def test_no_older_month_has_been_zeroed():
    daily, yearly = load_fleet(_repo_root() / DATA_REL)
    lost = older_month_zeroed(daily, yearly, date.today())
    assert not lost, (
        "These months read 0 between two producing months and have no daily file "
        "to check them against. That is what a failed call used to leave. See the "
        "month loop in check_shinemonitor_yearly.sh:\n" + _report(lost))


# --- the alarm reaches someone ----------------------------------------------


def _shinemonitor_workflow():
    path = _repo_root() / ".github" / "workflows" / "trigger-shinemonitor.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_tests_job_runs_on_the_schedule_as_well_as_on_a_push():
    """
    A data run commits without triggering a push build, so damaged data is only
    ever read by a scheduled `tests` job. Limit that job to pushes and every
    check in this file goes quiet.
    """
    workflow = _shinemonitor_workflow()
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = workflow[True]
    assert triggers.get("schedule"), "the workflow has no schedule any more"
    assert "push" in triggers
    assert "if" not in workflow["jobs"]["tests"], (
        "the tests job has been made conditional; a scheduled run may no longer "
        "read the committed data, so damage would go unreported")


def test_a_failure_is_emailed_and_names_the_test_that_failed():
    """Otherwise the email only says something is wrong, not what."""
    steps = _shinemonitor_workflow()["jobs"]["tests"]["steps"]

    run_step = next(s for s in steps if s.get("name") == "Run integration tests")
    assert "pytest-output.txt" in run_step["run"], (
        "pytest's output is no longer saved for the email to read")

    email = next(s for s in steps if s.get("name") == "Email test regression to admin")
    assert "failure()" in email["if"]
    assert "pytest-output.txt" in email["run"] and "FAILED" in email["run"], (
        "the failure email no longer lists which tests failed")

    flags = json.loads((_repo_root() / "src/main/java/org/ktronics/config/features.json")
                       .read_text(encoding="utf-8"))
    assert flags["emails"]["test_regression"] is True, (
        "emails.test_regression is off, so nobody hears about a failure here")


# --- the check itself -------------------------------------------------------
#
# A test that can only ever pass tells nobody anything, so prove each one fires
# on the shape of the real damage.


def _write(data_dir, name, header, rows):
    lines = [header] + [f"{k},{v}" for k, v in rows]
    (data_dir / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


TODAY = date(2026, 11, 20)


def test_fires_on_the_troy_wipe(tmp_path):
    _write(tmp_path, "troy-2026-10.csv", "date,kwh",
           [("2026-10-01", "0.0000"), ("2026-10-02", "0.0000")])
    _write(tmp_path, "troy-2026.csv", "month,kwh", [("2026-10", "341.1000")])
    daily, yearly = load_fleet(tmp_path)
    assert daily_rows_lost(daily, yearly, TODAY) == [("troy", "2026-10", 341.1)]


def test_fires_on_a_zeroed_yearly_row(tmp_path):
    _write(tmp_path, "troy-2026-09.csv", "date,kwh", [("2026-09-01", "349.5000")])
    _write(tmp_path, "troy-2026.csv", "month,kwh", [("2026-09", "0")])
    daily, yearly = load_fleet(tmp_path)
    assert yearly_row_lost(daily, yearly, TODAY) == [("troy", "2026-09", 349.5)]


def test_fires_on_a_zeroed_month_with_no_daily_file(tmp_path):
    """Troy's 2025-03 read like this. 2025-01 proves the lookup crosses a year."""
    _write(tmp_path, "troy-2024.csv", "month,kwh", [("2024-12", "214.3000")])
    _write(tmp_path, "troy-2025.csv", "month,kwh",
           [("2025-01", "0"), ("2025-02", "299.0000"), ("2025-03", "0"),
            ("2025-04", "390.3000")])
    daily, yearly = load_fleet(tmp_path)
    assert older_month_zeroed(daily, yearly, TODAY) == [
        ("troy", "2025-01", 0.0), ("troy", "2025-03", 0.0)]


def test_a_real_outage_with_a_daily_file_is_not_called_damage(tmp_path):
    """A daily file proves the month really read zero, so the zero stands."""
    _write(tmp_path, "troy-2026-08.csv", "date,kwh", [("2026-08-01", "0.0000")])
    _write(tmp_path, "troy-2026.csv", "month,kwh",
           [("2026-07", "425.2000"), ("2026-08", "0"), ("2026-09", "349.5000")])
    daily, yearly = load_fleet(tmp_path)
    assert older_month_zeroed(daily, yearly, TODAY) == []
    assert yearly_row_lost(daily, yearly, TODAY) == []


def test_a_truly_dead_plant_is_not_reported(tmp_path):
    """Zero both ways is what a dead plant looks like; the records agree."""
    _write(tmp_path, "dead-2026-10.csv", "date,kwh", [("2026-10-01", "0.0000")])
    _write(tmp_path, "dead-2026.csv", "month,kwh", [("2026-10", "0.0000")])
    daily, yearly = load_fleet(tmp_path)
    assert daily_rows_lost(daily, yearly, TODAY) == []
    assert yearly_row_lost(daily, yearly, TODAY) == []


def test_a_month_that_has_only_just_begun_is_left_alone(tmp_path):
    """One failed call on the 1st is not damage yet; it is 3 days later."""
    _write(tmp_path, "troy-2026-11.csv", "date,kwh", [("2026-11-01", "0.0000")])
    _write(tmp_path, "troy-2026.csv", "month,kwh", [("2026-11", "12.0000")])
    daily, yearly = load_fleet(tmp_path)
    assert daily_rows_lost(daily, yearly, date(2026, 11, 2)) == []
    assert daily_rows_lost(daily, yearly, date(2026, 11, 4)) == [
        ("troy", "2026-11", 12.0)]
