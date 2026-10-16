"""
Publishing to `main` goes through push_data_commit.sh. Never a bare `git push`.

#18/#28 gave the two data workflows a safe publish: fetch, rebase, merge the
cumulative `state/*.json` with merge_alarm_state.py, retry five times. Four
other workflows kept a bare `git push` (#29). A bare push does not fetch, so
when it loses a race `main` has moved, the push is rejected non-fast-forward,
and because it is the last command in its step the step fails and the commit
dies with the runner. Nothing retries it.

Three of those four committed a `state/*.json`, so losing the commit meant the
send counters never reached `main` and the next run mailed the same fault as
new - the October flood reached by a different route. Per-workflow
`concurrency` groups do not help: a group only serialises a workflow against
itself, and trigger-shinemonitor and trigger-dessmonitor push to `main` every
hour.

This is the guard that stops a new workflow reintroducing it. It is deliberately
about `main` only: the dashboard branches below have a single writer each and
are force-pushed on purpose.
"""

import re
from pathlib import Path

import pytest
import yaml

PUBLISH_SCRIPT = "push_data_commit.sh"

# Single-writer dashboard data branches, decoupled from main by design. A force
# push to one of these is correct, not a race.
DEDICATED_BRANCHES = ("ph1000-live", "ph1000-hist", "ph1800-live", "ph1800-hist")


def _workflows_dir():
    """Walk up to the repo root. Counting .parent here has been wrong before."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".github" / "workflows"
        if candidate.is_dir():
            return candidate
    raise AssertionError(f"could not find .github/workflows above {__file__}")


WORKFLOWS = _workflows_dir()


def _workflows_that_push():
    """(name, doc) for every workflow whose steps run `git push` at all."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bgit\s+push\b", text):
            found.append((path.name, yaml.safe_load(text)))
    return found


def _push_lines(doc):
    """Every `git push ...` line in the workflow, with the step that holds it."""
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            body = step.get("run") or ""
            # Join backslash continuations so a wrapped command reads as one.
            body = re.sub(r"\\s*\n\s*", " ", body)
            for line in body.splitlines():
                if re.search(r"\bgit\s+push\b", line):
                    yield step.get("name", "?"), line.strip()


def _is_safe(line):
    """A push is safe if the publish script makes it, or it owns its branch."""
    if PUBLISH_SCRIPT in line:
        return True
    return any(b in line for b in DEDICATED_BRANCHES)


def test_there_are_pushes_to_check():
    """Guard against this file passing because it matched nothing."""
    assert _workflows_that_push(), (
        "no workflow runs `git push` - if publishing moved elsewhere, delete "
        "this file rather than leaving it passing vacuously"
    )


@pytest.mark.parametrize("name,doc", _workflows_that_push())
def test_no_workflow_pushes_to_main_bare(name, doc):
    offenders = [
        f"{step}: {line}" for step, line in _push_lines(doc) if not _is_safe(line)
    ]

    assert not offenders, (
        f"{name} pushes without the retry/merge path from #28:\n  - "
        + "\n  - ".join(offenders)
        + "\n\nReplace the bare push with:\n"
        f"  chmod +x src/main/java/org/ktronics/scripts/{PUBLISH_SCRIPT}\n"
        f"  src/main/java/org/ktronics/scripts/{PUBLISH_SCRIPT} --resolve-conflicts\n"
        "\nAdd --branch NAME if the step must publish to a fixed branch "
        "regardless of the ref the run started from."
    )


@pytest.mark.parametrize("name,doc", _workflows_that_push())
def test_the_publish_script_is_made_executable(name, doc):
    """
    A checkout does not preserve the mode reliably, so every caller chmods
    first. One that forgets fails with "Permission denied" only in CI.
    """
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            body = step.get("run") or ""
            if PUBLISH_SCRIPT not in body:
                continue
            assert f"chmod +x" in body and PUBLISH_SCRIPT in body, (
                f"{name}, step {step.get('name', '?')!r} runs {PUBLISH_SCRIPT} "
                f"without chmod +x first"
            )


def test_the_dedicated_branches_are_the_only_exemption():
    """
    If this list grows, someone has exempted a branch from the retry path.
    Pinning it means that shows up as a test change in review, not as a
    one-word diff in a workflow nobody re-reads.
    """
    assert DEDICATED_BRANCHES == (
        "ph1000-live", "ph1000-hist", "ph1800-live", "ph1800-hist"
    )
