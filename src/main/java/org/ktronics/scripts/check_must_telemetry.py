#!/usr/bin/env python3
"""
MUST inverter telemetry alarms through the six-hour gate (UC20) - hourly.

ShineMonitor's warning list does not report a MUST inverter losing the grid.
Of the 157 alarms UC3 had tracked from it by 10 Nov 2026, not one was a grid
loss, while the inverters' own 5-minute readings showed 32 grid-off episodes in
14 days. A 25-hour outage on 8-9 Nov ran one customer's 24 V battery down to
21.9 V and shut the inverter off twice before sunrise; eleven alarm polls in
that window got "no device alarms", so the six-hour gate had nothing to hold.

So the alarms are made here from the readings, and handed to the same six-hour
gate and three-sends / four-hours rule as UC3, UC18 and UC19.

  Grid loss (class GRID). Two readings in a row below 100 V on the inverter's
  AC input open an outage, timed from the first; a reading of 180 V or more
  closes it. The alarm is open only while a fresh reading (30 minutes) still
  shows no grid: a logger that goes quiet is never taken as proof that the
  grid is still off.

  Backup failed (class SOC), on the outage's own clock, so once the outage is
  six hours old it goes out at the next run. With no grid, it is open when
    * the battery's last reading is flat (22.5 V on 24 V, 45.5 V on 48 V) and
      nothing has come for 20 minutes: the inverter has shut down and taken
      the logger with it;
    * two readings in a row show the battery flat: it is about to; or
    * two readings in a row show the AC output below 100 V.
  A load of 0 W is not a failure: on three nights in Oct 2026 one inverter
  held 229.8 V at its output with nothing drawing from it.

Every plant is expected to have grid. A plant built without an AC input is
listed in config/must_telemetry.json and raises no grid-loss alarm; its
battery is still watched, on the backup episode's own clock.

Usage:
    python check_must_telemetry.py              # gate, email the admin, save state
    python check_must_telemetry.py --dry-run    # print what would be mailed; no email, no state
"""

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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

API_URL = "https://web.shinemonitor.com/public/"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "must_telemetry.json"
STATE_PATH = Path("state/must_telemetry_state.json")
DEFAULT_ADMIN_EMAIL = "ktronicssolar@gmail.com"
EMAIL_CHANNEL = "emails.must_telemetry_admin"
PLATFORM = "shinemonitor"
PAGE_SIZE = 100          # queryDeviceDataOneDayPaging returns at most 100 rows a page, newest first
MAX_PAGES_PER_DAY = 5    # a 5-minute logger writes about 290 rows a day
TIMEOUTS_S = (15, 25)    # one try, one retry: the vendor-silence rule of shinemonitor_common.sh
RETRY_PAUSE_S = 3
# Accounts read at once. Six lost 9 of 23 logins on the first CI run (10 Nov 2026),
# where UC3, one account at a time, loses 3-4: ShineMonitor answers a runner worse
# when logins arrive together.
WORKERS = 3
SECOND_PASS_BUDGET_S = 360   # time for trying the failed accounts again, one at a time
SECOND_PASS_PAUSE_S = 5      # let the vendor settle before the second pass
NO_RECORD = 12          # ERR_NO_RECORD: no rows for that day or page
DEVICE_PREFIX = "device:"
TS_FMT = "%Y-%m-%d %H:%M:%S"
FIELDS = {"Timestamp": "ts", "Grid Voltage": "grid_v", "Battery Voltage": "batt_v",
          "Inverter Voltage": "out_v", "PLoad": "load_w"}
READINGS = ("grid_v", "batt_v", "out_v", "load_w")


class FetchError(Exception):
    """A call ShineMonitor did not answer. The device keeps the state it had."""


# --------------------------------------------------------------------------- #
# Config and time
# --------------------------------------------------------------------------- #
def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def tz_offset_minutes():
    return int(gda.ALARM_TRIAGE_CONFIG.get("timezone", {})
               .get("plant_tz_offset_minutes_default", 330))


def no_grid_labels(cfg):
    return {exclusions.normalize(e["label"]) for e in cfg.get("no_grid_expected", [])}


def parse_ts(ts):
    return datetime.strptime(ts, TS_FMT)


def minutes_between(earlier, later):
    return (parse_ts(later) - parse_ts(earlier)).total_seconds() / 60


def local_to_utc_iso(ts, tz_offset_min):
    local = parse_ts(ts).replace(tzinfo=timezone(timedelta(minutes=tz_offset_min)))
    return local.astimezone(timezone.utc).isoformat()


def compact(ts):
    """'2026-11-08 06:10:00' -> '2026-11-08T061000': an alarm id with no colons,
    because the state key is split on them."""
    return ts[:10] + "T" + ts[11:].replace(":", "")


# --------------------------------------------------------------------------- #
# Readings
# --------------------------------------------------------------------------- #
def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_page(dat):
    """One queryDeviceDataOneDayPaging page as rows, newest first as sent.

    None when the feed carries no grid or battery voltage: not a MUST PV1800
    style device, so there is nothing here to watch.
    """
    titles = [t.get("title") for t in (dat.get("title") or [])]
    if not {"Timestamp", "Grid Voltage", "Battery Voltage"} <= set(titles):
        return None
    index = {key: titles.index(title) for title, key in FIELDS.items() if title in titles}
    rows = []
    for raw in dat.get("row") or []:
        field = raw.get("field") or []
        row = {key: field[i] if i < len(field) else None for key, i in index.items()}
        if not row.get("ts"):
            continue
        for key in READINGS:
            row[key] = _num(row.get(key))
        if row["grid_v"] is None or row["batt_v"] is None:
            continue
        rows.append(row)
    return rows


def fetch_new_rows(get_page, today, since, look_back_days, present_v):
    """The device's readings newer than `since`, oldest first; None if it is not a MUST feed.

    get_page(date, page) returns (rows newest first, more pages follow), or
    (None, False) for a feed with no grid or battery voltage. It raises
    FetchError for a call that got no answer.

    With `since` (the newest reading already processed) it reads back to that
    reading and never before its day. On a first look it reads today, then
    earlier days while no reading has yet shown grid, so an outage that began
    before today is timed from its real start - at most `look_back_days` back.
    """
    rows = []
    for back in range(look_back_days + 1):
        day = (today - timedelta(days=back)).isoformat()
        if since and day < since[:10]:
            break
        reached = False
        for page in range(MAX_PAGES_PER_DAY):
            try:
                items, more = get_page(day, page)
            except FetchError:
                # A first look that walks back several days is many calls, and one
                # unanswered call must not cost all of them: keep what was read.
                # Those rows are the newest, so an outage still open in them is
                # timed from the oldest one, marked "or earlier".
                if since is None and rows:
                    return sorted({r["ts"]: r for r in rows}.values(), key=lambda r: r["ts"])
                raise
            if items is None:
                return None
            for row in items:
                if since and row["ts"] <= since:
                    reached = True
                    break
                rows.append(row)
            if reached or not more:
                break
        if reached:
            break
        if since is None and any(r["grid_v"] >= present_v for r in rows):
            break
    unique = {r["ts"]: r for r in rows}
    return [unique[ts] for ts in sorted(unique)]


# --------------------------------------------------------------------------- #
# Outage and backup state, one device
# --------------------------------------------------------------------------- #
def new_device_state():
    return {"last_ts": None, "nominal_v": None, "pending": None,
            "outage": None, "backup": None, "recent": []}


def nominal_voltage(battery_volts):
    """24 or 48 from the battery's readings; None for anything else (no flat mark)."""
    values = [v for v in battery_volts if v is not None and v > 0]
    if not values:
        return None
    median = statistics.median(values)
    if median > 36:
        return 48
    if median > 18:
        return 24
    return None


def flat_mark(dev, cfg):
    nominal = dev.get("nominal_v")
    return cfg["flat_battery_v"].get(str(nominal)) if nominal else None


def recharged_mark(dev, cfg):
    flat = flat_mark(dev, cfg)
    if flat is None:
        return None
    return flat + cfg["recharged_above_flat_v"][str(dev["nominal_v"])]


def _summary(row):
    return {key: row.get(key) for key in ("ts",) + READINGS}


def advance(dev, rows, cfg):
    """Feed new readings, oldest first, through the device's outage and backup state.

    Outage: two readings in a row below grid_absent_below_v open it, timed from
    the first; one at grid_present_from_v or more closes it. A reading between
    the two marks (a brownout) neither opens nor closes one.

    Backup episode: opens on the first reading with no grid and a flat battery
    or a dead output; closes when the grid is back, or when the output is alive
    and the battery has been recharged well clear of flat. One episode keeps
    one alarm key, so a battery
    that flickers either side of flat is not mailed afresh each time.
    """
    absent_v = cfg["grid_absent_below_v"]
    present_v = cfg["grid_present_from_v"]
    dead_v = cfg["output_dead_below_v"]
    if dev.get("nominal_v") is None and rows:
        dev["nominal_v"] = nominal_voltage([r["batt_v"] for r in rows])
    flat = flat_mark(dev, cfg)
    recharged = recharged_mark(dev, cfg)

    for row in rows:
        if dev["last_ts"] and row["ts"] <= dev["last_ts"]:
            continue
        grid = row["grid_v"]
        outage = dev["outage"]
        if outage is None:
            if grid < absent_v:
                if dev["pending"] is None:
                    # The start is known only if a reading came just before
                    # this one; after a first look or a long silence, the grid
                    # may have gone some time earlier.
                    known = bool(dev["last_ts"]) and \
                        minutes_between(dev["last_ts"], row["ts"]) <= cfg["fresh_minutes"]
                    dev["pending"] = {"ts": row["ts"], "batt_v": row["batt_v"], "known": known}
                else:
                    pending = dev["pending"]
                    dev["outage"] = {"start": pending["ts"], "start_known": pending["known"],
                                     "last_absent": row["ts"],
                                     "min_batt_v": min(pending["batt_v"], row["batt_v"])}
                    dev["pending"] = None
            else:
                dev["pending"] = None
        elif grid >= present_v:
            dev["outage"] = None
        elif grid < absent_v:
            outage["last_absent"] = row["ts"]
            outage["min_batt_v"] = min(outage["min_batt_v"], row["batt_v"])

        flat_now = flat is not None and row["batt_v"] <= flat
        dead_now = row.get("out_v") is not None and row["out_v"] < dead_v
        if dev["backup"] is None:
            if grid < absent_v and (flat_now or dead_now):
                dev["backup"] = {"start": row["ts"]}
        elif grid >= present_v or (recharged is not None and row["batt_v"] >= recharged
                                   and not dead_now):
            # A healthy battery behind a dead output is still a failure.
            dev["backup"] = None

        dev["recent"] = (dev["recent"] + [_summary(row)])[-2:]
        dev["last_ts"] = row["ts"]
    return dev


def open_alarms(dev, now_local, cfg, grid_expected):
    """The device's alarms open right now, as dicts: kind, id, start, message, detail."""
    recent = dev.get("recent") or []
    if not recent:
        return []
    absent_v = cfg["grid_absent_below_v"]
    last = recent[-1]
    prev = recent[-2] if len(recent) == 2 and \
        minutes_between(recent[-2]["ts"], last["ts"]) <= cfg["silent_minutes"] else None
    quiet_min = minutes_between(last["ts"], now_local)
    fresh = quiet_min <= cfg["fresh_minutes"]
    silent = quiet_min >= cfg["silent_minutes"]
    if last["grid_v"] >= absent_v:
        return []

    outage = dev.get("outage")
    detail = _detail(last, outage)
    alarms = []
    if grid_expected and outage and fresh:
        since = outage["start"][:16] + ("" if outage.get("start_known", True) else " or earlier")
        alarms.append({
            "kind": "grid-loss", "id": f"grid-loss@{compact(outage['start'])}",
            "start": outage["start"],
            "message": f"Grid loss: no AC input on the inverter since {since}",
            "detail": detail,
        })

    backup = dev.get("backup")
    if not backup:
        return alarms
    flat = flat_mark(dev, cfg)
    pair = [prev, last] if prev else []
    message = None
    if flat is not None and last["batt_v"] <= flat and silent:
        message = (f"Backup failed: battery flat at {last['batt_v']:.1f} V with no grid, and no "
                   f"reading since {last['ts'][11:16]}; the inverter has most likely shut down")
    elif fresh and pair and flat is not None and all(r["batt_v"] <= flat for r in pair):
        message = (f"Battery flat at {last['batt_v']:.1f} V with no grid: "
                   f"the inverter will cut off soon")
    elif fresh and pair and all(r.get("out_v") is not None and
                                r["out_v"] < cfg["output_dead_below_v"] for r in pair):
        message = (f"Backup failed: AC output at {last['out_v']:.0f} V with no grid; "
                   f"the house has no power")
    if message:
        # The outage's clock, not the episode's: six hours into an outage, a
        # battery that gives out is news at once.
        on_outage = bool(grid_expected and outage)
        alarms.append({"kind": "backup", "id": f"backup@{compact(backup['start'])}",
                       "start": outage["start"] if on_outage else backup["start"],
                       "clock": "outage" if on_outage else "battery",
                       "message": message, "detail": detail})
    return alarms


def _detail(last, outage):
    parts = [f"grid {last['grid_v']:.0f} V", f"battery {last['batt_v']:.1f} V"]
    if outage:
        parts[-1] += f" (lowest {outage['min_batt_v']:.1f} V)"
    if last.get("load_w") is not None:
        parts.append(f"load {last['load_w']:.0f} W")
    if last.get("out_v") is not None:
        parts.append(f"output {last['out_v']:.0f} V")
    return f"Latest reading {last['ts'][:16]}: " + ", ".join(parts)


def live_keys(device, dev, grid_expected):
    """Alarm keys still in play for a device that was read this run.

    A key lives as long as its outage or backup episode, even while the alarm
    reads closed for a moment, so its send count is not lost to a flicker.
    """
    prefix = f"{device['pid']}:{device['pn']}"
    keys = set()
    if grid_expected and dev.get("outage"):
        keys.add(f"{prefix}:grid-loss@{compact(dev['outage']['start'])}")
    if dev.get("backup"):
        keys.add(f"{prefix}:backup@{compact(dev['backup']['start'])}")
    return keys


def prune_alarm_state(state, read_devices, keep):
    """Forget the alarms of devices read this run whose outage or episode has ended.
    A device that could not be read keeps every key: a gap in the feed must not
    start its sent alarms again at Send 1/3."""
    for key in list(state):
        if key.startswith(DEVICE_PREFIX):
            continue
        if ":".join(key.split(":")[:2]) in read_devices and key not in keep:
            del state[key]
    return state


def to_gate_alarm(spec, device, label, tz_offset_min):
    """The fields the gate, UC3's state and the email read."""
    name = device.get("alias") or device["pn"]
    return {
        "pid": device["pid"],
        "pn": device["pn"],
        "id": spec["id"],
        "customer_label": label,
        "plant": f"{label} - {name}",
        "desc": spec["message"],
        "message": spec["message"],
        "detail": spec["detail"],
        "kind": spec["kind"],
        "clock": spec.get("clock", "outage"),
        "open": True,
        "started_at_utc": local_to_utc_iso(spec["start"], tz_offset_min),
    }


# --------------------------------------------------------------------------- #
# ShineMonitor client: login, device list, one day's rows. Read-only.
# --------------------------------------------------------------------------- #
def _sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _salt():
    return str(int(time.time() * 1000))


def _request(build):
    """One request with one retry. `build()` gives (url, body) afresh each try,
    so the retry carries a new salt. None when both tries go unanswered."""
    for attempt, timeout in enumerate(TIMEOUTS_S):
        if attempt:
            time.sleep(RETRY_PAUSE_S)
        url, body = build()
        try:
            req = urllib.request.Request(url, data=body)
            if body is not None:
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue
    return None


class ShineMonitor:
    def __init__(self, token, secret):
        self.token, self.secret = token, secret

    @classmethod
    def login(cls, username, password, company_key):
        """authEmail (GET), then authSource (POST), as shinemonitor_common.sh does."""
        pw = _sha1(password)
        tail = f"&action=authEmail&usr={urllib.parse.quote(username, safe='')}&company-key={company_key}"

        def by_email():
            salt = _salt()
            return f"{API_URL}?sign={_sha1(salt + pw + tail)}&salt={salt}{tail}", None

        def by_source():
            salt = _salt()
            body = urllib.parse.urlencode({"usr": username, "company-key": company_key, "pwd": pw,
                                           "sign": _sha1(username + pw + salt), "salt": salt})
            return f"{API_URL}?action=authSource", body.encode()

        for build in (by_email, by_source):
            resp = _request(build) or {}
            dat = resp.get("dat") or {}
            if resp.get("err") == 0 and isinstance(dat, dict) and dat.get("token") and dat.get("secret"):
                return cls(dat["token"], dat["secret"])
        raise FetchError("login failed")

    def call(self, action, params=""):
        tail = f"&action={action}" + (f"&{params}" if params else "")

        def build():
            salt = _salt()
            sign = _sha1(salt + self.secret + self.token + tail)
            return f"{API_URL}?sign={sign}&salt={salt}&token={self.token}{tail}", None

        resp = _request(build)
        if resp is None:
            raise FetchError(f"{action}: no answer")
        return resp

    def devices(self):
        resp = self.call("queryDevices")
        if resp.get("err") == NO_RECORD:
            return []
        if resp.get("err") != 0:
            raise FetchError(f"queryDevices: err {resp.get('err')}")
        dat = resp.get("dat") or {}
        return (dat.get("device") or []) if isinstance(dat, dict) else []

    def day_page(self, device, date, page):
        params = (f"pn={device['pn']}&devcode={device['devcode']}&devaddr={device['devaddr']}"
                  f"&sn={device['sn']}&date={date}&page={page}&pagesize={PAGE_SIZE}&i18n=en_US")
        resp = self.call("queryDeviceDataOneDayPaging", params)
        if resp.get("err") == NO_RECORD:
            return [], False
        if resp.get("err") != 0:
            raise FetchError(f"queryDeviceDataOneDayPaging: err {resp.get('err')}")
        dat = resp.get("dat") or {}
        rows = parse_page(dat)
        try:
            more = (page + 1) * PAGE_SIZE < int(dat.get("total") or 0)
        except (TypeError, ValueError):
            more = False
        return rows, more and rows is not None


def read_account(account, company_key, device_states, cfg, now_local, client_factory=None):
    """Read one account's devices and advance each device's state.

    Returns {"ok", "devices": [(device_key, device, label, state)], "unsupported", "failed"}.
    A device whose read fails is left out, so its old state and keys stand.
    """
    login = client_factory or ShineMonitor.login
    out = {"ok": False, "devices": [], "unsupported": 0, "failed": 0}
    try:
        client = login(account["username"], account["password"], company_key)
        devices = client.devices()
    except FetchError:
        return out
    out["ok"] = True
    today = parse_ts(now_local).date()
    for device in devices:
        key = f"{device['pid']}:{device['pn']}"
        dev = json.loads(json.dumps(device_states.get(key) or new_device_state()))
        since = max(filter(None, (dev.get("last_ts"), dev.get("cursor"))), default=None)
        try:
            rows = fetch_new_rows(lambda day, page: client.day_page(device, day, page), today,
                                  since, cfg["first_look_back_days"], cfg["grid_present_from_v"])
        except FetchError:
            out["failed"] += 1
            continue
        if rows is None:
            out["unsupported"] += 1
            continue
        if not rows:
            # A dark logger: read only from today on next time, not the whole
            # look-back again. Midnight, not now: a logger's own clock can lag
            # the runner's, and its first rows back must not fall behind the cursor.
            dev["cursor"] = f"{today.isoformat()} 00:00:00"
        out["devices"].append((key, device, account["label"], advance(dev, rows, cfg)))
    return out


def _better(first, second):
    """The read to keep for an account tried twice: the second only if it did better."""
    if second["ok"] and (not first["ok"] or second["failed"] < first["failed"]):
        return second
    return first


def read_fleet(accounts, company_key, device_states, cfg, now_local, client_factory=None,
               budget_s=None, pause_s=None, clock=time.monotonic):
    """Read every account, then try once more, one at a time, each that failed.

    From a GitHub runner ShineMonitor leaves a share of logins and calls
    unanswered, and the share is not the same twice: an account that hung on
    the first pass usually answers on the second. The second pass stops when
    its time budget is spent, so a vendor that answers nobody cannot run the
    job into its timeout. Returns (results, accounts recovered on the second try).
    """
    budget_s = SECOND_PASS_BUDGET_S if budget_s is None else budget_s
    pause_s = SECOND_PASS_PAUSE_S if pause_s is None else pause_s

    def read(account):
        return read_account(account, company_key, device_states, cfg, now_local, client_factory)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(read, accounts))
    again = [i for i, r in enumerate(results) if not r["ok"] or r["failed"]]
    recovered = 0
    deadline = clock() + budget_s
    if again and budget_s > 0 and pause_s:
        time.sleep(pause_s)
    for i in again:
        if clock() >= deadline:
            break
        second = read(accounts[i])
        best = _better(results[i], second)
        recovered += best is second
        results[i] = best
    return results, recovered


# --------------------------------------------------------------------------- #
# State, email, main
# --------------------------------------------------------------------------- #
def load_state(path):
    """One flat map, as every state file here is, so push_data_commit.sh's merge
    (merge_alarm_state.py) can union two runs key by key: the gate's alarm
    records under `pid:pn:id`, each device's readings state under `device:pid:pn`."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path, state):
    os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def device_states(state):
    return {k[len(DEVICE_PREFIX):]: v for k, v in state.items() if k.startswith(DEVICE_PREFIX)}


def collect_alarms(results, state, cfg, now_local, tz_offset_min):
    """Merge the fleet read into the state; return (gate alarms, devices read, live keys)."""
    no_grid = no_grid_labels(cfg)
    alarms, read, keep = [], set(), set()
    for result in results:
        for key, device, label, dev in result["devices"]:
            state[DEVICE_PREFIX + key] = dev
            read.add(key)
            grid_expected = exclusions.normalize(label) not in no_grid
            keep |= live_keys(device, dev, grid_expected)
            for spec in open_alarms(dev, now_local, cfg, grid_expected):
                alarms.append(to_gate_alarm(spec, device, label, tz_offset_min))
    return alarms, read, keep


def format_email(items, utc_now, tz_offset_min):
    local = timezone(timedelta(minutes=tz_offset_min))
    lines = [
        f"MUST inverters - {len(items)} alarm(s) from their own readings, 6 hours or more into an outage",
        f"Report time: {utc_now.astimezone(local):%Y-%m-%d %H:%M} (plant local time)",
        "",
    ]
    for item in sorted(items, key=lambda i: i["alarm"]["started_at_utc"]):
        alarm = item["alarm"]
        alarm_class = gda.alarm_triage.classify(alarm["message"], gda.ALARM_TRIAGE_CONFIG)
        hint = gda.ALARM_TRIAGE_CONFIG.get("escalation_hint", {}).get(alarm_class)
        started = datetime.fromisoformat(alarm["started_at_utc"])
        hours = (utc_now - started).total_seconds() / 3600
        clock = alarm.get("clock", "outage")
        lines += [
            f"{alarm['plant']} ({alarm['pn']})",
            f"  {alarm['message']}",
            f"  {alarm['detail']}",
            f"  {hours:.1f} h on the {clock} clock, class {alarm_class}, send {item['send_count'] + 1}/3",
        ]
        if hint:
            lines.append(f"  First check: {hint}")
        lines.append("")
    lines += [
        "Made from the inverters' 5-minute readings: ShineMonitor's warning list does not",
        "report a MUST inverter losing the grid. Grid loss goes out when a fresh reading",
        "still shows no AC input 6 hours after it went; a flat battery or a dead output",
        "goes out on the same outage clock. Each at most 3 times, 4 hours apart.",
    ]
    return "\n".join(lines)


def main(argv=None, client_factory=None, utc_now=None):
    ap = argparse.ArgumentParser(description="MUST telemetry alarms through the six-hour gate")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be mailed; send nothing, save no state")
    ap.add_argument("--state-file", default=str(STATE_PATH))
    args = ap.parse_args(argv)

    cfg = load_config()
    with open(CREDENTIALS_PATH, encoding="utf-8") as f:
        creds = json.load(f)
    tz_offset = tz_offset_minutes()
    utc_now = utc_now or datetime.now(timezone.utc)
    now_local = (utc_now + timedelta(minutes=tz_offset)).strftime(TS_FMT)

    accounts = [a for a in creds.get("accounts", [])
                if not exclusions.is_customer_excluded(a.get("label", ""))]
    excluded = len(creds.get("accounts", [])) - len(accounts)
    state = load_state(args.state_file)
    results, recovered = read_fleet(accounts, creds["company_key"], device_states(state), cfg,
                                    now_local, client_factory)
    read_ok = sum(r["ok"] for r in results)
    if accounts and not read_ok:
        print("✗ ShineMonitor answered for no account - state left as it was", file=sys.stderr)
        return 1

    alarms, read, keep = collect_alarms(results, state, cfg, now_local, tz_offset)
    outages = sum(1 for k in read if state[DEVICE_PREFIX + k].get("outage"))
    episodes = sum(1 for k in read if state[DEVICE_PREFIX + k].get("backup"))
    # Counts and keys only: Actions logs of this repo are public, names are not (#4).
    print(f"✓ {read_ok}/{len(accounts)} accounts read ({recovered} on a second try, {excluded} "
          f"excluded); {len(read)} devices "
          f"read, {sum(r['failed'] for r in results)} failed, "
          f"{sum(r['unsupported'] for r in results)} without grid/battery readings")
    print(f"✓ {outages} open grid outage(s), {episodes} backup episode(s); {len(alarms)} alarm(s) open")

    prune_alarm_state(state, read, keep)
    passed = gda.triage_alarms(alarms, state, PLATFORM, utc_now)
    to_send = gda.filter_alarms_to_send(passed, state)
    print(f"✓ {len(passed)} passed the six-hour gate; {len(to_send)} to send")
    for item in to_send:
        print(f"  - {item['alarm_key']}")

    if not to_send:
        if not args.dry_run:
            save_state(args.state_file, state)
        return 0

    body = format_email(to_send, utc_now, tz_offset)
    if args.dry_run:
        print("\n" + body)
        return 0

    from email_utils import send_email_smtp
    from features import is_enabled
    if not is_enabled(EMAIL_CHANNEL):
        print(f"Feature flag '{EMAIL_CHANNEL}' is off - nothing mailed")
        save_state(args.state_file, state)
        return 0
    admin = creds.get("admin_email", DEFAULT_ADMIN_EMAIL)
    subject = f"🔔 MUST: {len(to_send)} grid/battery alarm(s), 6h+ into an outage"
    sent = send_email_smtp(admin, subject, body, channel=EMAIL_CHANNEL)
    if sent:
        gda.update_alarm_state(state, to_send)
    save_state(args.state_file, state)
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
