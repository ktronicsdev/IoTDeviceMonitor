#!/usr/bin/env python3
"""
SolisCloud alarms through the six-hour gate — hourly (GitHub Actions).

Pulls the account's alarm list from SolisCloud and puts every open alarm on a
hybrid inverter through the same six-hour gate and three-sends / four-hours
rule as the ShineMonitor and DessMonitor device alarms (UC3). The admin hears
about an alarm only when it is still open six hours after it started: a CEB cut
that lasts two hours never reaches the inbox; a grid outage or a battery alarm
that outlives six hours does.

Hybrids only. A grid-tie inverter shuts down at dusk, so its alarms cannot
clear until it wakes the next morning: a NO-Grid that really ended at 18:00
reads "open" until about 06:30 and would mail at midnight. A hybrid runs around
the clock, so its alarm times are real. Comms alarms ("Loss of internet
connection") are never mailed, as everywhere else on the gate.

Config: the `solis` block of credentials.json (same key as UC13 and UC17).

Usage:
    python check_solis_alarms.py              # gate, email the admin, save state
    python check_solis_alarms.py --dry-run    # print what would be mailed; no email, no state
"""

import argparse
import json
import re
import sys
import time
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
from solis_common import SolisClient, is_success  # noqa: E402

STATE_PATH = Path("state/solis_alarms_state.json")
DEFAULT_ADMIN_EMAIL = "ktronicssolar@gmail.com"
EMAIL_CHANNEL = "emails.solis_alarms_admin"
PLATFORM = "solis"
LOOKBACK_DAYS = 7          # an alarm open longer than this has long since been mailed 3x
REQUEST_GAP_S = 1.0        # stay well under the per-key rate limit
DEFAULT_TZ_OFFSET_MIN = 330

# Solis hybrids: S6-EH1P/EH3P (and the older RHI/RAI families). Grid-tie
# models read S5-GR/S6-GR/S5-GC and are left out (see the module docstring).
_HYBRID = re.compile(r"-EH\d|^RHI|^RAI", re.IGNORECASE)


def load_solis_config():
    with open(CREDENTIALS_PATH, encoding="utf-8") as f:
        solis = json.load(f).get("solis")
    if not solis:
        raise SystemExit("No 'solis' section in credentials.json — see solis/README_SOLIS.md")
    return solis


def is_hybrid(machine):
    """True for a hybrid model, False for grid-tie, None when the model is unknown."""
    if not machine:
        return None
    return bool(_HYBRID.search(str(machine)))


def is_open(raw):
    """alarmList state: "0" open, "1" processed by someone, "2" restored."""
    state = raw.get("state")
    return state is not False and str(state) == "0"


def to_gate_alarm(raw):
    """Add the fields UC3's state, keying and email code read to a raw Solis alarm.

    The id is the device's alarm code plus its start time, so one occurrence keeps
    one key from poll to poll whatever SolisCloud does with its own record ids.
    """
    alarm = dict(raw)
    alarm.update({
        "pid": raw.get("stationId"),
        "sn": raw.get("alarmDeviceSn"),
        "id": f"{raw.get('alarmCode')}@{raw.get('alarmBeginTime')}",
        "plant": raw.get("stationName") or "?",
        "alias": raw.get("machine") or "",
        "desc": raw.get("alarmMsg") or "",
    })
    return alarm


def select_alarms(raw_alarms):
    """Open alarms on hybrid inverters of plants still on the platform.

    Returns (alarms, counts). An alarm whose inverter model is missing is kept:
    a gap in the feed must never silence alerting. In practice only SolisCloud's
    own "Loss of internet connection" (1D4C2) comes without one, and the gate
    never mails that.
    """
    counts = {"closed": 0, "grid_tie": 0, "unknown_model": 0, "excluded": 0}
    alarms = []
    for raw in raw_alarms:
        if not is_open(raw):
            counts["closed"] += 1
            continue
        hybrid = is_hybrid(raw.get("machine"))
        if hybrid is False:
            counts["grid_tie"] += 1
            continue
        if hybrid is None:
            counts["unknown_model"] += 1
        if exclusions.is_plant_excluded(raw.get("stationName") or ""):
            counts["excluded"] += 1
            continue
        alarms.append(to_gate_alarm(raw))
    return alarms, counts


def prune_state(state, live_keys):
    """Forget alarms that are no longer open. A Solis alarm never reopens under the
    same key, so nothing is lost, and the file does not grow with every CEB blip."""
    for key in [k for k in state if k not in live_keys]:
        del state[key]
    return state


def fetch_alarms(client, today, days=LOOKBACK_DAYS):
    """Every alarm that began in the last `days` days (paged)."""
    out, page = [], 1
    begin = (today - timedelta(days=days)).isoformat()
    while True:
        resp = client.post("/v1/api/alarmList", {
            "pageNo": page, "pageSize": 100,
            "alarmBeginTime": begin, "alarmEndTime": today.isoformat(),
        })
        if not is_success(resp):
            raise SystemExit(f"alarmList failed: {resp.get('msg') or resp.get('code')}")
        data = resp.get("data") or {}
        page_data = data.get("page") or data
        records = page_data.get("records") or []
        out += records
        if len(records) < 100 or len(out) >= int(page_data.get("total") or 0):
            return out
        page += 1
        time.sleep(REQUEST_GAP_S)


def format_email(items, utc_now, tz_offset_min=DEFAULT_TZ_OFFSET_MIN):
    local = timezone(timedelta(minutes=tz_offset_min))
    lines = [
        f"SolisCloud — {len(items)} alarm(s) open 6 hours or more on hybrid inverters",
        f"Report time: {utc_now.astimezone(local):%Y-%m-%d %H:%M} (plant local time)",
        "",
    ]
    for item in sorted(items, key=lambda i: int(i["alarm"].get("alarmBeginTime") or 0)):
        alarm = item["alarm"]
        record = gda.alarm_triage.normalize(alarm, "Solis", gda.ALARM_TRIAGE_CONFIG)
        started = record["started_at_utc"]
        hours = (utc_now - started).total_seconds() / 3600
        hint = gda.ALARM_TRIAGE_CONFIG.get("escalation_hint", {}).get(record["class"])
        lines += [
            f"{alarm['plant']} — {alarm['alias'] or '?'} ({alarm['sn']})",
            f"  {alarm['desc']} (code {alarm.get('alarmCode')}, class {record['class']})",
            f"  Open since {started.astimezone(local):%Y-%m-%d %H:%M}, {hours:.1f} h",
            f"  Send {item['send_count'] + 1}/3",
        ]
        if hint:
            lines.append(f"  First check: {hint}")
        lines.append("")
    lines += [
        "Rule: an alarm is mailed only when it is still open 6 hours after it started",
        "(the six-hour gate), then at most 3 times, 4 hours apart. Comms alarms are",
        "never mailed. Grid-tie inverters are left out: their alarms stay open overnight.",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="SolisCloud alarms through the six-hour gate")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be mailed; send nothing, save no state")
    ap.add_argument("--state-file", default=str(STATE_PATH))
    args = ap.parse_args()

    cfg = load_solis_config()
    client = SolisClient(cfg["key_id"], cfg["key_secret"],
                         **({"base_url": cfg["api_base"]} if cfg.get("api_base") else {}))
    tz_offset = int(cfg.get("tz_offset_minutes", DEFAULT_TZ_OFFSET_MIN))
    utc_now = datetime.now(timezone.utc)
    today = (utc_now + timedelta(minutes=tz_offset)).date()

    raw = fetch_alarms(client, today)
    alarms, counts = select_alarms(raw)
    print(f"✓ {len(raw)} alarms in the last {LOOKBACK_DAYS} days; {len(alarms)} open on "
          f"hybrids (closed {counts['closed']}, grid-tie {counts['grid_tie']}, "
          f"model unknown {counts['unknown_model']}, excluded {counts['excluded']})")

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
        # Switched off on purpose: not a failure, and nothing counts as sent.
        print(f"Feature flag '{EMAIL_CHANNEL}' is off — nothing mailed")
        gda.save_alarm_state(args.state_file, state)
        return 0
    admin = cfg.get("admin_email", DEFAULT_ADMIN_EMAIL)
    subject = f"🔔 Solis: {len(to_send)} alarm(s) open 6h+"
    sent = send_email_smtp(admin, subject, body, channel=EMAIL_CHANNEL)
    if sent:
        gda.update_alarm_state(state, to_send)
    gda.save_alarm_state(args.state_file, state)
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
