#!/usr/bin/env python3
"""
SolaX Cloud alarms through the six-hour gate (UC21) - hourly (GitHub Actions).

The SolaX twin of UC18 and UC19. Pulls every plant's ONGOING alarms from the
SolaX developer API and puts them through the same six-hour gate and
three-sends / four-hours rule. The admin hears about an alarm only when it is
still open six hours after it started: a two-hour CEB cut never reaches the
inbox; a grid fault that outlives six hours does.

Two rules are specific to SolaX, because every SolaX inverter in the fleet is
grid-tie and the Solis/Deye answer (leave grid-tie units out) would leave
nothing to watch:

  * DAYTIME ONLY. A grid-tie inverter shuts down at dusk, so an alarm that
    really ended at 18:00 reads "open" until it wakes next morning. The run
    therefore evaluates alarms only between DAY_START and DAY_END plant-local
    time, when every working inverter is awake and would have cleared a
    finished alarm. Outside the window it does nothing and leaves the state
    alone. An alarm still open at 09:00 after starting the evening before is
    real and goes through the gate as usual.
  * STALE CAP. An alarm that started more than STALE_DAYS ago is a record on a
    dead inverter, not a fault in progress (one such alarm was 53 days old when
    this was built); it would be mailed three times and then be ignored anyway.
    It is counted and skipped. An inverter that is simply offline is covered by
    the 3-day production-loss rule, not here.

Alarm times are plant-local (verified on the fleet, Nov 2026).

Config: the `solax` block of credentials.json (see solax_cloud.py).

Usage:
    python check_solax_alarms.py              # gate, email the admin, save state
    python check_solax_alarms.py --dry-run    # print what would be mailed; no email, no state
    python check_solax_alarms.py --any-hour   # ignore the daytime window (tests, manual look)
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import exclusions  # noqa: E402
import generate_device_alarms as gda  # noqa: E402
from config import CREDENTIALS_PATH  # noqa: E402
from solax_cloud import SolaxClient, SolaxError  # noqa: E402

STATE_PATH = Path("state/solax_alarms_state.json")
DEFAULT_ADMIN_EMAIL = "ktronicssolar@gmail.com"
EMAIL_CHANNEL = "emails.solax_alarms_admin"
PLATFORM = "solax"
DEFAULT_TZ_OFFSET_MIN = 330
STALE_DAYS = 14             # older than this: a dead inverter's record, not a fault in progress
DAY_START = 9               # plant-local hours between which alarms are evaluated
DAY_END = 17
LEVEL = {1: "notice", 2: "failure", 3: "critical"}
PLANT_PATH = "/openapi/v2/plant/page_plant_info"
ALARM_PATH = "/openapi/v2/alarm/page_alarm_info"


def load_solax_config():
    with open(CREDENTIALS_PATH, encoding="utf-8") as f:
        sx = json.load(f).get("solax")
    if not sx:
        raise SystemExit("No 'solax' section in credentials.json - see solax_cloud.py")
    return sx


def in_daytime(utc_now, tz_offset_min=DEFAULT_TZ_OFFSET_MIN):
    local = utc_now.astimezone(timezone(timedelta(minutes=tz_offset_min)))
    return DAY_START <= local.hour < DAY_END


def parse_local(text, tz_offset_min=DEFAULT_TZ_OFFSET_MIN):
    """'2026-09-18 10:59:31' (plant-local) -> aware UTC datetime, or None."""
    try:
        naive = datetime.strptime(str(text).strip()[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return (naive - timedelta(minutes=tz_offset_min)).replace(tzinfo=timezone.utc)


def is_open(raw):
    """alarmState 1 is ongoing and 0 closed. Without a usable state, an alarm
    with no end time is open: a gap in the feed must not close it."""
    state = str(raw.get("alarmState"))
    if state == "1":
        return True
    if state == "0":
        return False
    return not raw.get("alarmEndTime")


def to_gate_alarm(raw, plant, tz_offset_min=DEFAULT_TZ_OFFSET_MIN):
    """Add the fields UC3's state, keying and email code read to a raw SolaX alarm.

    The id is the code plus the start time: one occurrence keeps one key from
    poll to poll, and a later fault with the same code is a new alarm.
    """
    alarm = dict(raw)
    name = str(raw.get("alarmName") or "").strip()
    started = parse_local(raw.get("alarmStartTime"), tz_offset_min)
    alarm.update({
        "pid": plant.get("plantId"),
        "sn": raw.get("deviceSn"),
        "id": f"{raw.get('errorCode')}@{raw.get('alarmStartTime')}",
        "plant": plant.get("plantName") or "?",
        "alias": f"model {raw.get('deviceModel')}",
        "desc": name,
        "message": name,
        "vendor_code": str(raw.get("errorCode") or ""),
        "open": True,
    })
    if started is not None:
        alarm["started_at_utc"] = int(started.timestamp())
    return alarm


def select_alarms(entries, utc_now, tz_offset_min=DEFAULT_TZ_OFFSET_MIN):
    """Open, not-stale alarms of plants still on the platform.

    entries: (plant, raw alarm) pairs. Returns (alarms, counts).
    """
    counts = {"closed": 0, "stale": 0, "excluded": 0}
    alarms = []
    for plant, raw in entries:
        if not is_open(raw):
            counts["closed"] += 1
            continue
        started = parse_local(raw.get("alarmStartTime"), tz_offset_min)
        if started is not None and utc_now - started > timedelta(days=STALE_DAYS):
            counts["stale"] += 1
            continue
        if exclusions.is_plant_excluded(plant.get("plantName") or ""):
            counts["excluded"] += 1
            continue
        alarms.append(to_gate_alarm(raw, plant, tz_offset_min))
    return alarms, counts


def prune_state(state, live_keys):
    """Forget alarms that are no longer open (or have gone stale); one occurrence
    never reopens under the same key, so nothing is lost."""
    for key in [k for k in state if k not in live_keys]:
        del state[key]
    return state


def fetch_plants(client):
    return client.paged(PLANT_PATH, {"businessType": 1})


def fetch_alarms(client, plants):
    """(plant, alarm) for every ongoing alarm. Closed history is never read."""
    out = []
    for plant in plants:
        for item in client.paged(ALARM_PATH, {"plantId": plant["plantId"],
                                              "alarmState": 1, "businessType": 1}):
            out.append((plant, item))
    return out


def format_email(items, utc_now, tz_offset_min=DEFAULT_TZ_OFFSET_MIN):
    local = timezone(timedelta(minutes=tz_offset_min))
    lines = [
        f"SolaX Cloud - {len(items)} alarm(s) open 6 hours or more",
        f"Report time: {utc_now.astimezone(local):%Y-%m-%d %H:%M} (plant local time)",
        "",
    ]
    for item in sorted(items, key=lambda i: i["alarm"].get("alarmStartTime") or ""):
        alarm = item["alarm"]
        alarm_class = gda.alarm_triage.classify(alarm["message"], gda.ALARM_TRIAGE_CONFIG)
        hint = gda.ALARM_TRIAGE_CONFIG.get("escalation_hint", {}).get(alarm_class)
        level = LEVEL.get(alarm.get("alarmLevel"), alarm.get("alarmLevel"))
        started = parse_local(alarm.get("alarmStartTime"), tz_offset_min)
        if started is None:
            since = "  Start time missing from SolaX's record"
        else:
            hours = (utc_now - started).total_seconds() / 3600
            since = f"  Open since {alarm.get('alarmStartTime')}, {hours:.1f} h"
        lines += [
            f"{alarm['plant']} - {alarm['alias']} ({alarm['sn']})",
            f"  {alarm['desc']} (code {alarm.get('errorCode')}, {level}, class {alarm_class})",
            since,
            f"  Send {item['send_count'] + 1}/3",
        ]
        if hint:
            lines.append(f"  First check: {hint}")
        lines.append("")
    lines += [
        "Rule: an alarm is mailed only when it is still open 6 hours after it started",
        f"(the six-hour gate), then at most 3 times, 4 hours apart. Checked only {DAY_START}:00-{DAY_END}:00",
        f"plant time, when grid-tie inverters are awake; alarms older than {STALE_DAYS} days are skipped.",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="SolaX Cloud alarms through the six-hour gate")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be mailed; send nothing, save no state")
    ap.add_argument("--any-hour", action="store_true", help="ignore the daytime window")
    ap.add_argument("--state-file", default=str(STATE_PATH))
    args = ap.parse_args()

    cfg = load_solax_config()
    tz_offset = int(cfg.get("tz_offset_minutes", DEFAULT_TZ_OFFSET_MIN))
    utc_now = datetime.now(timezone.utc)
    if not args.any_hour and not in_daytime(utc_now, tz_offset):
        print(f"- Outside {DAY_START}:00-{DAY_END}:00 plant time: grid-tie inverters are asleep, "
              "nothing evaluated")
        return 0
    try:
        client = SolaxClient.from_config(cfg)
        plants = fetch_plants(client)
        if not plants:
            print("✗ SolaX Cloud returned no plants - check the app's authorised stations",
                  file=sys.stderr)
            return 1
        entries = fetch_alarms(client, plants)
    except SolaxError as e:
        # A half-read fleet must not prune the state of the plants it missed:
        # their sent alarms would start again at Send 1/3. Save nothing.
        print(f"✗ SolaX Cloud: {e}", file=sys.stderr)
        return 1

    alarms, counts = select_alarms(entries, utc_now, tz_offset)
    print(f"✓ {len(plants)} plants, {len(entries)} ongoing alarms; {len(alarms)} considered "
          f"(stale {counts['stale']}, closed {counts['closed']}, excluded {counts['excluded']})")

    state = gda.load_alarm_state(args.state_file)
    prune_state(state, {gda.create_alarm_key(a) for a in alarms})
    passed = gda.triage_alarms(alarms, state, PLATFORM, utc_now)
    to_send = gda.filter_alarms_to_send(passed, state)
    # Keys only: Actions logs of this repo are public, plant names are not (#4).
    print(f"✓ {len(passed)} passed the six-hour gate; {len(to_send)} to send")
    for item in to_send:
        print(f"  - {item['alarm_key']}")

    if not to_send:
        if not args.dry_run:
            gda.save_alarm_state(args.state_file, state)
        return 0

    body = format_email(to_send, utc_now, tz_offset)
    if args.dry_run:
        print("\n" + body)
        return 0

    from email_utils import send_email_smtp
    from features import is_enabled
    if not is_enabled(EMAIL_CHANNEL):
        print(f"Feature flag '{EMAIL_CHANNEL}' is off - nothing mailed")
        gda.save_alarm_state(args.state_file, state)
        return 0
    admin = cfg.get("admin_email", DEFAULT_ADMIN_EMAIL)
    subject = f"🔔 SolaX: {len(to_send)} alarm(s) open 6h+"
    sent = send_email_smtp(admin, subject, body, channel=EMAIL_CHANNEL)
    if sent:
        gda.update_alarm_state(state, to_send)
    gda.save_alarm_state(args.state_file, state)
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
