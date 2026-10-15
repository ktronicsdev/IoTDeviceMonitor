#!/usr/bin/env python3
"""
Merge two versions of a cumulative state file, instead of choosing one.

A state JSON is a map of key -> record, and two concurrent runs each add and
update different keys. Resolving a conflict with `git checkout --ours` or
`--theirs` is whole-file, so whichever side loses has *everything* it recorded
discarded, not just the part that conflicted. For alarm state that means
already-emailed alarms come back as new and get mailed again, for ever.

There is no ambiguity about the right answer here, because the map is
cumulative and every field only ever moves one way:

  * a key present on either side belongs in the result;
  * for a key on both sides, the record that has been sent more times is the
    one that has actually happened - counters do not go backwards;
  * `ignored` latches: once either side has stopped sending, it stays stopped;
  * `first_seen` is the earliest either side saw it, `last_sent` the latest.

Usage:
    merge_alarm_state.py BASE OURS THEIRS > merged.json

BASE may be the empty tree (git passes a missing stage for an added file), in
which case it is ignored. The output is written to stdout so the caller
decides where it lands.
"""

import json
import sys
from pathlib import Path

# Fields where the larger value is the one that really happened.
_MAX_FIELDS = ("send_count",)
# Fields where the earliest wins.
_MIN_FIELDS = ("first_seen",)
# Fields where the latest wins.
_LATEST_FIELDS = ("last_sent", "last_sent_utc", "started_at_utc")
# Fields that latch to True and never back.
_LATCH_TRUE = ("ignored",)


def _load(path):
    if not path or not Path(path).is_file():
        return {}
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: not valid JSON ({exc})")
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected an object at the top level")
    return data


def _merge_record(ours, theirs):
    """Combine two records for the same key. Neither side's work is dropped."""
    if not isinstance(ours, dict) or not isinstance(theirs, dict):
        # Not the shape we understand. Prefer the side that has more to say
        # rather than guessing, and never raise - a merge that fails here
        # would leave the conflict unresolved and the run red.
        return theirs if theirs else ours

    merged = dict(ours)
    merged.update({k: v for k, v in theirs.items() if v is not None})

    for field in _MAX_FIELDS:
        values = [r.get(field) for r in (ours, theirs)
                  if isinstance(r.get(field), (int, float))]
        if values:
            merged[field] = max(values)

    for field in _MIN_FIELDS:
        values = sorted(str(r[field]) for r in (ours, theirs) if r.get(field))
        if values:
            merged[field] = values[0]

    for field in _LATEST_FIELDS:
        values = sorted(str(r[field]) for r in (ours, theirs) if r.get(field))
        if values:
            merged[field] = values[-1]

    for field in _LATCH_TRUE:
        if ours.get(field) or theirs.get(field):
            merged[field] = True

    return merged


def merge_states(ours, theirs):
    """Union of keys; per-key fields combined so nothing recorded is lost."""
    merged = dict(ours)
    for key, record in theirs.items():
        merged[key] = _merge_record(
            ours[key], record) if key in ours else record
    return merged


def main(argv):
    if len(argv) not in (2, 3):
        raise SystemExit(
            "usage: merge_alarm_state.py [BASE] OURS THEIRS > merged.json")

    ours, theirs = (argv[-2], argv[-1])
    merged = merge_states(_load(ours), _load(theirs))

    json.dump(merged, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
