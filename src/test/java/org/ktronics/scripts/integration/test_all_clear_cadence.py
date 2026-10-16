"""
"ALL SYSTEMS OPERATIONAL" is a weekly report, not an hourly one.

Once the alarm poll went hourly (#10) the admin inbox got an all-clear most
days and some days twice: the hourly run analysed stale CSVs, wrote a
different alerts.json from the 4-hourly run, and the UC9 content hash flapped
between two fleet views - every flap reading as "the state changed".

The stale-CSV half is fixed in test_hourly_poll_scope.py. This file pins the
other half: with nothing to report, a scheduled run sends only on the Monday
06:30 UTC run. A run that HAS something to report is never gated, because
that is the email the whole step exists to send.

The gate lives in shell inside the workflow, so these tests lift it out of
the YAML and run it, rather than asserting on the text around it.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_WORKFLOW = Path(".github/workflows/trigger-shinemonitor.yml")
_GATE_START = '"$send_email" = "true"'


def _repo_root():
    """Walk up to the repo root. Counting .parent lands outside the tree."""
    for parent in Path(__file__).resolve().parents:
        if (parent / _WORKFLOW).is_file():
            return parent
    raise AssertionError(f"could not find {_WORKFLOW} above {__file__}")


def _gate_shell():
    """Lift the all-clear gate out of the workflow YAML, verbatim."""
    lines = (_repo_root() / _WORKFLOW).read_text(encoding="utf-8").splitlines()

    start = None
    for i, line in enumerate(lines):
        if _GATE_START in line and "exit_code" in "".join(lines[i:i + 4]):
            start = i
            break
    assert start is not None, (
        "the all-clear gate is gone from the workflow: no `if "
        f'[ {_GATE_START} ]` block that also tests the anomaly exit code'
    )

    indent = len(lines[start]) - len(lines[start].lstrip())
    closing = " " * indent + "fi"
    for end in range(start + 1, len(lines)):
        if lines[end].rstrip() == closing:
            return "\n".join(lines[start:end + 1])
    raise AssertionError("the gate block is not closed by a matching `fi`")


def _run_gate(exit_code, event_name, schedule, weekday):
    """Run the real gate and report whether an email would go out."""
    shell = _gate_shell()
    for expr, value in (
        (r"\$\{\{\s*steps\.anomaly\.outputs\.exit_code\s*\}\}", exit_code),
        (r"\$\{\{\s*github\.event_name\s*\}\}", event_name),
        (r"\$\{\{\s*github\.event\.schedule\s*\}\}", schedule),
    ):
        shell = re.sub(expr, value, shell)
    assert "${{" not in shell, f"an unrendered workflow expression is left: {shell}"

    # The gate asks `date -u +%u` for the weekday; a function shadows the real
    # command so a test does not pass or fail according to the day it is run.
    script = "\n".join([
        "set -u",
        f"date() {{ echo {weekday}; }}",
        'send_email="true"',
        shell,
        'echo "GATE=$send_email"',
    ])

    # encoding is explicit: the gate echoes an emoji, and the Windows default
    # codec cannot decode it.
    out = subprocess.run([shutil.which("bash"), "-c", script],
                         capture_output=True, text=True, check=True,
                         encoding="utf-8", errors="replace").stdout
    assert "GATE=" in out, out
    return out.rsplit("GATE=", 1)[1].strip() == "true"


pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="the gate is bash; it runs in CI and on any machine with bash",
)


def test_the_gate_is_still_in_the_workflow():
    """A plain structural canary: deleting the gate must fail a test, loudly."""
    shell = _gate_shell()
    assert "date -u +%u" in shell, "the gate no longer looks at the weekday"
    assert "30 6 * * *" in shell, "the gate no longer pins the 06:30 run"


def test_a_quiet_fleet_sends_on_monday_at_0630():
    assert _run_gate("0", "schedule", "30 6 * * *", weekday=1) is True


def test_a_quiet_fleet_is_silent_on_the_hourly_run():
    """The run that caused the flood. Monday is not enough; 06:30 is the one."""
    assert _run_gate("0", "schedule", "0 * * * *", weekday=1) is False


@pytest.mark.parametrize("weekday", [2, 3, 4, 5, 6, 7])
def test_a_quiet_fleet_is_silent_every_other_day(weekday):
    assert _run_gate("0", "schedule", "30 6 * * *", weekday) is False


def test_an_actual_alert_is_never_gated():
    """
    Exit code 2 means a plant is in trouble. Holding that until Monday would
    be a far worse bug than the flood this gate was added to stop.
    """
    assert _run_gate("2", "schedule", "0 * * * *", weekday=3) is True


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_a_push_or_manual_run_still_sends(event):
    """There the email is build verification, not a status report."""
    assert _run_gate("0", event, "", weekday=3) is True
