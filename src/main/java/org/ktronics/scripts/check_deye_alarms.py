#!/usr/bin/env python3
"""
Deye Cloud alarms through the six-hour gate (UC19) - hourly (GitHub Actions).

The Deye twin of UC18. Pulls every plant's alert list from Deye Cloud and puts
every open alarm on a hybrid or off-grid inverter through the same six-hour
gate and three-sends / four-hours rule as the ShineMonitor, DessMonitor and
SolisCloud alarms. The admin hears about an alarm only when it is still open
six hours after it started: a two-hour CEB cut never reaches the inbox; a grid
fault or a battery fault that outlives six hours does.

Grid-tie inverters are left out, as on Solis: one shuts down at dusk, so its
alarm cannot clear until it wakes next morning, and an alarm that really ended
at 18:00 would read "open" all night. Hybrids and off-grid units run around the
clock, so their alarm times are real. An inverter whose product id is not yet
known is kept: a gap in the feed must never silence alerting.

Deye has no comms alarm in its alert list (a dead logger is a device status
there, not an alert), so nothing here needs the COMMS rule; it still applies.

Config: the `deye` block of credentials.json (see deye_cloud.py).

Usage:
    python check_deye_alarms.py              # gate, email the admin, save state
    python check_deye_alarms.py --dry-run    # print what would be mailed; no email, no state
"""

import argparse
import json
import re
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
from deye_cloud import DeyeClient, DeyeError  # noqa: E402

STATE_PATH = Path("state/deye_alarms_state.json")
DEFAULT_ADMIN_EMAIL = "ktronicssolar@gmail.com"
EMAIL_CHANNEL = "emails.deye_alarms_admin"
PLATFORM = "deye"
LOOKBACK_DAYS = 7           # an alarm open longer than this has long since been mailed 3x
DEFAULT_TZ_OFFSET_MIN = 330
PAGE_SIZE = 200
STATIONS_PER_DEVICE_CALL = 10   # /station/device answers "list too long" above 10

# Deye product ids, read off the fleet's /station/device on 5 Nov 2026. The
# one grid-tie unit (0_5406_1) sits on the only plant typed EXCESS rather than
# BATTERY_BACKUP; the seven 0_5434_1 units are the fleet's SUN-xK-OG01 off-grids.
PRODUCTS = {
    "0_5407_1": "1-phase hybrid",
    "0_5411_1": "3-phase LV hybrid",
    "0_5412_1": "3-phase HV hybrid",
    "0_5434_1": "off-grid",
    "0_5406_1": "grid-tie",
    "0_5420_3": "battery",
    "0_5421_3": "battery",
    "0_D0002_18": "logger",
}
GRID_TIE_PRODUCTS = frozenset({"0_5406_1"})
LEVEL = {0: "notice", 1: "warning", 2: "failure"}


def load_deye_config():
    with open(CREDENTIALS_PATH, encoding="utf-8") as f:
        deye = json.load(f).get("deye")
    if not deye:
        raise SystemExit("No 'deye' section in credentials.json - see deye_cloud.py")
    return deye


def is_grid_tie(product_id):
    """True for a grid-tie product, False for a known other, None when unknown."""
    if not product_id or product_id not in PRODUCTS:
        return None
    return product_id in GRID_TIE_PRODUCTS


def is_open(raw):
    """status 1 is open and 0 closed. Without a usable status, an alarm with no
    end time is open: a gap in the feed must not close it."""
    status = str(raw.get("status"))
    if status == "1":
        return True
    if status == "0":
        return False
    return not raw.get("alertEndTime")


def clean_name(name):
    """Deye's alert names carry no-break spaces and doubled spaces
    ('W3\\xa0Grid_Phase_Wrong', 'BusUnbalance_Fault   F26')."""
    return re.sub(r"\s+", " ", str(name or "").replace("\xa0", " ")).strip()


def epoch_s(value):
    """Epoch seconds from seconds or milliseconds (number or numeric string), else None."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return int(v / 1000) if v > 1e11 else int(v)


def to_gate_alarm(raw, station, product_id=None):
    """Add the fields UC3's state, keying and email code read to a raw Deye alert.

    The id is the alert's protocol word and code plus its start time: Deye's
    alertCode is a bit number inside one protocol word (code 12 is F13 in ERR3
    and F45 in ERR5), so the code alone is not one fault. One occurrence keeps
    one key from poll to poll.
    """
    alarm = dict(raw)
    name = clean_name(raw.get("alertName"))
    alarm.update({
        "pid": station.get("id"),
        "sn": raw.get("deviceSn"),
        "id": f"{raw.get('protocolName') or '?'}-{raw.get('alertCode')}@{raw.get('alertStartTime')}",
        "plant": station.get("name") or "?",
        "alias": PRODUCTS.get(product_id) or str(raw.get("deviceType") or "").lower(),
        "desc": name,
        "message": name,
        # Deye's own status reads 1 for open, and the gate's generic reader
        # takes 1 as closed (ShineMonitor's convention). Only open alarms get
        # this far, so say so plainly.
        "open": True,
    })
    started = epoch_s(raw.get("alertStartTime"))
    if started is not None:
        # An epoch the gate reads as UTC. Without one the gate cannot time the
        # alarm, and passes it through rather than hold it.
        alarm["started_at_utc"] = started
    return alarm


def select_alarms(entries, products):
    """Open alarms on non-grid-tie inverters of plants still on the platform.

    entries: (station, raw alert) pairs. products: deviceSn -> productId.
    Returns (alarms, counts).
    """
    counts = {"closed": 0, "grid_tie": 0, "unknown_product": 0, "excluded": 0}
    alarms = []
    for station, raw in entries:
        if not is_open(raw):
            counts["closed"] += 1
            continue
        product_id = products.get(str(raw.get("deviceSn")))
        grid_tie = is_grid_tie(product_id)
        if grid_tie:
            counts["grid_tie"] += 1
            continue
        if grid_tie is None:
            counts["unknown_product"] += 1
        if exclusions.is_plant_excluded(station.get("name") or ""):
            counts["excluded"] += 1
            continue
        alarms.append(to_gate_alarm(raw, station, product_id))
    return alarms, counts


def prune_state(state, live_keys):
    """Forget alarms that are no longer open. One occurrence never reopens under
    the same key, so nothing is lost, and the file does not grow with every blip."""
    for key in [k for k in state if k not in live_keys]:
        del state[key]
    return state


def _paged(client, path, body, items_key):
    out, page = [], 1
    while True:
        resp = client.post(path, {**body, "page": page, "size": PAGE_SIZE})
        items = resp.get(items_key) or []
        out += items
        if len(items) < PAGE_SIZE or len(out) >= int(resp.get("total") or 0):
            return out
        page += 1


def fetch_stations(client):
    return _paged(client, "/station/list", {}, "stationList")


def fetch_products(client, station_ids):
    """deviceSn -> productId for every device of the given plants."""
    products = {}
    for i in range(0, len(station_ids), STATIONS_PER_DEVICE_CALL):
        batch = [int(s) for s in station_ids[i:i + STATIONS_PER_DEVICE_CALL]]
        for d in _paged(client, "/station/device", {"stationIds": batch}, "deviceListItems"):
            if d.get("deviceSn"):
                products[str(d["deviceSn"])] = d.get("productId")
    return products


def fetch_alarms(client, stations, now_s, days=LOOKBACK_DAYS):
    """(station, alert) for every alert that began in the last `days` days."""
    body = {"startTimestamp": int(now_s) - days * 86400, "endTimestamp": int(now_s)}
    out = []
    for station in stations:
        items = _paged(client, "/station/alertList",
                       {**body, "stationId": int(station["id"])}, "stationAlertItems")
        out += [(station, item) for item in items]
    return out


def format_email(items, utc_now, tz_offset_min=DEFAULT_TZ_OFFSET_MIN):
    local = timezone(timedelta(minutes=tz_offset_min))
    lines = [
        f"Deye Cloud - {len(items)} alarm(s) open 6 hours or more on hybrid and off-grid inverters",
        f"Report time: {utc_now.astimezone(local):%Y-%m-%d %H:%M} (plant local time)",
        "",
    ]
    for item in sorted(items, key=lambda i: epoch_s(i["alarm"].get("alertStartTime")) or 0):
        alarm = item["alarm"]
        # The same class the gate gave it: Deye has no vendor-code map (its codes
        # repeat across protocol words), so the class comes from the text alone.
        alarm_class = gda.alarm_triage.classify(alarm["message"], gda.ALARM_TRIAGE_CONFIG)
        hint = gda.ALARM_TRIAGE_CONFIG.get("escalation_hint", {}).get(alarm_class)
        level = LEVEL.get(alarm.get("level"), alarm.get("level"))
        started_s = epoch_s(alarm.get("alertStartTime"))
        if started_s is None:
            since = "  Start time missing from Deye's record"
        else:
            started = datetime.fromtimestamp(started_s, timezone.utc)
            hours = (utc_now - started).total_seconds() / 3600
            since = f"  Open since {started.astimezone(local):%Y-%m-%d %H:%M}, {hours:.1f} h"
        lines += [
            f"{alarm['plant']} - {alarm['alias'] or '?'} ({alarm['sn']})",
            f"  {alarm['desc']} ({alarm.get('protocolName')} code {alarm.get('alertCode')}, "
            f"{level}, class {alarm_class})",
            since,
            f"  Send {item['send_count'] + 1}/3",
        ]
        if hint:
            lines.append(f"  First check: {hint}")
        lines.append("")
    lines += [
        "Rule: an alarm is mailed only when it is still open 6 hours after it started",
        "(the six-hour gate), then at most 3 times, 4 hours apart. Grid-tie inverters",
        "are left out: their alarms stay open overnight.",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Deye Cloud alarms through the six-hour gate")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be mailed; send nothing, save no state")
    ap.add_argument("--state-file", default=str(STATE_PATH))
    args = ap.parse_args()

    cfg = load_deye_config()
    tz_offset = int(cfg.get("tz_offset_minutes", DEFAULT_TZ_OFFSET_MIN))
    utc_now = datetime.now(timezone.utc)
    try:
        client = DeyeClient.from_config(cfg)
        stations = fetch_stations(client)
        if not stations:
            # A token without company_id sees only the account's own plants,
            # and none at all is never right. Keep the state as it is.
            print("✗ Deye Cloud returned no stations - check company_id", file=sys.stderr)
            return 1
        products = fetch_products(client, [s["id"] for s in stations])
        entries = fetch_alarms(client, stations, utc_now.timestamp())
    except DeyeError as e:
        # A half-read fleet must not prune the state of the plants it missed:
        # their sent alarms would start again at Send 1/3. Save nothing.
        print(f"✗ Deye Cloud: {e}", file=sys.stderr)
        return 1

    alarms, counts = select_alarms(entries, products)
    print(f"✓ {len(stations)} plants, {len(entries)} alarms in the last {LOOKBACK_DAYS} days; "
          f"{len(alarms)} open (closed {counts['closed']}, grid-tie {counts['grid_tie']}, "
          f"product unknown {counts['unknown_product']}, excluded {counts['excluded']})")

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
        print(f"Feature flag '{EMAIL_CHANNEL}' is off - nothing mailed")
        gda.save_alarm_state(args.state_file, state)
        return 0
    admin = cfg.get("admin_email", DEFAULT_ADMIN_EMAIL)
    subject = f"🔔 Deye: {len(to_send)} alarm(s) open 6h+"
    sent = send_email_smtp(admin, subject, body, channel=EMAIL_CHANNEL)
    if sent:
        gda.update_alarm_state(state, to_send)
    gda.save_alarm_state(args.state_file, state)
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
