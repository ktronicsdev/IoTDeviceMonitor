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


def test_log_lines_do_not_hardcode_the_test_customer():
    """
    The test-customer label belongs in the filter that selects it, and nowhere
    else. Two [TEST MODE] lines repeated it into the log as a literal, which
    PR #30 removed.

    The disclosure argument for this one is weak on its own - 'gayan-imh' is
    already in the filter two lines above, in `send_customer_emails.py`, in the
    directory name `site/ph1800/Gayan-IMH/` and across `data/*.csv`. The real
    defect is duplication: the label is decided in exactly one place, and a
    copy inside a user-visible message silently lies the day that place
    changes. Removing it is right for that reason, and this test is what stops
    it coming back.

    Comments are deliberately not checked. They carry the same literal and are
    not log output, so they are out of scope rather than overlooked.
    """
    text = (ROOT / SCRIPTS / "generate_device_alarms.py").read_text(encoding="utf-8")

    # Read the label out of the filter rather than hardcoding it here, so this
    # test keeps working when the test customer changes - which is the whole
    # failure mode it guards.
    match = re.search(r"customer_label'[^=]*==\s*'([^']+)'", text)
    assert match, (
        "could not find the test-customer filter in generate_device_alarms.py; "
        "if UC5 moved, point this test at its new home")
    label = match.group(1)

    def norm(s):
        """'Gayan-IMH', 'gayan imh' and 'gayanimh' are the same thing here."""
        return re.sub(r"[^a-z0-9]", "", s.lower())

    offenders = []
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if norm(label) not in norm(stripped):
            continue
        if "customer_label" in stripped:
            continue        # the filter itself: the one place it belongs
        offenders.append(f"{n}: {stripped}")

    assert not offenders, (
        f"the test-customer label {label!r} is repeated outside the filter "
        f"that defines it:\n  " + "\n  ".join(offenders)
        + "\n\nLog the count, or the label read from the data, not a literal."
    )
