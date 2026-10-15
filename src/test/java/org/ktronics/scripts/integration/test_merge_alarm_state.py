"""
Merging cumulative state must never drop what a run recorded.

PR #28 resolved a conflicting state/*.json with `git checkout --ours`, which
is whole-file: the losing side's entire contribution is discarded, not just
the conflicting part. Reproduced in a sandbox — main at {A:2, B:1}, our run at
{A:1, B:2, C:1}; after resolution main was {A:2, B:1}, the script reported
success, and B's second send and alarm C were gone. The next run mails them as
new, and the next, for ever.

A cumulative map has a correct merge, so these tests pin it.
"""

import json
import sys
from pathlib import Path

_REL = Path("src/main/java/org/ktronics/scripts")


def _scripts_dir():
    """Walk up to the repo root. Counting .parent lands outside the tree."""
    for parent in Path(__file__).resolve().parents:
        if (parent / _REL).is_dir():
            return parent / _REL
    raise AssertionError(f"could not find {_REL} above {__file__}")


SCRIPTS_DIR = _scripts_dir()
sys.path.insert(0, str(SCRIPTS_DIR))

from merge_alarm_state import merge_states  # noqa: E402


def test_the_exact_case_that_lost_data_in_the_sandbox():
    """Neither side's work disappears."""
    ours = {"A": {"send_count": 2}, "B": {"send_count": 1}}
    theirs = {"A": {"send_count": 1}, "B": {
        "send_count": 2}, "C": {"send_count": 1}}

    merged = merge_states(ours, theirs)

    assert set(merged) == {"A", "B", "C"}, "a key on either side must survive"
    assert merged["A"]["send_count"] == 2
    assert merged["B"]["send_count"] == 2, \
        "B's second send actually happened; it cannot be un-sent"
    assert merged["C"]["send_count"] == 1, "a brand-new alarm must not vanish"


def test_a_counter_never_goes_backwards():
    """send_count records emails that were really sent."""
    merged = merge_states({"X": {"send_count": 3}}, {"X": {"send_count": 1}})
    assert merged["X"]["send_count"] == 3


def test_ignored_latches_once_either_side_has_stopped():
    """
    If one run decided this alarm had said its piece, the other run must not
    restart it. Silence is the safer side of this particular doubt: the alarm
    has already been mailed three times.
    """
    merged = merge_states({"X": {"send_count": 3, "ignored": True}},
                          {"X": {"send_count": 2, "ignored": False}})
    assert merged["X"]["ignored"] is True


def test_first_seen_is_the_earliest_and_last_sent_the_latest():
    merged = merge_states(
        {"X": {"first_seen": "2026-10-12T08:00:00",
               "last_sent": "2026-10-12T08:00:00"}},
        {"X": {"first_seen": "2026-10-14T09:00:00",
               "last_sent": "2026-10-15T10:00:00"}},
    )
    assert merged["X"]["first_seen"] == "2026-10-12T08:00:00", \
        "the alarm was first seen when the earlier run saw it"
    assert merged["X"]["last_sent"] == "2026-10-15T10:00:00"


def test_descriptive_fields_survive_from_whichever_side_has_them():
    """A run that learned the customer name must not lose it to one that did not."""
    merged = merge_states(
        {"X": {"send_count": 1}},
        {"X": {"send_count": 1, "customer": "Gayan-IMH", "plant": "Imbulgoda"}},
    )
    assert merged["X"]["customer"] == "Gayan-IMH"
    assert merged["X"]["plant"] == "Imbulgoda"


def test_an_empty_side_is_not_a_reason_to_lose_the_other():
    assert merge_states({}, {"X": {"send_count": 1}}) == {"X": {"send_count": 1}}
    assert merge_states({"X": {"send_count": 1}}, {}) == {"X": {"send_count": 1}}


def test_a_record_of_an_unexpected_shape_does_not_raise():
    """
    A merge that throws leaves the conflict unresolved and the run red, which
    is worse than an imperfect merge of a record nobody understands.
    """
    merged = merge_states({"X": "not-a-record"}, {"X": {"send_count": 2}})
    assert merged["X"] == {"send_count": 2}


def test_the_merged_file_is_stable_on_disk(tmp_path):
    """
    Sorted keys and a trailing newline: an unstable key order would make every
    run's diff enormous and turn every concurrent run into a conflict.
    """
    import subprocess
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"b": {"send_count": 1}}), encoding="utf-8")
    b.write_text(json.dumps({"a": {"send_count": 1}}), encoding="utf-8")

    out = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "merge_alarm_state.py"),
         str(a), str(b)],
        capture_output=True, text=True, check=True).stdout

    assert out.endswith("\n")
    assert list(json.loads(out)) == ["a", "b"], "keys must come out sorted"
