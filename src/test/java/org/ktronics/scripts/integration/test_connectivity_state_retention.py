"""
A plant missing from this poll has not recovered — it is just missing.

`clear_recovered()` drops the connectivity state of every plant that is not in
`fleet.link_down`. The intent is right: a plant that came back should lose its
auto-ignored status, so its next outage is not silent. The test used to decide
that was wrong, because "not in link_down" also matches a plant that was not
classified at all this run.

What that cost, in production: sohan-must-pv1800-3024-pro appeared in one run's
fleet and not the next, so its entry was deleted and recreated every few hours.
`send_count` reset to 1 each time, never reached 3, never auto-ignored, and the
plant mailed the admin indefinitely from 12 to 16 October 2026.

The immediate trigger — the hourly poll analysing CSVs it had not fetched — is
fixed separately. This pins the behaviour so that *any* future reason for a
plant to drop out of one poll cannot reset its counter: a transient fetch
failure, a renamed CSV, a vendor API timeout. State is only cleared when the
plant is positively seen to be reporting again.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent.parent.parent / \
    "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from connectivity import FleetStatus, LinkStatus  # noqa: E402
from check_connectivity import clear_recovered  # noqa: E402


def _down(plant_key):
    return LinkStatus(
        plant_key=plant_key, kind="no_rows", link_down=True, days_stale=197,
        days_since_data=197, last_nonzero_date=None, last_data_date=None,
        stale_days=3,
    )


def _reporting(plant_key):
    return LinkStatus(
        plant_key=plant_key, kind="reporting", link_down=False, days_stale=0,
        days_since_data=0, last_nonzero_date=None, last_data_date=None,
        stale_days=3,
    )


def _state(send_count=2):
    return {
        "sohan-must-pv1800-3024-pro": {
            "plant": "sohan-must-pv1800-3024-pro",
            "send_count": send_count,
            "first_seen": "2026-10-12T16:32:00",
            "last_sent": "2026-10-12T16:32:00",
            "ignored": False,
            "kind": "no_rows",
        }
    }


def test_a_plant_missing_from_this_poll_keeps_its_counter():
    """
    THE regression. The plant is in neither list — not down, not reporting,
    simply absent. Its progress towards auto-ignore must survive.
    """
    state = _state(send_count=2)
    fleet = FleetStatus(reporting=[_reporting("someone-else")], link_down=[])

    clear_recovered(state, fleet)

    entry = state.get("sohan-must-pv1800-3024-pro")
    assert entry is not None, (
        "the plant was absent from this poll, not reporting — deleting its "
        "state resets send_count to 1 and it mails for ever"
    )
    assert entry["send_count"] == 2, "its progress must be untouched"


def test_a_plant_still_down_keeps_its_counter():
    """The ordinary case: still down, still tracked."""
    state = _state(send_count=2)
    fleet = FleetStatus(
        reporting=[], link_down=[_down("sohan-must-pv1800-3024-pro")])

    clear_recovered(state, fleet)

    assert state["sohan-must-pv1800-3024-pro"]["send_count"] == 2


def test_a_plant_that_is_reporting_again_is_cleared():
    """
    The counterpart, and the reason clear_recovered exists. A plant that has
    genuinely come back must lose its state, or it stays auto-ignored and its
    next outage is silent.
    """
    state = _state(send_count=3)
    fleet = FleetStatus(
        reporting=[_reporting("sohan-must-pv1800-3024-pro")], link_down=[])

    clear_recovered(state, fleet)

    assert "sohan-must-pv1800-3024-pro" not in state, (
        "a plant that is reporting again must be cleared, so a later outage "
        "is not swallowed by a stale auto-ignore"
    )
