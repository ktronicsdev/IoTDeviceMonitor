"""
This repository is public, so every workflow log is world-readable (#4).

Four places wrote customer data into one. Three only on a failure, but the
fourth ran on every push and manual run:

  * both alarm fetch scripts echoed the whole response body when a fetch
    failed - an error response still carries plant names, device serials and
    sometimes a session token;
  * generate_device_alarms.py printed the plant name and the alarm text in
    test mode, which is every push;
  * trigger-dessmonitor.yml printed the first 100 characters of the
    credentials file when it would not parse. GitHub masks a whole secret,
    but a cut-off fragment need not match the mask, so masking does not save
    you here.

The rule these tests encode: a log line may carry an error code, a count, a
size or an opaque key. It may not carry a response body, a plant name, an
alarm message or any part of a credentials file.
"""

import re
from pathlib import Path

import pytest

SCRIPTS = Path("src/main/java/org/ktronics/scripts")
WORKFLOWS = Path(".github/workflows")


def _repo_root():
    """Walk up to the repo root. Counting .parent here has been wrong before."""
    for parent in Path(__file__).resolve().parents:
        if (parent / SCRIPTS).is_dir():
            return parent
    raise AssertionError(f"could not find {SCRIPTS} above {__file__}")


ROOT = _repo_root()

ALARM_SCRIPTS = ("check_device_alarms.sh", "check_dessmonitor_device_alarms.sh")


@pytest.mark.parametrize("name", ALARM_SCRIPTS)
def test_a_failed_fetch_does_not_echo_the_response_body(name):
    path = ROOT / SCRIPTS / name
    text = path.read_text(encoding="utf-8")

    # `${alarms_response}` on its own expands to the body. `${#alarms_response}`
    # is its length, which is what the failure path is allowed to report.
    bare = re.findall(r"echo[^\n]*\$\{alarms_response\}", text)

    assert not bare, (
        f"{name} echoes the raw response into a public log:\n  "
        + "\n  ".join(bare)
        + "\n\nPrint the error code and ${#alarms_response} (its size) instead."
    )


@pytest.mark.parametrize("name", ALARM_SCRIPTS)
def test_a_failed_fetch_still_says_enough_to_diagnose(name):
    """The counterpart, so nobody satisfies the above by printing nothing."""
    text = (ROOT / SCRIPTS / name).read_text(encoding="utf-8")
    failure = text.split("Failed to fetch alarms", 1)
    assert len(failure) == 2, f"{name} no longer reports a failed fetch at all"
    assert "${err}" in failure[0] + failure[1][:200], (
        f"{name} reports a failed fetch without the error code, which is the "
        f"one thing that identifies it"
    )


def test_test_mode_does_not_print_the_plant_or_the_alarm_text():
    """
    The worst of the four: test mode runs on every push, so this wrote
    customer data to a public log continuously rather than only on a fault.
    """
    text = (ROOT / SCRIPTS / "generate_device_alarms.py").read_text(encoding="utf-8")

    offenders = [
        line.strip()
        for line in text.splitlines()
        if "TEST MODE" in line and re.search(r"\.get\(\s*'(plant|desc)'", line)
    ]

    assert not offenders, (
        "test mode prints customer data into a public log:\n  "
        + "\n  ".join(offenders)
        + "\n\nPrint the alarm key instead; the state file it indexes is not public."
    )


def test_no_workflow_prints_part_of_a_credentials_file():
    """
    `head -c N` on a credentials file defeats GitHub's secret masking, which
    matches the whole value and not a prefix of it.
    """
    offenders = []
    for path in sorted((ROOT / WORKFLOWS).glob("*.yml")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # The leading \b matters: without it, `od` matches inside `chmod 600`.
            if re.search(r"\b(head|cut|dd|xxd|od|strings)\b[^\n]*credentials", line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")

    assert not offenders, (
        "a workflow prints part of a credentials file to a public log:\n  "
        + "\n  ".join(offenders)
        + "\n\nReport its size and the parser's position, never its contents."
    )
