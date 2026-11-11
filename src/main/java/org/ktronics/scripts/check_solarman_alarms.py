#!/usr/bin/env python3
"""
SOLARMAN alarms through the six-hour gate (UC22) - hourly (GitHub Actions).

The four plants of the "Ktronics KH" organisation on SOLARMAN Business (one
SRNE and three Deye-type inverters, none of them in Deye Cloud) had no alarm
path at all. SOLARMAN's own Alerts tab returned nothing for any of them in
Nov 2026, even for an inverter that had sent nothing since 24 May, and the
readings carry no fault codes. So, as UC20 does for MUST inverters, the alarms
are made here from each inverter's own 5-minute history, its live status and
its silence, and handed to the same six-hour gate and three-sends / four-hours
rule as UC3, UC18, UC19 and UC20.

  Grid loss (GRID) and backup failed / battery flat (SOC): UC20's own rules,
    on the outage's clock (check_must_telemetry.advance and open_alarms).
  Battery connection open (HARDWARE): an SRNE that reads SoC exactly 0 with
    its DC bus at 440 V or more, or a Deye-type whose battery reads under 10 V,
    on two readings in a row.
  Inverter output off with the grid present (HARDWARE): the output under
    100 V on two readings in a row while the grid reads 180 V or more.
  House load not on the inverter (LOAD): 25 W or less on two readings in a
    row with the output alive; one reading of 50 W or more ends it. A house on
    the manual changeover leaves the inverter idle at 10-16 W.
  Inverter fault (HARDWARE): a live status that says fault or error.
  No data (SILENT): the inverter has sent nothing for 30 minutes. Mailed, unlike
    the other vendors' COMMS alarms: on SOLARMAN, silence is the only sign of an
    inverter that has died or been switched off. Timed from the last data, or
    from the outage's start when the last reading showed no grid.
  SOLARMAN's Alerts tab: any open alert, classified by its text.

Each episode keeps its start in the device's state, so its alarm key stays the
same from run to run and a flicker does not start Send 1/3 again. An alarm is
claimed only from a fresh reading (30 minutes); a device that goes quiet raises
its no-data alarm instead.

Sign-in: see solarman_cloud.py. The job's tokens come from the
SOLARMAN_TOKENS_JSON secret; a renewal is written back to it with
--publish-secret. A sign-in that fails, or a renewal that cannot be saved, is
mailed at once (no six-hour wait), at most 3 times, 4 hours apart.

Usage:
    python check_solarman_alarms.py --tokens <file> [--publish-secret]
    python check_solarman_alarms.py --tokens <file> --dry-run   # local only: prints plant names
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import check_must_telemetry as must  # noqa: E402
import exclusions  # noqa: E402
import generate_device_alarms as gda  # noqa: E402
import solarman_cloud  # noqa: E402
from config import CREDENTIALS_PATH  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "solarman_alarms.json"
STATE_PATH = Path("state/solarman_alarms_state.json")
DEFAULT_ADMIN_EMAIL = "ktronicssolar@gmail.com"
EMAIL_CHANNEL = "emails.solarman_alarms_admin"
PLATFORM = "solarman"
DEVICE_PREFIX = must.DEVICE_PREFIX
TS_FMT = must.TS_FMT
SIGNIN_PID = "solarman"
SIGNIN_PN = "signin"
VOLTAGES = ("grid_v", "out_v", "bus_v", "batt_v")
FIELDS = ("grid_v", "out_v", "load_w", "soc", "bus_v", "batt_v")


# --------------------------------------------------------------------------- #
# Config, time
# --------------------------------------------------------------------------- #
def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_credentials(path=CREDENTIALS_PATH):
    """The `solarman` block and admin_email, if credentials.json is there. The job
    needs neither: the sign-in is the tokens file, the rest has defaults."""
    try:
        with open(path, encoding="utf-8") as f:
            creds = json.load(f)
    except (OSError, ValueError):
        return {}, DEFAULT_ADMIN_EMAIL
    return creds.get("solarman") or {}, creds.get("admin_email", DEFAULT_ADMIN_EMAIL)


def local_ts(epoch, tz_offset_min):
    return (datetime.fromtimestamp(float(epoch), timezone.utc)
            + timedelta(minutes=tz_offset_min)).strftime(TS_FMT)


def rule_on(cfg, station_id, rule):
    return rule not in (cfg.get("rules_off", {}).get(str(station_id)) or [])


# --------------------------------------------------------------------------- #
# Readings
# --------------------------------------------------------------------------- #
def norm_name(name):
    return re.sub(r"\s+", " ", str(name or "").replace("\xa0", " ")).strip().casefold()


def profile_for(product_id, cfg):
    name = cfg.get("products", {}).get(str(product_id))
    profile = cfg.get("profiles", {}).get(name) if name else None
    return name, profile


def parse_day(series, profile, tz_offset_min):
    """One day's history as rows {ts, grid_v, out_v, load_w, soc, bus_v, batt_v},
    oldest first. A field with several series (one per phase) takes the lowest
    voltage. A row with no grid reading is left out, as in UC20."""
    by_name = {}
    for item in series or []:
        points = {}
        for p in item.get("detailList") or []:
            value = must._num(p.get("value"))
            if p.get("collectionTime") is not None and value is not None:
                points[int(float(p["collectionTime"]))] = value
        by_name[norm_name(item.get("name"))] = points
    wanted = {field: [by_name.get(norm_name(n)) for n in profile.get(field, [])]
              for field in FIELDS}
    wanted = {f: [s for s in lists if s] for f, lists in wanted.items()}
    epochs = sorted({e for s in wanted["grid_v"] for e in s})
    rows = []
    for epoch in epochs:
        row = {"ts": local_ts(epoch, tz_offset_min)}
        for field in FIELDS:
            values = [s[epoch] for s in wanted[field] if epoch in s]
            row[field] = (min(values) if field in VOLTAGES else values[0]) if values else None
        if row["grid_v"] is not None:
            rows.append(row)
    return rows


def days_to_read(dev, last_data_local, today):
    """The local days whose history may hold readings not yet processed: from the
    newest one processed (on a first look, the day before the last data) to the
    day of the last data, never further back than yesterday."""
    end = date.fromisoformat(last_data_local[:10])
    start = date.fromisoformat(dev["last_ts"][:10]) if dev.get("last_ts") else end - timedelta(days=1)
    start = max(start, today - timedelta(days=1))
    days = []
    while start <= min(end, today):
        days.append(start)
        start += timedelta(days=1)
    return days


# --------------------------------------------------------------------------- #
# Episodes, one device
# --------------------------------------------------------------------------- #
def new_device_state():
    dev = must.new_device_state()
    dev.update({"episodes": {}, "fault": None, "last_row": None})
    return dev


def _ge(value, mark):
    return value is not None and value >= mark


def _lt(value, mark):
    return value is not None and value < mark


def episode_rules(profile, cfg):
    """(kind, opens(row), closes(row)) for every rule this profile has readings for."""
    present = {f for f in FIELDS if profile.get(f)}
    alive = cfg["output_alive_from_v"]
    dead = cfg["output_dead_below_v"]
    rules = []
    batt = cfg["battery_open"]
    if {"soc", "bus_v"} <= present:
        rules.append(("battery-open",
                      lambda r: r["soc"] == 0 and _ge(r["bus_v"], batt["soc_zero_bus_from_v"]),
                      lambda r: _ge(r["soc"], 0.5) and _lt(r["bus_v"], batt["soc_back_bus_below_v"])))
    elif "batt_v" in present:
        rules.append(("battery-open",
                      lambda r: _lt(r["batt_v"], batt["battery_missing_below_v"]),
                      lambda r: _ge(r["batt_v"], batt["battery_back_from_v"])))
    if "out_v" in present:
        rules.append(("output-off",
                      lambda r: _ge(r["grid_v"], cfg["grid_present_from_v"]) and _lt(r["out_v"], dead),
                      lambda r: _ge(r["out_v"], alive)))
    if {"out_v", "load_w"} <= present:
        load = cfg["load"]
        rules.append(("load-off",
                      lambda r: r["load_w"] is not None and r["load_w"] <= load["off_at_or_below_w"]
                      and _ge(r["out_v"], alive),
                      lambda r: _ge(r["load_w"], load["back_from_w"]) or _lt(r["out_v"], dead)))
    return rules


def step_episodes(dev, rows, rules, cfg):
    """Feed new rows, oldest first, through the device's episodes. Two rows in a
    row open one, timed from the first; a row that closes it ends it; a row that
    does neither keeps it as it was."""
    episodes = dev.setdefault("episodes", {})
    prev = dev.get("last_ts")
    for row in rows:
        if prev and row["ts"] <= prev:
            continue
        follows = bool(prev) and must.minutes_between(prev, row["ts"]) <= cfg["fresh_minutes"]
        for kind, opens, closes in rules:
            ep = episodes.get(kind)
            if ep and ep.get("start"):
                if closes(row):
                    del episodes[kind]
            elif opens(row):
                if ep and ep.get("pending") and follows:
                    episodes[kind] = {"start": ep["pending"], "start_known": ep.get("known", True)}
                else:
                    # After a first look or a gap, the episode may have begun
                    # before the first reading that shows it.
                    episodes[kind] = {"pending": row["ts"], "known": follows}
            else:
                episodes.pop(kind, None)
        prev = row["ts"]
    return dev


def advance(dev, rows, profile, cfg):
    """All of a device's state, from the rows newer than the last one processed."""
    new = [r for r in rows if not dev.get("last_ts") or r["ts"] > dev["last_ts"]]
    if not new:
        return dev
    step_episodes(dev, new, episode_rules(profile, cfg), cfg)
    must.advance(dev, new, cfg)
    dev["last_row"] = dict(new[-1])
    return dev


def fault_status(detail, cfg):
    """'<field>: <value>' of the first live status that reads as a fault, or None."""
    words = [w.casefold() for w in cfg.get("fault_words", [])]
    for category in (detail or {}).get("paramCategoryList") or []:
        for field in category.get("fieldList") or []:
            key, value = str(field.get("key") or ""), str(field.get("value") or "")
            if "status" in key.casefold() and any(w in value.casefold() for w in words):
                return f"{key}: {value}"
    return None


def note_fault(dev, status, seen_local):
    if status is None:
        dev["fault"] = None
    elif not dev.get("fault") or dev["fault"].get("status") != status:
        dev["fault"] = {"start": seen_local, "status": status}
    return dev


# --------------------------------------------------------------------------- #
# Alarms
# --------------------------------------------------------------------------- #
def detail_line(row):
    if not row:
        return "No reading yet"
    parts = []
    for field, label, unit, fmt in (("grid_v", "grid", "V", ".0f"), ("out_v", "output", "V", ".0f"),
                                    ("load_w", "load", "W", ".0f"), ("soc", "SoC", "%", ".0f"),
                                    ("batt_v", "battery", "V", ".1f"), ("bus_v", "bus", "V", ".0f")):
        if row.get(field) is not None:
            sep = "" if unit == "%" else " "
            parts.append(f"{label} {row[field]:{fmt}}{sep}{unit}")
    return f"Latest reading {row['ts'][:16]}: " + ", ".join(parts)


EPISODE_TEXT = {
    "battery-open": "Battery connection is open: the inverter has lost its battery since {since}",
    "output-off": "Inverter output is off with the grid present since {since}: "
                  "the house gets no power through the inverter",
    "load-off": "House load not on the inverter since {since}: the inverter is idle with its "
                "output alive (the house is most likely on the changeover)",
}


def open_alarms(dev, now_local, last_data_local, cfg, station_id):
    """The device's alarms open now, as dicts: kind, id, start, message, detail, clock."""
    alarms = []
    last = dev.get("last_row")
    detail = detail_line(last)
    silent = last_data_local is not None and \
        must.minutes_between(last_data_local, now_local) > cfg["no_data_minutes"]
    if silent:
        if rule_on(cfg, station_id, "silent"):
            message = f"Inverter has sent nothing to SOLARMAN since {last_data_local[:16]}"
            start, clock = last_data_local, "no-data"
            outage = dev.get("outage")
            if last and _lt(last.get("grid_v"), cfg["grid_absent_below_v"]) and outage:
                message += ("; its last reading showed no grid, so it has most likely shut down "
                            "on a flat battery, or the router lost power")
                start, clock = outage["start"], "outage"
            alarms.append({"kind": "silent", "id": f"silent@{must.compact(last_data_local)}",
                           "start": start, "clock": clock, "message": message, "detail": detail})
        return alarms
    if not last or must.minutes_between(last["ts"], now_local) > cfg["fresh_minutes"]:
        return alarms

    for spec in must.open_alarms(dev, now_local, cfg, grid_expected=True):
        if rule_on(cfg, station_id, spec["kind"]):
            alarms.append({**spec, "detail": detail})
    for kind, ep in sorted((dev.get("episodes") or {}).items()):
        if ep.get("start") and rule_on(cfg, station_id, kind):
            since = ep["start"][:16] + ("" if ep.get("start_known", True) else " or earlier")
            alarms.append({"kind": kind, "id": f"{kind}@{must.compact(ep['start'])}",
                           "start": ep["start"], "clock": "episode",
                           "message": EPISODE_TEXT[kind].format(since=since),
                           "detail": detail})
    fault = dev.get("fault")
    if fault and rule_on(cfg, station_id, "fault"):
        alarms.append({"kind": "fault", "id": f"fault@{must.compact(fault['start'])}",
                       "start": fault["start"], "clock": "first seen",
                       "message": f"Inverter fault: SOLARMAN shows {fault['status']}",
                       "detail": detail})
    return alarms


def live_keys(device, dev, last_data_local, now_local, cfg):
    """Keys still in play: every open episode's, even while its alarm reads
    closed for a moment (stale data), so its send count is not lost."""
    prefix = f"{device['pid']}:{device['pn']}"
    keys = must.live_keys(device, dev, True)
    for kind, ep in (dev.get("episodes") or {}).items():
        if ep.get("start"):
            keys.add(f"{prefix}:{kind}@{must.compact(ep['start'])}")
    if dev.get("fault"):
        keys.add(f"{prefix}:fault@{must.compact(dev['fault']['start'])}")
    if last_data_local and must.minutes_between(last_data_local, now_local) > cfg["no_data_minutes"]:
        keys.add(f"{prefix}:silent@{must.compact(last_data_local)}")
    return keys


def alert_alarms(station, alerts, tz_offset_min):
    """SOLARMAN Alerts-tab items as gate alarms. The tab was empty on every plant
    in Nov 2026, so the field names are read loosely."""
    out = []
    for a in alerts or []:
        alert_id = a.get("id") or a.get("alertId") or a.get("alarmId")
        when = a.get("alertTime") or a.get("startTime") or a.get("createTime")
        name = next((str(a[k]) for k in ("alertName", "name", "alertMsg", "description",
                                         "content", "alertDesc") if a.get(k)), "")
        if alert_id is None or when is None or not name:
            continue
        when = float(when)
        when = when / 1000 if when > 10_000_000_000 else when
        start = local_ts(when, tz_offset_min)
        # Keyed on the plant, not the device: the plant is read every run, so a
        # closed alert's key is pruned even when it was raised on a logger.
        device = {"pid": station["id"], "pn": "station", "alias": "plant"}
        on = f" on {a['deviceSn']}" if a.get("deviceSn") else ""
        spec = {"kind": "alert", "id": f"alert-{alert_id}", "start": start, "clock": "alert",
                "message": f"SOLARMAN alert: {name}",
                "detail": f"Raised by SOLARMAN{on} at {start[:16]}"}
        out.append((device, spec))
    return out


def signin_alarm(kind, message, utc_now):
    return {"pid": SIGNIN_PID, "pn": SIGNIN_PN, "id": kind,
            "customer_label": "SOLARMAN", "plant": "SOLARMAN sign-in of the GitHub job",
            "desc": message, "message": message, "detail": "", "kind": kind,
            "clock": "sign-in", "open": True, "started_at_utc": utc_now.isoformat()}


# --------------------------------------------------------------------------- #
# The fleet
# --------------------------------------------------------------------------- #
def read_station(client, station, state, cfg, now_local, today, tz_offset_min):
    """One plant: (gate alarms, devices read, live keys, counts). Raises
    SolarmanError on a call that failed: the plant then keeps its state."""
    sid = station["id"]
    label = station.get("name") or str(sid)
    alarms, read, keep = [], set(), set()
    counts = {"inverters": 0, "silent": 0, "no_profile": 0}
    updates = {}
    for inv in client.inverters(sid):
        sn = str(inv.get("deviceSn") or inv.get("id"))
        device = {"pid": sid, "pn": sn, "alias": inv.get("name") or sn}
        key = f"{sid}:{sn}"
        dev = state.get(DEVICE_PREFIX + key) or new_device_state()
        dev = json.loads(json.dumps(dev))
        for k, v in new_device_state().items():
            dev.setdefault(k, v)
        counts["inverters"] += 1
        last_data = local_ts(inv["collectionTime"], tz_offset_min) if inv.get("collectionTime") else None
        name, profile = profile_for(inv.get("productId"), cfg)
        if profile is None:
            counts["no_profile"] += 1
        fresh_data = last_data and (not dev.get("last_ts") or last_data > dev["last_ts"])
        if profile is not None and fresh_data:
            rows = []
            for day in days_to_read(dev, last_data, today):
                rows += parse_day(client.day(inv["id"], day), profile, tz_offset_min)
            advance(dev, sorted({r["ts"]: r for r in rows}.values(), key=lambda r: r["ts"]),
                    profile, cfg)
        if last_data and must.minutes_between(last_data, now_local) <= cfg["no_data_minutes"]:
            note_fault(dev, fault_status(client.detail(inv["id"], sid), cfg), last_data)
        else:
            counts["silent"] += 1
        updates[DEVICE_PREFIX + key] = dev
        read.add(key)
        keep |= live_keys(device, dev, last_data, now_local, cfg)
        for spec in open_alarms(dev, now_local, last_data, cfg, sid):
            alarms.append(must.to_gate_alarm(spec, device, label, tz_offset_min))
    if rule_on(cfg, sid, "alert"):
        for device, spec in alert_alarms(station, client.open_alerts(sid), tz_offset_min):
            alarms.append(must.to_gate_alarm(spec, device, label, tz_offset_min))
            keep.add(f"{device['pid']}:{device['pn']}:{spec['id']}")
    read.add(f"{sid}:station")
    state.update(updates)
    return alarms, read, keep, counts


def read_fleet(client, state, cfg, utc_now, tz_offset_min):
    now_local = (utc_now + timedelta(minutes=tz_offset_min)).strftime(TS_FMT)
    today = date.fromisoformat(now_local[:10])
    stations = client.stations()
    total = {"plants": len(stations), "read": 0, "failed": 0, "excluded": 0,
             "inverters": 0, "silent": 0, "no_profile": 0}
    alarms, read, keep = [], set(), set()
    for station in stations:
        if exclusions.is_customer_excluded(station.get("name") or ""):
            total["excluded"] += 1
            continue
        try:
            a, r, k, counts = read_station(client, station, state, cfg, now_local, today,
                                           tz_offset_min)
        except solarman_cloud.SignInError:
            raise
        except solarman_cloud.SolarmanError as e:
            # A plant that was not read keeps its state and every key it had:
            # a gap in the feed must not start its sent alarms again at Send 1/3.
            total["failed"] += 1
            print(f"  ✗ plant {station.get('id')}: {e}", file=sys.stderr)
            continue
        total["read"] += 1
        for name in ("inverters", "silent", "no_profile"):
            total[name] += counts[name]
        alarms += a
        read |= r
        keep |= k
    return alarms, read, keep, total


# --------------------------------------------------------------------------- #
# Email, main
# --------------------------------------------------------------------------- #
def format_email(items, utc_now, tz_offset_min):
    local = timezone(timedelta(minutes=tz_offset_min))
    lines = [
        f"SOLARMAN plants - {len(items)} alarm(s) from the inverters' own readings",
        f"Report time: {utc_now.astimezone(local):%Y-%m-%d %H:%M} (plant local time)",
        "",
    ]
    for item in sorted(items, key=lambda i: i["alarm"]["started_at_utc"]):
        alarm = item["alarm"]
        if alarm["pid"] == SIGNIN_PID:
            alarm_class, hint = "SIGN-IN", None
            when = "mailed at once: until it is fixed, nothing on SOLARMAN is watched"
        else:
            alarm_class = gda.alarm_triage.classify(alarm["message"], gda.ALARM_TRIAGE_CONFIG)
            hint = gda.ALARM_TRIAGE_CONFIG.get("escalation_hint", {}).get(alarm_class)
            started = datetime.fromisoformat(alarm["started_at_utc"])
            hours = (utc_now - started).total_seconds() / 3600
            when = f"{hours:.1f} h on the {alarm.get('clock', 'episode')} clock, class {alarm_class}"
        lines += [f"{alarm['plant']} ({alarm['pn']})", f"  {alarm['message']}"]
        if alarm.get("detail"):
            lines.append(f"  {alarm['detail']}")
        lines.append(f"  {when}, send {item['send_count'] + 1}/3")
        if hint:
            lines.append(f"  First check: {hint}")
        lines.append("")
    lines += [
        "Made from the inverters' 5-minute readings, live status and silence: SOLARMAN's",
        "Alerts tab reports none of these. An alarm goes out when it is still open 6 hours",
        "after it started (grid loss and backup on the outage's clock), then at most 3",
        "times, 4 hours apart.",
    ]
    return "\n".join(lines)


def _send(admin, to_send, utc_now, tz_offset, dry_run):
    body = format_email(to_send, utc_now, tz_offset)
    if dry_run:
        print("\n" + body)
        return True
    from email_utils import send_email_smtp
    from features import is_enabled
    if not is_enabled(EMAIL_CHANNEL):
        print(f"Feature flag '{EMAIL_CHANNEL}' is off - nothing mailed")
        return None
    subject = f"🔔 SOLARMAN: {len(to_send)} alarm(s) open 6h+"
    if all(i["alarm"]["pid"] == SIGNIN_PID for i in to_send):
        subject = "🔔 SOLARMAN: the GitHub job's sign-in needs attention"
    return send_email_smtp(admin, subject, body, channel=EMAIL_CHANNEL)


def main(argv=None, client_factory=None, utc_now=None, env=os.environ):
    ap = argparse.ArgumentParser(description="SOLARMAN alarms through the six-hour gate")
    ap.add_argument("--tokens", required=True, help="file holding the SOLARMAN token pair")
    ap.add_argument("--publish-secret", action="store_true",
                    help="write a renewed pair back to the SOLARMAN_TOKENS_JSON secret (CI)")
    ap.add_argument("--force-refresh", action="store_true",
                    help="renew the sign-in now, to prove the write-back")
    ap.add_argument("--dry-run", action="store_true",
                    help="local only: print what would be mailed; send nothing, save no state")
    ap.add_argument("--state-file", default=str(STATE_PATH))
    args = ap.parse_args(argv)

    cfg = load_config()
    sm_cfg, admin = load_credentials()
    tz_offset = int(sm_cfg.get("tz_offset_minutes") or must.tz_offset_minutes())
    utc_now = utc_now or datetime.now(timezone.utc)
    state = must.load_state(args.state_file)

    def on_refresh(tokens):
        solarman_cloud.mask_in_actions(tokens, env=env)
        solarman_cloud.save_tokens(args.tokens, tokens)
        if args.publish_secret:
            solarman_cloud.publish_tokens(tokens, env=env)

    signin = []
    alarms, read, keep, total = [], set(), set(), None
    tokens = solarman_cloud.load_tokens(args.tokens)
    try:
        factory = client_factory or (lambda t: solarman_cloud.SolarmanClient(
            t, base_url=sm_cfg.get("base_url"), on_refresh=on_refresh))
        client = factory(tokens)
        client.access_token(force_refresh=args.force_refresh)
        alarms, read, keep, total = read_fleet(client, state, cfg, utc_now, tz_offset)
    except solarman_cloud.SignInError as e:
        print(f"✗ SOLARMAN sign-in: {e}", file=sys.stderr)
        signin.append(("no-session", "The GitHub job has no working SOLARMAN sign-in, so "
                       "nothing on SOLARMAN is being watched. Seed a new one with a browser "
                       "login (Readme, UC22 sign-in)."))
        client = None
    except solarman_cloud.SolarmanError as e:
        # The plant list itself failed: save nothing, so no key is pruned.
        print(f"✗ SOLARMAN: {e}", file=sys.stderr)
        return 1
    if client is not None:
        if client.refresh_error:
            left = client.days_left()["access"]
            signin.append(("renew-refused", "SOLARMAN refused to renew the GitHub job's sign-in; "
                           f"the current one runs out in {left:.0f} days. Seed a new one with a "
                           "browser login (Readme, UC22 sign-in)."))
        if client.save_error:
            signin.append(("not-saved", "The GitHub job renewed its SOLARMAN sign-in but could "
                           f"not save it to the SOLARMAN_TOKENS_JSON secret ({client.save_error}). "
                           "Check the SOLARMAN_SECRET_WRITER token."))
        print(f"✓ sign-in: renewed {'yes' if client.refreshed else 'no'}, "
              f"{client.days_left()['access']:.0f} days left on the access token")

    if total:
        # Counts and keys only: Actions logs of this repo are public, names are not (#4).
        print(f"✓ {total['read']}/{total['plants']} plants read ({total['failed']} failed, "
              f"{total['excluded']} excluded); {total['inverters']} inverters, "
              f"{total['silent']} with no data, {total['no_profile']} of an unknown product")
        print(f"✓ {len(alarms)} alarm(s) open")
        must.prune_alarm_state(state, read, keep)

    signin_keys = {f"{SIGNIN_PID}:{SIGNIN_PN}:{kind}" for kind, _ in signin}
    for key in [k for k in state if k.startswith(f"{SIGNIN_PID}:{SIGNIN_PN}:")]:
        if key not in signin_keys:
            del state[key]
    passed = gda.triage_alarms(alarms, state, PLATFORM, utc_now)
    passed += [signin_alarm(kind, message, utc_now) for kind, message in signin]
    to_send = gda.filter_alarms_to_send(passed, state)
    print(f"✓ {len(passed)} passed the six-hour gate; {len(to_send)} to send")
    for item in to_send:
        print(f"  - {item['alarm_key']}")

    if not to_send:
        if not args.dry_run:
            must.save_state(args.state_file, state)
        return 0
    sent = _send(admin, to_send, utc_now, tz_offset, args.dry_run)
    if args.dry_run:
        return 0
    if sent:
        gda.update_alarm_state(state, to_send)
    must.save_state(args.state_file, state)
    return 0 if sent is not False else 1


if __name__ == "__main__":
    sys.exit(main())
