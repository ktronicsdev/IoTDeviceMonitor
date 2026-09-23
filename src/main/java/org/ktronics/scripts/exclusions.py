"""
Plant exclusions — the plants deliberately taken off the platform.

Single source of truth: src/main/java/org/ktronics/config/excluded_plants.json.
Read by check_anomaly.py, check_connectivity.py, generate_device_alarms.py,
generate_weekly_report.py and generate_admin_summary.py so that one edit takes a
plant out of alarms, reports and the fleet counts at the same time.

An exclusion is a business decision, not a symptom. A plant that has simply gone
quiet is NOT excluded — it is reported as a CONNECTIVITY / LINK DOWN plant and
keeps its place in the fleet counts. Only a plant the owner has dropped belongs
in the file.

Usage:
    import exclusions
    if exclusions.is_plant_excluded(plant_key):
        continue

    for line in exclusions.log_lines():
        print(line)

Matching is on a normalised key (lower-case, non-alphanumerics stripped), so
"Gayan-IMH", "gayan imh" and "gayanimh" are the same thing. A `customer` entry
also excludes every plant key that starts with that customer's normalised label,
which is how the data files are named (account "Abeetha" -> abeetha-plant-*.csv).

Unlike features.py this module FAILS CLOSED on a missing or malformed file: it
excludes nothing. Losing the file must never silently drop a paying customer off
the platform — the worst case is that an excluded plant briefly reappears, which
is visible, rather than a monitored plant silently vanishing, which is not.
"""

import json
import re
from pathlib import Path

# Anchored to this module, not the CWD — same reasoning as features.py: the
# workflows run from the repo root and pytest does not.
EXCLUSIONS_PATH = Path(__file__).resolve().parent.parent / "config" / "excluded_plants.json"

_cache = None


def normalize(value):
    """'Gayan-IMH' -> 'gayanimh'. The repo's standard label/key normalisation."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _load():
    """Read and cache excluded_plants.json. A missing/broken file excludes nothing."""
    global _cache
    if _cache is not None:
        return _cache

    try:
        with open(EXCLUSIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # Fail closed: no file means no exclusions, never "exclude everything".
        _cache = []
        return _cache

    entries = []
    for raw in data.get("excluded", []):
        if not isinstance(raw, dict):
            continue
        plant = raw.get("plant", "")
        customer = raw.get("customer", "")
        if not plant and not customer:
            continue  # an entry naming nothing would match everything
        entries.append({
            "plant": plant,
            "customer": customer,
            "reason": raw.get("reason", "no reason recorded"),
            "since": raw.get("since", ""),
            "plant_norm": normalize(plant),
            "customer_norm": normalize(customer),
        })

    _cache = entries
    return _cache


def reset_cache():
    """Drop the cached file — for tests that write a temporary exclusions file."""
    global _cache
    _cache = None


def entries():
    """Every configured exclusion, as a list of dicts."""
    return list(_load())


def match_for_plant(plant_key):
    """The exclusion entry covering `plant_key`, or None."""
    key = normalize(plant_key)
    if not key:
        return None

    for entry in _load():
        if entry["plant_norm"] and entry["plant_norm"] == key:
            return entry
        # A customer-level exclusion covers every plant file they own:
        # customer "Abeetha" -> abeetha-plant, abeetha-shop, ...
        if entry["customer_norm"] and key.startswith(entry["customer_norm"]):
            return entry
    return None


def match_for_customer(label):
    """The exclusion entry covering the account `label`, or None.

    Matches a `customer` entry outright, and also a plant-only entry whose plant
    key starts with the customer's label — excluding "abeetha-plant" when the
    account owns nothing else should stop that account's reports too.
    """
    key = normalize(label)
    if not key:
        return None

    for entry in _load():
        if entry["customer_norm"] and entry["customer_norm"] == key:
            return entry
        if entry["plant_norm"] and entry["plant_norm"].startswith(key):
            return entry
    return None


def is_plant_excluded(plant_key):
    """True if this data-file plant key is off the platform."""
    return match_for_plant(plant_key) is not None


def is_customer_excluded(label):
    """True if this credentials.json account label is off the platform."""
    return match_for_customer(label) is not None


def reason_for_plant(plant_key):
    """Why this plant is excluded, or '' if it is not."""
    entry = match_for_plant(plant_key)
    return entry["reason"] if entry else ""


def reason_for_customer(label):
    """Why this customer is excluded, or '' if they are not."""
    entry = match_for_customer(label)
    return entry["reason"] if entry else ""


def split_plants(plant_keys):
    """
    Partition plant keys into (kept, dropped).

    `dropped` carries the matching entry so the caller can name the reason in a
    log line rather than leaving a hole where a plant used to be.
    """
    kept, dropped = [], []
    for key in plant_keys:
        entry = match_for_plant(key)
        if entry:
            dropped.append({
                "plant_key": key,
                "reason": entry["reason"],
                "since": entry["since"],
            })
        else:
            kept.append(key)
    return kept, dropped


def log_lines(dropped=None):
    """
    Human-readable lines for the workflow log.

    Pass the `dropped` list from split_plants() to report what was actually
    skipped on this run; pass nothing to report the configured list.
    """
    if dropped is None:
        dropped = [
            {
                "plant_key": e["plant"] or e["customer"],
                "reason": e["reason"],
                "since": e["since"],
            }
            for e in _load()
        ]

    if not dropped:
        return ["Excluded plants: none configured (all monitored plants are in scope)"]

    lines = [f"Excluded plants: {len(dropped)} deliberately off the platform"]
    for item in dropped:
        since = f" (since {item['since']})" if item.get("since") else ""
        lines.append(f"  - EXCLUDED {item['plant_key']}{since}: {item['reason']}")
    return lines


if __name__ == "__main__":
    # `python3 exclusions.py` prints the live exclusion list — handy in logs.
    for line in log_lines():
        print(line)
