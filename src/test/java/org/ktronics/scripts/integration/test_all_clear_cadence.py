"""
"ALL SYSTEMS OPERATIONAL" is a weekly report, and it has to actually arrive.

First attempt (2026-10-16) gated it on `github.event.schedule == '30 6 * * *'`
and weekday 1. On Monday 2026-10-19 that run never happened: GitHub delivered
11 of the ~30 scheduled runs the crons ask for, which is its normal rate here
(11-13 a day across 14-19 Oct). Every Monday run logged "Skipping all-clear",
so nothing was sent at all, and silence stopped carrying information - a
healthy fleet and a stopped pipeline looked identical to the admin. That is a
worse failure than the daily noise the gate was added to stop, because the
noise at least proved the pipeline was alive.

There was a second, quieter half to the same bug. The gate could only ever set
`send_email=false`, and the hash check above it already says false whenever the
fleet is unchanged - so a fleet that simply stayed quiet produced no status
email at all, for as long as it stayed quiet. The "weekly" all-clear only ever
rode along on the run where alerts happened to clear.

The rule now: one all-clear per ISO week, sent by the FIRST eligible scheduled
run of that week, whichever run that happens to be. No cron is named, so no
single dropped run can swallow the week; and the gate can now *cause* a send,
not only suppress one, so an unchanging quiet fleet still produces its weekly
proof of life.

These tests run the real step out of the workflow YAML in a sandbox - the hash
logic, the gate and the state write together - with `date` and `python3`
stubbed so nothing depends on the day they run or sends mail.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_WORKFLOW = Path(".github/workflows/trigger-shinemonitor.yml")
_STEP = "Send system-wide status email to admin"
_STATE = "state/admin_all_clear_state.txt"


def _repo_root():
    """Walk up to the repo root. Counting .parent lands outside the tree."""
    for parent in Path(__file__).resolve().parents:
        if (parent / _WORKFLOW).is_file():
            return parent
    raise AssertionError(f"could not find {_WORKFLOW} above {__file__}")


ROOT = _repo_root()


def _step_body():
    """The step's `run:` script, straight out of the workflow."""
    doc = yaml.safe_load((ROOT / _WORKFLOW).read_text(encoding="utf-8"))
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if step.get("name") == _STEP:
                return step["run"]
    raise AssertionError(f"no step named {_STEP!r} in {_WORKFLOW}")


def _render(body, exit_code, event_name, schedule):
    """Substitute the workflow expressions this step reads."""
    for pattern, value in (
        (r"\$\{\{\s*steps\.anomaly\.outputs\.exit_code\s*\}\}", exit_code),
        (r"\$\{\{\s*github\.event_name\s*\}\}", event_name),
        (r"\$\{\{\s*github\.event\.schedule\s*\}\}", schedule),
        (r"\$\{\{\s*github\.repository\s*\}\}", "ktronicsdev/IoTDeviceMonitor"),
        (r"\$\{\{\s*github\.run_id\s*\}\}", "12345"),
    ):
        body = re.sub(pattern, value, body)
    assert "${{" not in body, f"an unrendered workflow expression remains:\n{body}"
    return body


def _run_step(tmp_path, *, week, exit_code="0", event_name="schedule",
              schedule="0 * * * *", last_week=None, alerts=None, send_ok=True,
              last_hash_matches=False, last_hash_differs=False):
    """
    Run the real step in `tmp_path` and report what it decided.

    Returns (sent, recorded_week, returncode, output).
    """
    (tmp_path / "alerts").mkdir(exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    body_json = '{"alerts": %s, "suppressed": [], "ignored": []}' % (
        alerts if alerts is not None else "[]")
    (tmp_path / "alerts/alerts.json").write_text(body_json, encoding="utf-8")
    (tmp_path / "alerts/alerts.txt").write_text("fleet report\n", encoding="utf-8")
    if last_week is not None:
        (tmp_path / _STATE).write_text(last_week + "\n", encoding="utf-8")
    if last_hash_matches:
        # Make the UC9 hash check see no change, the way a fleet that has been
        # healthy for days looks. Computed exactly as the step computes it.
        digest = subprocess.run(
            [shutil.which("bash"), "-c",
             "jq -cS '{alerts, suppressed, ignored}' alerts/alerts.json "
             "| sha256sum | awk '{print $1}'"],
            cwd=str(tmp_path), capture_output=True, text=True,
            check=True).stdout.strip()
        (tmp_path / "state/admin_email_state.txt").write_text(
            digest + "\n", encoding="utf-8")
    if last_hash_differs:
        # A stored hash that cannot match: the fleet's suppressed/ignored
        # lists have churned since the last run, which is the ordinary case
        # in production and the one the old gate mistook for "alerts cleared".
        (tmp_path / "state/admin_email_state.txt").write_text(
            "0" * 64 + "\n", encoding="utf-8")

    sent_marker = tmp_path / "sent.marker"
    # One test runs the step twice in the same directory; a stale marker from
    # the first run would read as a send by the second.
    sent_marker.unlink(missing_ok=True)
    script = "\n".join([
        # `date -u +%G-W%V` is the step's only date call, so a stub that
        # ignores its arguments is unambiguous - and the result no longer
        # depends on the week the suite happens to run in.
        f'date() {{ echo "{week}"; }}',
        # Stand in for send_email.py. Records the attempt, and can fail.
        f'python3() {{ touch "{sent_marker.as_posix()}"; '
        f'return {0 if send_ok else 1}; }}',
        _render(_step_body(), exit_code, event_name, schedule),
    ])

    proc = subprocess.run(
        [shutil.which("bash"), "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=str(tmp_path), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)

    recorded = None
    state_file = tmp_path / _STATE
    if state_file.is_file():
        recorded = state_file.read_text(encoding="utf-8").strip()
    return sent_marker.exists(), recorded, proc.returncode, proc.stdout + proc.stderr


pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="the step is bash and uses jq; both are present in CI",
)


# --- the regression this file exists for --------------------------------


@pytest.mark.parametrize("schedule", [
    "0 * * * *",     # the hourly poll
    "30 2 * * *",
    "30 6 * * *",    # the one the first version insisted on
    "30 18 * * *",
    "30 22 * * *",
])
def test_any_scheduled_run_can_carry_the_weekly_all_clear(tmp_path, schedule):
    """
    The bug: the all-clear was pinned to `30 6 * * *` on a Monday, and GitHub
    drops most scheduled runs, so on 2026-10-19 that run never existed and no
    all-clear was sent. Whichever run is first in the week must be able to do
    it, or a dropped run silently costs the admin the whole week.
    """
    # last_hash_matches forces this through the weekly path rather than the
    # "alerts just cleared" path. Without it the test passes for the wrong
    # reason: the hash change alone would send the mail whatever the cron.
    sent, recorded, rc, out = _run_step(tmp_path, week="2026-W43",
                                        schedule=schedule,
                                        last_week="2026-W42",
                                        last_hash_matches=True)

    assert sent, f"schedule {schedule!r} could not send the week's all-clear{out}"
    assert recorded == "2026-W43", f"the week was not recorded: {recorded!r}{out}"
    assert rc == 0, out


def test_a_quiet_fleet_sends_once_and_then_stays_quiet(tmp_path):
    """One a week, not one a run - the original complaint."""
    sent, recorded, _, out = _run_step(tmp_path, week="2026-W43")
    assert sent and recorded == "2026-W43", out

    again, still, _, out2 = _run_step(tmp_path, week="2026-W43")
    assert not again, "a second run in the same week sent another all-clear" + out2
    assert still == "2026-W43", out2


def test_an_unchanging_quiet_fleet_still_gets_its_weekly_confirmation(tmp_path):
    """
    The quieter half of the bug. The hash check says "skip, no change" for a
    fleet that stays healthy, and the old gate could only subtract - so a
    month of good health produced a month of silence, indistinguishable from
    a dead pipeline. Here the marker is last week's and the hash is stable,
    which is exactly that situation.
    """
    sent, recorded, rc, out = _run_step(tmp_path, week="2026-W44",
                                        last_week="2026-W43",
                                        last_hash_matches=True)

    assert sent, (
        "a healthy fleet produced no weekly confirmation; silence then means "
        "either health or a stopped pipeline, and nobody can tell which" + out)
    assert recorded == "2026-W44", out
    assert rc == 0, out


def test_the_next_week_sends_again(tmp_path):
    sent, recorded, _, out = _run_step(tmp_path, week="2026-W44",
                                       last_week="2026-W43")
    assert sent, "a new week did not produce an all-clear" + out
    assert recorded == "2026-W44", out


def test_the_turn_of_the_year_is_a_new_week(tmp_path):
    """
    A naive comparison of week numbers alone would read W01 as earlier than
    W52 and stay silent through January.
    """
    sent, recorded, _, out = _run_step(tmp_path, week="2027-W01",
                                       last_week="2026-W52")
    assert sent, "the first week of the year was treated as already sent" + out
    assert recorded == "2027-W01", out


# --- what must not change ----------------------------------------------


def test_an_actual_alert_is_never_gated(tmp_path):
    """
    Exit code 2 means a plant is in trouble. Holding that for the week would
    be far worse than the noise this gate exists to stop - so an alert sends
    even in a week whose all-clear has already gone out.
    """
    sent, _, rc, out = _run_step(tmp_path, week="2026-W43",
                                 last_week="2026-W43", exit_code="2",
                                 alerts='[{"plant": "p", "severity": "RED"}]')
    assert sent, "a RED alert was suppressed by the all-clear gate" + out
    assert rc == 0, out


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_a_push_or_manual_run_says_nothing_when_nothing_is_wrong(tmp_path, event):
    """
    Build verification used to send unconditionally, so every developer push
    mailed an "ALL SYSTEMS OPERATIONAL" reporting that nothing had happened.
    That is noise in UC1, whose content is empty, and in UC9, whose cadence it
    ignores. The run status and the test email already report the build.
    """
    sent, _, _, out = _run_step(tmp_path, week="2026-W43",
                                last_week="2026-W43",
                                event_name=event, schedule="")
    assert not sent, (
        f"a {event} run mailed an all-clear as build verification" + out)


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_a_push_or_manual_run_still_reports_alerts(tmp_path, event):
    """The counterpart: a developer pushing wants to hear about a live fault."""
    sent, _, _, out = _run_step(tmp_path, week="2026-W43",
                                last_week="2026-W43",
                                event_name=event, schedule="", exit_code="2",
                                alerts='[{"plant": "p", "severity": "RED"}]')
    assert sent, f"a {event} run suppressed a RED alert" + out


def test_a_changed_hash_does_not_bypass_the_week(tmp_path):
    """
    The case that broke it in production, twice over.

    The gate had an exception: if the hash moved and nothing was RED, treat it
    as "alerts cleared" and send. But the hash covers alerts + suppressed +
    ignored, and those churn constantly - with alerts=0, suppressed=1,
    ignored=6, a plant moving in or out of the ignored list moves the hash.
    So nearly every scheduled run took the exception and mailed another
    all-clear: five of them on 21-22 October inside one ISO week whose marker
    was already written.

    Here the week is already confirmed and the hash has moved. Nothing may go
    out. No previous test covered this: they all left the hash unchanged, so
    the exception was never exercised and the suite stayed green while the
    inbox filled.
    """
    sent, _, _, out = _run_step(tmp_path, week="2026-W43",
                                last_week="2026-W43",
                                alerts='[]', last_hash_differs=True)

    assert not sent, (
        "a changed hash bypassed the weekly rule - this is the 21 Oct bug, "
        "where churn in the ignored list mailed an all-clear every run" + out)


def test_an_unreadable_marker_errs_towards_sending(tmp_path):
    """
    A spare all-clear costs one email. A missing one costs the admin the
    ability to tell a quiet fleet from a stopped pipeline, so the doubt is
    resolved in favour of sending.
    """
    sent, recorded, _, out = _run_step(tmp_path, week="2026-W43",
                                       last_week="not-a-week")
    assert sent, "an unreadable marker silenced the all-clear" + out
    assert recorded == "2026-W43", out


def test_a_failed_send_does_not_consume_the_week(tmp_path):
    """
    If the marker were written before the send, one SMTP failure would cost
    the whole week's confirmation and nothing would say so.
    """
    _, recorded, rc, out = _run_step(tmp_path, week="2026-W43", send_ok=False)

    assert rc != 0, "a failed send left the step green" + out
    assert recorded is None, (
        f"the week was recorded despite the send failing: {recorded!r}{out}")


def test_the_gate_names_no_cron(tmp_path):
    """
    A plain canary on the shape of the fix. Re-pinning the all-clear to a
    particular cron is the regression; it would pass every test above that
    happens to use that cron, so assert the dependency is gone outright.
    """
    body = _step_body()
    gate = body.split("all_clear_week=", 1)[1].split("# Send email if needed")[0]

    assert "github.event.schedule" not in gate, (
        "the all-clear gate reads github.event.schedule again — a dropped run "
        "of that one cron then costs the whole week:\n" + gate)
    assert "date -u +%u" not in gate, (
        "the gate is testing the weekday again:\n" + gate)
