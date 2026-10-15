"""
The hourly run polls alarms. It must not analyse CSVs.

Issue #10 added an hourly cron so the six-hour gate's "six hours" would be
literal instead of six-to-ten. The CSV fetch and the retention cleanup were
correctly guarded to skip on that run — but `check_anomaly.py` and
`check_connectivity.py` were not, so from 2026-10-11 they ran every hour
against whatever CSVs happened to be committed, with no fresh data.

What that produced, in production:

  * the hourly run computed one fleet view, the 4-hourly run computed another,
    and each overwrote the other's state - connectivity_state.json and
    admin_email_state.txt alternated between exactly two values;
  * UC9 reads any hash change as "something happened", so the admin got
    "ALL SYSTEMS OPERATIONAL" and RED alert mail several times a day;
  * sohan-must-pv1800-3024-pro had its connectivity entry deleted and recreated
    every few hours, so its 3-send counter reset to 1 forever and it mailed
    indefinitely.

The giveaway was that every "wrong" commit was byte-identical rather than
varied: two deterministic code paths, not a race.

So: a step that reads `data/` belongs to the data cycle and must carry the
same hourly-skip guard the fetch steps carry. A step that polls the vendor's
alarm API belongs to the alarm cycle and must not.
"""

from pathlib import Path

import pytest
import yaml

def _workflows_dir():
    """Walk up to the repo root. Counting .parent here has been wrong before."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".github" / "workflows"
        if candidate.is_dir():
            return candidate
    raise AssertionError(f"could not find .github/workflows above {__file__}")


WORKFLOWS = _workflows_dir()

HOURLY_CRON = "0 * * * *"

# Reads data/ — only meaningful after a fetch, so it must skip the hourly run.
DATA_CYCLE = ("check_anomaly.py", "check_connectivity.py")

# Talks to the vendor API — the whole point of #10, so it must NOT skip.
ALARM_CYCLE = ("check_device_alarms.sh", "generate_device_alarms.py")


def _workflows_with_an_hourly_cron():
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        # PyYAML parses a bare `on:` key as the boolean True.
        triggers = doc.get("on", doc.get(True)) or {}
        schedules = triggers.get("schedule") or []
        if any(s.get("cron") == HOURLY_CRON for s in schedules):
            found.append((path.name, doc))
    return found


def _steps_running(doc, needles):
    for job in (doc.get("jobs") or {}).values():
        for step in (job.get("steps") or []):
            body = str(step.get("run", ""))
            if any(n in body for n in needles):
                yield step


def _skips_the_hourly_run(step):
    """True if this step's `if:` excludes the hourly schedule."""
    cond = str(step.get("if", ""))
    return HOURLY_CRON in cond and "github.event.schedule" in cond


def test_there_is_a_workflow_to_check():
    """Guard against the test silently passing because it found nothing."""
    assert _workflows_with_an_hourly_cron(), (
        "no workflow has an hourly cron — if #10 was reverted, delete this "
        "file rather than leaving it passing vacuously"
    )


@pytest.mark.parametrize("name,doc", _workflows_with_an_hourly_cron())
def test_csv_analysis_skips_the_hourly_run(name, doc):
    """
    The hourly run does not fetch, so analysing data/ on it means analysing
    stale CSVs — and writing state from them, which is what made the admin
    mail flap between two answers.
    """
    unguarded = [
        step.get("name", "?")
        for step in _steps_running(doc, DATA_CYCLE)
        if not _skips_the_hourly_run(step)
    ]

    assert not unguarded, (
        f"in {name}, these steps read data/ but run on the hourly poll, which "
        f"does no fetch:\n  - " + "\n  - ".join(unguarded) +
        f"\n\nAdd the same guard the fetch steps use:\n"
        f"  if: github.event_name != 'schedule' "
        f"|| github.event.schedule != '{HOURLY_CRON}'"
    )


@pytest.mark.parametrize("name,doc", _workflows_with_an_hourly_cron())
def test_the_alarm_poll_still_runs_hourly(name, doc):
    """
    The counterpart, so nobody 'fixes' the above by guarding everything.
    Polling alarms hourly is the entire purpose of #10: it is what makes the
    gate's six-hour dwell literal rather than six-to-ten.
    """
    wrongly_guarded = [
        step.get("name", "?")
        for step in _steps_running(doc, ALARM_CYCLE)
        if _skips_the_hourly_run(step)
    ]

    assert not wrongly_guarded, (
        f"in {name}, these alarm steps skip the hourly poll:\n  - "
        + "\n  - ".join(wrongly_guarded) +
        "\n\nThat reverts #10 — the gate's six hours becomes six-to-ten again."
    )
