#!/usr/bin/env python3
"""
CONNECTIVITY check — which plants have stopped talking to us, and why.

This is deliberately NOT the production-anomaly check. check_anomaly.py asks
"is this plant producing less than it should?". This asks "are we still in
contact with it at all?", because for months the platform could not tell the
two apart: a customer whose WiFi or monitoring dongle is offline records
0.0000 kWh every day, exactly like a plant whose inverter has died — and the
solar system is usually fine.

A plant whose data has not advanced for --stale-days days (default 3) raises a
CONNECTIVITY alert that says the DATA LINK is down and says, in as many words,
that this is not a production fault and the plant must not be written off as a
dead system.

Alerts follow the same discipline as the device alarms: each plant's link-down
alert is sent at most 3 times, at least 4 hours apart, then auto-ignored so a
customer who never fixes their WiFi cannot flood the inbox for ever. State lives
in state/connectivity_state.json. A plant that starts reporting again has its
state cleared, so the next outage alerts from scratch.

Usage:
    python3 check_connectivity.py --data-dir data --out-dir alerts \
        --state-file state/connectivity_state.json --stale-days 3

Exit codes:
    0  nothing to send
    2  connectivity alerts to send (the workflow gates the email on this)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import connectivity
import exclusions
from check_anomaly import load_daily_series, utc_today
from config import CREDENTIALS_PATH
from features import is_enabled

# Same discipline as generate_device_alarms.py: 3 sends, 4 hours apart.
MAX_SENDS = 3
MIN_INTERVAL_HOURS = 4

# One state key for "the whole fleet went dark", kept apart from the per-plant
# keys so a platform-side outage never burns a customer's alert budget.
FLEET_KEY = "__fleet__"


def load_state(state_file):
    """Load connectivity notification state from JSON file."""
    path = Path(state_file)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state_file, state):
    """Save connectivity notification state to JSON file."""
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def load_plant_owners(credentials_file, platform=None):
    """
    Map a normalised customer label -> the label and email from credentials.json.

    Used only to name the customer to phone about their WiFi; a plant with no
    match is still reported, just without an owner.
    """
    try:
        with open(credentials_file, "r", encoding="utf-8") as f:
            creds = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

    accounts_key = "dessmonitor_accounts" if platform == "dessmonitor" else "accounts"
    owners = {}
    for account in creds.get(accounts_key, []):
        label = account.get("label", "")
        if not label:
            continue
        owners[exclusions.normalize(label)] = {
            "label": label,
            "email": account.get("email", ""),
        }
    return owners


def owner_for_plant(plant_key, owners):
    """Best-effort owner lookup: the longest customer label the key starts with."""
    key = exclusions.normalize(plant_key)
    best = None
    for norm, info in owners.items():
        if norm and key.startswith(norm):
            if best is None or len(norm) > len(best[0]):
                best = (norm, info)
    return best[1] if best else None


def filter_alerts_to_send(statuses, state, now=None,
                          max_sends=MAX_SENDS, min_interval_hours=MIN_INTERVAL_HOURS):
    """
    Which link-down plants we are allowed to mail about right now.

    Mirrors generate_device_alarms.filter_alarms_to_send: skip anything already
    auto-ignored, auto-ignore anything that has had its `max_sends`, and hold
    anything sent inside the last `min_interval_hours`.
    """
    now = now or datetime.now()
    to_send = []

    for status in statuses:
        key = status.plant_key
        entry = state.get(key, {
            "send_count": 0,
            "last_sent": None,
            "first_seen": now.isoformat(),
            "ignored": False,
        })

        if entry.get("ignored", False):
            continue

        if entry.get("send_count", 0) >= max_sends:
            entry["ignored"] = True
            state[key] = entry
            continue

        if entry.get("last_sent"):
            try:
                last_sent = datetime.fromisoformat(entry["last_sent"])
            except ValueError:
                last_sent = None
            if last_sent and (now - last_sent).total_seconds() / 3600 < min_interval_hours:
                continue

        to_send.append({
            "status": status,
            "plant_key": key,
            "send_count": entry.get("send_count", 0),
        })

    return to_send


def fleet_alert_due(state, now=None,
                    max_sends=MAX_SENDS, min_interval_hours=MIN_INTERVAL_HOURS):
    """Is a fleet-wide-outage alert due? Same 3-send / 4-hour rule, one key."""
    now = now or datetime.now()
    entry = state.get(FLEET_KEY)
    if entry is None:
        return True
    if entry.get("ignored") or entry.get("send_count", 0) >= max_sends:
        return False
    if entry.get("last_sent"):
        try:
            last_sent = datetime.fromisoformat(entry["last_sent"])
        except ValueError:
            return True
        if (now - last_sent).total_seconds() / 3600 < min_interval_hours:
            return False
    return True


def record_fleet_alert(state, fleet, now=None):
    """Count one fleet-wide-outage send against the single shared key."""
    now = now or datetime.now()
    entry = state.get(FLEET_KEY, {
        "send_count": 0,
        "first_seen": now.isoformat(),
        "ignored": False,
    })
    entry["send_count"] += 1
    entry["last_sent"] = now.isoformat()
    entry["plants_down"] = len(fleet.link_down)
    entry["note"] = "fleet-wide outage — suspect the collector or the portal API, not the customers"
    if entry["send_count"] >= MAX_SENDS:
        entry["ignored"] = True
    state[FLEET_KEY] = entry
    return state


def update_state(state, to_send, owners=None, now=None):
    """Record a send against each plant, auto-ignoring after MAX_SENDS."""
    now = now or datetime.now()
    owners = owners or {}

    for item in to_send:
        key = item["plant_key"]
        status = item["status"]

        if key not in state:
            state[key] = {
                "send_count": 0,
                "first_seen": now.isoformat(),
                "ignored": False,
            }

        state[key]["send_count"] += 1
        state[key]["last_sent"] = now.isoformat()

        # Human-readable, like the UC7 fields in device_alarms_state.json — the
        # state file should be readable without cross-referencing anything.
        owner = owner_for_plant(key, owners)
        state[key]["customer"] = owner["label"] if owner else "Unknown"
        state[key]["plant"] = key
        state[key]["days_stale"] = status.days_stale
        state[key]["last_data"] = str(status.last_data_date) if status.last_data_date else None
        state[key]["kind"] = status.kind

        if state[key]["send_count"] >= MAX_SENDS:
            state[key]["ignored"] = True

    return state


def clear_recovered(state, fleet):
    """
    Drop state for plants that are talking to us again.

    Without this a plant that went down, burned its 3 sends and later recovered
    would stay auto-ignored for ever and its next outage would be silent.
    """
    down = {s.plant_key for s in fleet.link_down}
    recovered = [key for key in list(state) if key != FLEET_KEY and key not in down]
    for key in recovered:
        del state[key]

    # The fleet-wide key clears the moment anything is reporting again.
    if FLEET_KEY in state and not fleet.fleet_wide_outage:
        del state[FLEET_KEY]

    return recovered


def format_connectivity_email(fleet, to_send, owners, platform_label="SHINEMONITOR",
                              fleet_wide=False):
    """Format the CONNECTIVITY report. Wording matters here — see module docstring."""
    owners = owners or {}
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    counts = fleet.counts

    lines = []
    lines.append("=" * 80)
    lines.append("║" + " " * 78 + "║")
    if fleet_wide:
        lines.append("║" + f"[{platform_label}] FLEET-WIDE OUTAGE".center(78) + "║")
    elif to_send:
        lines.append("║" + f"[{platform_label}] DATA LINK DOWN".center(78) + "║")
    else:
        lines.append("║" + f"[{platform_label}] CONNECTIVITY - ALL LINKS UP".center(78) + "║")
    lines.append("║" + " " * 78 + "║")
    lines.append("║" + "This is a CONNECTIVITY report, NOT a production fault".center(78) + "║")
    lines.append("║" + " " * 78 + "║")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"Report Time: {timestamp}")
    lines.append("")

    lines.append("┌─ FLEET STATUS " + "─" * 63 + "┐")
    lines.append("│")
    for line in fleet.counts_lines():
        lines.append(f"│ {line}")
    lines.append("│")
    lines.append("└" + "─" * 78 + "┘")
    lines.append("")

    if fleet_wide:
        lines.append("┌─ WHAT THIS MEANS " + "─" * 60 + "┐")
        lines.append("│")
        lines.append(f"│ EVERY monitored plant ({counts['link_down']}) has stopped sending data.")
        lines.append("│")
        lines.append("│ That is not 20-odd customers switching off their routers on the same day.")
        lines.append("│ Look at our own side first:")
        lines.append("│")
        lines.append("│   1. Did the data-collection workflow run, and did it succeed?")
        lines.append("│   2. Are the portal credentials still valid (auth failures return no data)?")
        lines.append("│   3. Is the manufacturer cloud API up?")
        lines.append("│")
        lines.append("│ No conclusion can be drawn about any individual customer's system or")
        lines.append("│ their link until collection is working again. Nothing here is evidence")
        lines.append("│ of a production fault.")
        lines.append("│")
        lines.append("│ This is reported once for the fleet, not once per plant, so no customer's")
        lines.append("│ alert budget is spent on a fault at our end.")
        lines.append("│")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")
        lines.append("┌─ PLANTS AFFECTED " + "─" * 60 + "┐")
        lines.append("│")
        for status in fleet.link_down:
            lines.append(f"│ • {status.plant_key}"
                         f"  (last reading {status.last_nonzero_date or 'never'},"
                         f" {status.days_stale} days ago)")
        lines.append("│")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")
    elif not to_send:
        lines.append("┌─ STATUS " + "─" * 69 + "┐")
        lines.append("│")
        if counts["link_down"]:
            lines.append(f"│ {counts['link_down']} plant(s) still have a dead data link, but every one has already")
            lines.append(f"│ been reported {MAX_SENDS} times and is now auto-ignored until it reports again.")
        else:
            lines.append("│ ✅ Every monitored plant is sending data.")
        lines.append("│")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")
    else:
        lines.append("┌─ WHAT THIS MEANS " + "─" * 60 + "┐")
        lines.append("│")
        lines.append("│ These plants have sent NO data for " + f"{fleet.stale_days}+ days.")
        lines.append("│")
        lines.append("│ That is a MONITORING problem, not a production fault. The usual cause is")
        lines.append("│ the customer's WiFi or the monitoring dongle being offline. The solar")
        lines.append("│ system itself is very probably running normally — do NOT record these as")
        lines.append("│ dead or failed systems, and do NOT quote them as lost production.")
        lines.append("│")
        lines.append("│ We cannot confirm production either way while the link is down. Restoring")
        lines.append("│ the link is the only way to find out.")
        lines.append("│")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        lines.append("┌─ PLANTS WITH A DEAD LINK " + "─" * 52 + "┐")
        lines.append("│")
        for item in to_send:
            status = item["status"]
            send_count = item["send_count"] + 1
            owner = owner_for_plant(status.plant_key, owners)
            lines.append(f"│ 📡 {status.plant_key}")
            lines.append(f"│    Customer:    {owner['label'] if owner else 'unmatched'}")
            if owner and owner.get("email"):
                lines.append(f"│    Contact:     {owner['email']}")
            lines.append(f"│    Last data:   {status.last_nonzero_date or 'never'}"
                         f"  ({status.days_stale} days ago)")
            lines.append(f"│    Symptom:     {status.meaning}")
            lines.append(f"│    Diagnosis:   DATA LINK DOWN — not a production fault")
            lines.append(f"│    Status:      Send #{send_count}/{MAX_SENDS}")
            lines.append("│")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        lines.append("┌─ RECOMMENDED ACTIONS " + "─" * 56 + "┐")
        lines.append("│")
        lines.append("│ 1. Ask the customer to confirm their WiFi / router is on and online")
        lines.append("│ 2. Check the monitoring dongle's LED and that it is still paired")
        lines.append("│ 3. Re-provision the dongle onto the current WiFi password if it changed")
        lines.append("│ 4. Only once data is flowing again can production be judged")
        lines.append("│ 5. If the customer will not keep the link alive, add the plant to")
        lines.append("│    src/main/java/org/ktronics/config/excluded_plants.json")
        lines.append("│")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

    if fleet.excluded:
        lines.append("┌─ EXCLUDED PLANTS (off the platform by decision) " + "─" * 29 + "┐")
        lines.append("│")
        for item in fleet.excluded:
            since = f" (since {item['since']})" if item.get("since") else ""
            lines.append(f"│ • {item['plant_key']}{since}")
            lines.append(f"│   Reason: {item['reason']}")
        lines.append("│")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

    lines.append("┌─ NOTES " + "─" * 70 + "┐")
    lines.append("│")
    lines.append(f"│ • A link is called down after {fleet.stale_days} days with no new reading")
    lines.append(f"│ • Each plant is reported {MAX_SENDS} times, {MIN_INTERVAL_HOURS} hours apart, then auto-ignored")
    lines.append("│ • A plant that starts reporting again is reset and will alert afresh")
    lines.append("│")
    lines.append("└" + "─" * 78 + "┘")
    lines.append("")
    lines.append("This is an automated connectivity monitoring report.")
    lines.append("")
    lines.append("For support: ktronicssolar@gmail.com")
    lines.append("")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Detect plants whose data link is down (distinct from a production fault)")
    parser.add_argument("--data-dir", default="data", help="Directory containing CSV files")
    parser.add_argument("--out-dir", default="alerts", help="Output directory")
    parser.add_argument("--state-file", default="state/connectivity_state.json",
                        help="Path to connectivity state JSON file")
    parser.add_argument("--stale-days", type=int, default=connectivity.DEFAULT_STALE_DAYS,
                        help="Days with no new reading before the link is called down (default: 3)")
    parser.add_argument("--platform", default=None, choices=["shinemonitor", "dessmonitor"],
                        help="Filter CSV files by platform prefix")
    parser.add_argument("--output-file", default=None, help="Custom output path for the report text")
    parser.add_argument("--json-output", default=None, help="Custom output path for the report JSON")
    parser.add_argument("--credentials", default=None,
                        help="Path to credentials JSON (default: config.CREDENTIALS_PATH)")
    parser.add_argument("--no-exclusions", action="store_true",
                        help="Ignore excluded_plants.json and report on every plant")

    args = parser.parse_args()

    platform_label = args.platform.upper() if args.platform else "SHINEMONITOR"

    print("━" * 78)
    print("📡 CONNECTIVITY CHECK (data link, NOT production)")
    print("━" * 78)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = "dessmonitor_" if args.platform == "dessmonitor" else ""
    output_txt = Path(args.output_file) if args.output_file else out_dir / f"{prefix}connectivity.txt"
    output_json = Path(args.json_output) if args.json_output else out_dir / f"{prefix}connectivity.json"
    output_txt.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    today = utc_today()
    # Load every series, then pick this platform's — see connectivity.select_platform
    # for why load_daily_series's own filter cannot be used for ShineMonitor.
    plants_daily = connectivity.select_platform(
        load_daily_series(Path(args.data_dir)), args.platform)
    print(f"[1/5] Loaded {len(plants_daily)} plant series from {args.data_dir}")

    # Exclusions are config, so say out loud what they did on this run.
    for line in exclusions.log_lines():
        print(f"[2/5] {line}")

    fleet = connectivity.classify_fleet(
        plants_daily, today, stale_days=args.stale_days,
        apply_exclusions=not args.no_exclusions,
    )
    print(f"[3/5] Classified fleet with a {args.stale_days}-day staleness threshold")
    for line in fleet.counts_lines():
        print(f"       {line}")

    credentials_path = Path(args.credentials) if args.credentials else CREDENTIALS_PATH
    owners = load_plant_owners(credentials_path, args.platform)

    state = load_state(args.state_file)
    recovered = clear_recovered(state, fleet)
    for key in recovered:
        print(f"       ✓ {key} is reporting again — connectivity state cleared")

    fleet_wide = fleet.fleet_wide_outage
    if fleet_wide:
        to_send = []
        sending = fleet_alert_due(state)
        print(f"[4/5] FLEET-WIDE OUTAGE: all {len(fleet.link_down)} plants silent — "
              "reporting once for the fleet, not once per plant")
    else:
        to_send = filter_alerts_to_send(fleet.link_down, state)
        sending = bool(to_send)
        print(f"[4/5] {len(to_send)} link-down alert(s) ready to send "
              f"(max {MAX_SENDS} sends, {MIN_INTERVAL_HOURS}h apart)")

    email_body = format_connectivity_email(fleet, to_send, owners, platform_label,
                                           fleet_wide=fleet_wide)
    output_txt.write_text(email_body, encoding="utf-8")

    report = fleet.as_dict()
    report["generated_at"] = datetime.now().isoformat() + "Z"
    report["fleet_wide_outage"] = fleet_wide
    report["alerting_now"] = (
        [s.plant_key for s in fleet.link_down] if fleet_wide
        else [item["status"].plant_key for item in to_send]
    )
    output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"       Report written to {output_txt}")

    if fleet_wide:
        if sending:
            state = record_fleet_alert(state, fleet)
    else:
        state = update_state(state, to_send, owners)
    save_state(args.state_file, state)
    print(f"[5/5] State saved to {args.state_file}")

    for item in to_send:
        status = item["status"]
        print(f"       📡 LINK DOWN {status.plant_key}: no data for {status.days_stale} days "
              f"({status.meaning}) — connectivity, not a production fault")

    print("━" * 78)

    if not is_enabled("alerts.connectivity_link_down"):
        # The report is still written for the logs; we just do not ask for mail.
        print("alerts.connectivity_link_down is off — report written, no email requested")
        print("━" * 78)
        return 0

    return 2 if sending else 0


if __name__ == "__main__":
    sys.exit(main())
