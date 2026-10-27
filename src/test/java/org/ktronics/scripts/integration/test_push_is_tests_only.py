"""
A push to `main` runs the test suite only (2026-10-23).

Why: `trigger-shinemonitor.yml` used to run its full data-collection job on
every push as "build verification". That job takes 80-90 minutes (21 plants
fetched one after another) and a push run first waits for the scheduled run
that holds the `shinemonitor-commit` concurrency slot. Measured 2026-10-21:
28 minutes queued, then 89 minutes running - about two hours from push to
"Build OK". The three other push-triggered workflows (DessMonitor collection
and both weekly report workflows) added a collection run and two admin
summary emails to every push, for nothing a scheduled run would not do a few
hours later anyway.

Now: only `trigger-shinemonitor.yml` listens to `push`; only its `tests` job
runs on one; and that job is where the "Build OK" email comes from. Everything
else runs on its schedule or on `workflow_dispatch`, which still does the full
end-to-end run on demand.

These tests read the workflow YAML so a revert of any part is loud.
"""

from pathlib import Path

import pytest
import yaml


def _workflows_dir():
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / ".github" / "workflows"
        if candidate.is_dir():
            return candidate
    raise AssertionError(f"could not find .github/workflows above {__file__}")


WORKFLOWS = _workflows_dir()
SHINEMONITOR = "trigger-shinemonitor.yml"


def _load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(doc):
    # PyYAML parses a bare `on:` key as the boolean True.
    return doc.get("on", doc.get(True)) or {}


def _steps(job):
    return job.get("steps") or []


def test_only_the_shinemonitor_workflow_listens_to_push():
    """
    DessMonitor collection and both weekly report workflows used to run on
    every push too. Each push then produced a collection run and two admin
    weekly summaries that said nothing a scheduled run would not say.
    """
    offenders = [
        path.name
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if path.name != SHINEMONITOR and "push" in _triggers(_load(path.name))
    ]
    assert not offenders, (
        "these workflows run on push again; a push is tests only:\n  - "
        + "\n  - ".join(offenders)
    )


def test_the_shinemonitor_workflow_still_listens_to_push():
    """Without this a push would be untested and send no email at all."""
    assert "push" in _triggers(_load(SHINEMONITOR)), (
        f"{SHINEMONITOR} no longer runs on push, so nothing tests a push"
    )


def test_data_collection_does_not_run_on_push():
    """
    The `run` job is the 80-90 minute one, and the one that queues behind the
    scheduled run. It must be skipped on push, at job level, so no step of it
    runs and no runner is held.
    """
    run = _load(SHINEMONITOR)["jobs"]["run"]
    cond = str(run.get("if", ""))
    assert "github.event_name != 'push'" in cond, (
        "the `run` job of trigger-shinemonitor.yml runs on push again "
        "(expected `if: github.event_name != 'push'` on the job), which puts "
        "every push back behind the 80-90 minute collection"
    )


def test_the_tests_job_runs_on_every_trigger():
    """The counterpart: the `tests` job must not pick up the same exclusion."""
    tests = _load(SHINEMONITOR)["jobs"]["tests"]
    cond = str(tests.get("if", ""))
    assert "push" not in cond, (
        f"the `tests` job is conditioned on the trigger ({cond!r}); a push "
        "must at least run the tests"
    )


def test_build_ok_mail_is_sent_by_the_tests_job():
    """
    With `run` skipped on push, the only job that can say "Build OK" is
    `tests`. The step must be push + success, and gated fail-closed
    (`== 'true'`): a success mail has no business sending when the flags
    step never ran.
    """
    tests = _load(SHINEMONITOR)["jobs"]["tests"]
    push_success = [
        step for step in _steps(tests)
        if "github.event_name == 'push'" in str(step.get("if", ""))
        and "success()" in str(step.get("if", ""))
    ]
    assert push_success, (
        "no step in the `tests` job sends mail on push + success; a green push "
        "would be silent"
    )
    for step in push_success:
        assert "emails_build_verification == 'true'" in str(step.get("if", "")), (
            f"{step.get('name', '?')}: the push success mail must be gated on "
            "emails_build_verification == 'true'"
        )


def test_no_push_only_step_is_left_in_the_run_job():
    """
    A `github.event_name == 'push'` step inside `run` can never fire now. It is
    dead code, and dead code that looks like it sends "Build OK" is how the
    next person comes to believe the push still runs the collection.
    """
    run = _load(SHINEMONITOR)["jobs"]["run"]
    dead = [
        step.get("name", "?") for step in _steps(run)
        if "github.event_name == 'push'" in str(step.get("if", ""))
    ]
    assert not dead, (
        "these steps of the `run` job are conditioned on push, which never "
        "runs that job any more:\n  - " + "\n  - ".join(dead)
    )
