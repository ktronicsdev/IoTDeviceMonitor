#!/usr/bin/env python3
"""Tests that the PH1000/PH1800 live loops are TIME-bounded, not count-bounded.

Both live workflows run one long job that loops fetch -> publish and then hands over to a
pre-queued standby. The loop used to be `for i in $(seq 1 "${ITERATIONS}")` with
ITERATIONS=26 and SLEEP_S=600, against `timeout-minutes: 330`. Run length was therefore
`26 x work + 25 x sleep`, and `work` scales with plant count:

    job timeout          19,800 s (330 min)
    sleeps (25 x 600)    15,000 s
    work budget           4,800 s  ->  185 s per iteration
    measured (4 plants)     155 s  (~39 s/plant)  ->  29 s/iteration spare

A 5th PH1800 plant (a one-line credentials flag) took work to ~194 s/iteration, i.e. a
334-minute run against a 330-minute timeout: the job would be killed mid-loop and the tail
iterations would never publish. That is the Aug-2026 staleness signature reached without
breaking the two-CI rule - see site/ph1800/README.md.

The fix: `while :` against a BUDGET_MIN wall-clock budget that stops while a full iteration
still fits. These are static assertions (no network, no bash) guarding against a revert, in
the same style as test_dashboard_clock.py / test_dessmonitor_data_collection.py.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[7]
WORKFLOWS = {
    "ph1800": REPO_ROOT / ".github" / "workflows" / "trigger-ph1800.yml",
    "ph1000": REPO_ROOT / ".github" / "workflows" / "trigger-ph1000.yml",
}


@pytest.fixture(scope="module")
def flows():
    return {name: p.read_text(encoding="utf-8") for name, p in WORKFLOWS.items()}


def _loop_step(text):
    """The body of the 'Fetch + publish loop' step (everything after its `run: |`)."""
    i = text.index("Fetch + publish loop")
    return text[text.index("run: |", i):]


def _code_only(body):
    """Drop shell comments, so an assertion can't be satisfied (or tripped) by prose.

    The loop carries a comment explaining why the old `seq 1 26` form was removed; a naive
    substring check would match that explanation instead of real code.
    """
    return "\n".join(
        ln for ln in body.splitlines() if not ln.lstrip().startswith("#")
    )


class TestLoopIsTimeBounded:
    """The control structure, not the iteration count, decides when a run ends."""

    @pytest.mark.parametrize("name", sorted(WORKFLOWS))
    def test_workflow_exists(self, flows, name):
        assert WORKFLOWS[name].exists(), f"{WORKFLOWS[name]} is missing"

    @pytest.mark.parametrize("name", sorted(WORKFLOWS))
    def test_not_seq_bounded(self, flows, name):
        """REGRESSION: `for i in $(seq 1 "${ITERATIONS}")` is what made run length scale
        with plant count. Re-adding it re-arms the timeout overrun."""
        body = _code_only(_loop_step(flows[name]))
        assert "seq 1" not in body, (
            f"{name}: the live loop is count-bounded again (`seq 1 ...`). Run length then "
            f"scales with plant count and a 5th plant overruns timeout-minutes."
        )
        assert re.search(r"^\s*while\s*:\s*;?\s*do", body, re.M), (
            f"{name}: expected a `while :` loop driven by the wall-clock budget"
        )

    @pytest.mark.parametrize("name", sorted(WORKFLOWS))
    def test_budget_is_derived_from_budget_min(self, flows, name):
        body = _loop_step(flows[name])
        assert "BUDGET_S=$(( BUDGET_MIN * 60 ))" in body, (
            f"{name}: BUDGET_S must be derived from BUDGET_MIN (minutes -> seconds)"
        )
        assert "START=$(date +%s)" in body, f"{name}: the loop must record its start time"

    @pytest.mark.parametrize("name", sorted(WORKFLOWS))
    def test_budget_is_under_the_job_timeout(self, flows, name):
        """The budget must leave headroom, or the loop is killed before it can stop itself."""
        text = flows[name]
        timeout = int(re.search(r"timeout-minutes:\s*(\d+)", text).group(1))
        default_budget = int(
            re.search(r"BUDGET_MIN:\s*\$\{\{[^}]*\|\|\s*'(\d+)'", text).group(1)
        )
        assert default_budget < timeout, (
            f"{name}: BUDGET_MIN ({default_budget}) must be below timeout-minutes ({timeout})"
        )
        assert timeout - default_budget >= 15, (
            f"{name}: only {timeout - default_budget} min of headroom between the budget and "
            f"the job timeout - one slow iteration would still be killed mid-publish"
        )

    @pytest.mark.parametrize("name", sorted(WORKFLOWS))
    def test_break_accounts_for_sleep_and_measured_work(self, flows, name):
        """Stopping must look ahead a whole iteration - sleep AND the work just measured -
        otherwise the last iteration starts and is killed partway through."""
        body = _loop_step(flows[name])
        assert "work=$(( $(date +%s) - iter_start ))" in body, (
            f"{name}: each iteration must measure its own work time"
        )
        assert "elapsed + SLEEP_S + work" in body, (
            f"{name}: the break condition must look ahead by sleep + measured work"
        )

    @pytest.mark.parametrize("name", sorted(WORKFLOWS))
    def test_iterations_still_available_as_a_manual_cap(self, flows, name):
        """Scheduled runs are purely time-bounded (blank default), but a manual dispatch can
        still ask for a short run."""
        text = flows[name]
        body = _loop_step(text)
        assert re.search(r"ITERATIONS:\s*\$\{\{[^}]*\|\|\s*''\s*\}\}", text), (
            f"{name}: ITERATIONS must default to empty so scheduled runs are time-bounded"
        )
        assert '[ -n "${ITERATIONS}" ]' in body, (
            f"{name}: the manual iteration cap must be honoured when supplied"
        )
        assert "budget_min:" in text, f"{name}: budget_min must be a workflow_dispatch input"

    @pytest.mark.parametrize("name", sorted(WORKFLOWS))
    def test_warns_before_the_fleet_outgrows_the_live_cycle(self, flows, name):
        """The early-warning signal that work is crowding out the poll interval."""
        body = _loop_step(flows[name])
        assert "::warning::" in body, f"{name}: missing the scaling ::warning:: guard"
        assert "SLEEP_S / 2" in body, (
            f"{name}: the warning should fire when work exceeds half the poll interval"
        )


class TestConcurrencyUnchanged:
    """The two-CI rule's other half: a healthy live loop is never cancelled mid-flight."""

    @pytest.mark.parametrize("name", sorted(WORKFLOWS))
    def test_live_loop_is_not_cancel_in_progress(self, flows, name):
        text = flows[name]
        group = re.search(r"concurrency:\s*\n\s*group:\s*(\S+)", text).group(1)
        assert group == f"{name}-live", f"{name}: unexpected concurrency group {group!r}"
        assert re.search(r"cancel-in-progress:\s*false", text), (
            f"{name}: the live loop must not be cancel-in-progress - the next run waits as "
            f"a hot standby instead of killing a healthy loop"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
