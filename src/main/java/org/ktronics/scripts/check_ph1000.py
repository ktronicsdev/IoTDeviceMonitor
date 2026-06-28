#!/usr/bin/env python3
"""
PH1000 inverter telemetry module (ShineMonitor).

Reads the RELIABLE cloud fields for the MUST PV18/PH18-family PH1000 hybrid
inverter (Mifanza account) and writes a dashboard JSON plus a per-device monthly
CSV history.

IMPORTANT — data reliability (established by live discovery, see README_PH1000.md):
  ShineMonitor's cloud telemetry for this device (devcode 697) is mostly broken:
  "Batt Current" is stuck at 100, PV/Inverter/Grid Voltage and PLoad are nonsense,
  and every Accumulated energy counter is frozen. The cloud exposes NO correct
  PV power, load power, or SOC (those exist only over local RS485 Modbus). The
  raw Modbus registers are not readable through the public cloud API either
  (queryDeviceCtrlField is settings-only).

  So this module only surfaces the fields that ARE trustworthy:
    * Battery Voltage
    * Battery Current  (= the "Charger Current" column; signed +charge/-discharge)
    * Battery Power    (= the "Charger Power" column; verified V x A)
    * PInverter
    * work state, connection status, last-update time

Endpoints:
  * queryDeviceLastData          -> latest snapshot (the SP variant is empty)
  * queryDeviceDataOneDayPaging  -> per-day history (the "Data Details" table)

Usage:
    python check_ph1000.py --customer Mifanza --discover
    python check_ph1000.py --customer Mifanza --json-out data/ph1000_live.json --history-days 7
"""

import argparse
import csv
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Safe UTF-8 on Windows: reconfigure() mutates the stream in place (no buffer
# re-wrapping), so it won't crash pytest's output capture.
try:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
from config import CREDENTIALS_PATH  # noqa: E402

API_URL = "https://web.shinemonitor.com/public/"


# --------------------------------------------------------------------------- #
# ShineMonitor API helpers (self-contained; same scheme as check_plant_roi.py)
# --------------------------------------------------------------------------- #
def sha1hex(text):
    return hashlib.sha1(text.encode()).hexdigest()


def salt_ms():
    return str(int(time.time() * 1000))


def api_request(url, method="GET", data=None):
    try:
        if method == "POST" and data:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        else:
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"err": -1, "msg": str(e)}


def authenticate(username, password, company_key):
    """Auth with authEmail (GET) then fall back to authSource (POST)."""
    salt = salt_ms()
    pw = sha1hex(password)
    usr = urllib.parse.quote(username)

    tail = f"&action=authEmail&usr={usr}&company-key={company_key}"
    sign = sha1hex(f"{salt}{pw}{tail}")
    resp = api_request(f"{API_URL}?sign={sign}&salt={salt}{tail}")
    if resp.get("err") == 0:
        dat = resp.get("dat", {})
        if dat.get("token") and dat.get("secret"):
            print(f"[OK] Authenticated for {username}")
            return dat["token"], dat["secret"]

    salt = salt_ms()
    sign = sha1hex(f"{username}{pw}{salt}")
    resp = api_request(f"{API_URL}?action=authSource", method="POST", data={
        "usr": username, "company-key": company_key, "pwd": pw, "sign": sign, "salt": salt})
    if resp.get("err") == 0:
        dat = resp.get("dat", {})
        if dat.get("token") and dat.get("secret"):
            print(f"[OK] Authenticated for {username}")
            return dat["token"], dat["secret"]

    print(f"[ERROR] Authentication failed for {username}")
    return None, None


def api_call(token, secret, action, extra_params=""):
    salt = salt_ms()
    action_string = f"&action={action}&{extra_params}" if extra_params else f"&action={action}"
    sign = sha1hex(f"{salt}{secret}{token}{action_string}")
    return api_request(f"{API_URL}?sign={sign}&salt={salt}&token={token}{action_string}")


def get_plants(token, secret):
    resp = api_call(token, secret, "queryPlants")
    if resp.get("err") == 0:
        dat = resp.get("dat", {})
        return dat.get("plant", []) if isinstance(dat, dict) else dat
    return []


def get_collectors(token, secret, plant_id):
    resp = api_call(token, secret, "webQueryCollectorsEs", f"plantid={plant_id}")
    if resp.get("err") == 0:
        dat = resp.get("dat", {})
        return dat.get("collector", []) if isinstance(dat, dict) else dat
    return []

# Known device identity for the Mifanza PH1000 (from the Plant Analysis table).
# Discovery still runs and overrides these; they are sensible fallbacks/defaults.
DEFAULT_DEVCODE = "697"
DEFAULT_DEVADDR = "4"

LIVE_ACTION = "queryDeviceLastData"  # the SP variant returns almost nothing here

# Reliable canonical field -> candidate parameter/column title substrings (lowercase).
# The real battery current/power are the "Charger Current"/"Charger Power" columns.
RELIABLE_FIELDS = {
    "battery_v":   (["battery voltage"], "V"),
    "battery_a":   (["charger current"], "A"),   # signed: + charging / - discharging
    "battery_w":   (["charger power"], "W"),      # signed battery power
    "pinverter_w": (["pinverter", "inverter power"], "W"),
    "work_state":  (["work state", "working state"], ""),
}

# CSV history columns (history has no "work state" column, so it stays blank there).
# pv_power_w_est / load_power_w_est are ENERGY-BALANCE ESTIMATES (see derive_energy_balance).
CSV_COLUMNS = ["timestamp", "battery_v", "battery_a", "battery_w", "pinverter_w",
               "pv_power_w_est", "load_power_w_est", "work_state", "charge_state"]


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
def load_ph1000_accounts(creds_path, customer=None):
    """Return (company_key, [(label, username, password)]) for PH1000 accounts.

    Included if flagged "ph1000": true, OR if --customer matches the label.
    """
    path = Path(creds_path)
    if not path.exists():
        print(f"[ERROR] Credentials file not found: {path}")
        return None, []

    with open(path, "r", encoding="utf-8") as f:
        creds = json.load(f)

    company_key = creds.get("company_key")
    out = []
    for acc in creds.get("accounts", []):
        label = acc.get("label", "")
        if customer:
            if label.lower() != customer.lower():
                continue
        elif not acc.get("ph1000"):
            continue
        out.append((label, acc.get("username"), acc.get("password")))
    return company_key, out


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _first(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d.get(k)
    return default


def _to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _match_field(title):
    """Return the canonical field key for a column/par title, or None."""
    low = str(title).lower()
    for key, (candidates, _unit) in RELIABLE_FIELDS.items():
        if any(c in low for c in candidates):
            return key
    return None


def charge_state(battery_w):
    """Label battery flow from signed battery power."""
    w = _to_float(battery_w)
    if w is None:
        return "unknown"
    if w > 0:
        return "charging"
    if w < 0:
        return "discharging"
    return "idle"


INVERTER_SELF_USE_W = 10  # the PH1000's own self-power draw (~10 W, per field obs.)


def derive_energy_balance(battery_w, pinverter_w):
    """Estimate PV power and load power from the two reliable fields.

    Energy balance (signed Charger/Battery Power: + charging / - discharging):
      * PInverter == 0 -> battery discharges to the load -> load = -ChargerPower.
      * PInverter  > 0 -> PV powers the house, split between battery + load
                          -> load = PInverter - ChargerPower, PV ~= PInverter.
    Both cases unify to load = PInverter - ChargerPower, minus the inverter's own
    ~10 W self-use, clamped >= 0. PV is taken as PInverter (confirmed = PV power).
    These are ESTIMATES (the cloud's own PV/PLoad columns are broken); flag as such.
    """
    cp = _to_float(battery_w)
    pinv = _to_float(pinverter_w)
    if cp is None or pinv is None:
        return None, None
    pv = pinv if pinv > 0 else 0.0
    load = pinv - cp - INVERTER_SELF_USE_W
    if load < 0:
        load = 0.0
    return pv, load


def status_label(status):
    """ShineMonitor device status: 0 == Online (matches the Plant Analysis page)."""
    return "Online" if str(status) == "0" else f"Offline ({status})"


def safe_slug(text):
    s = "".join(ch if ch.isalnum() else "-" for ch in str(text).lower())
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


# --------------------------------------------------------------------------- #
# Device discovery
# --------------------------------------------------------------------------- #
def discover_device(token, secret, plant_id, devcode_override=None):
    """Discover the PH1000 device for a plant. Returns dict with pn/devcode/
    devaddr/sn/alias/status (+ _raw)."""
    collectors = get_collectors(token, secret, plant_id)
    if not collectors:
        return None

    collector = collectors[0] if isinstance(collectors[0], dict) else {}
    pn = _first(collector, "pn", "sn", default="")

    resp = api_call(token, secret, "webQueryDeviceEs",
                    f"pn={pn}&devcode={devcode_override or DEFAULT_DEVCODE}"
                    f"&devaddr={DEFAULT_DEVADDR}&sn={pn}")
    devices = []
    if resp.get("err") == 0:
        dat = resp.get("dat", {})
        devices = dat.get("device", []) if isinstance(dat, dict) else dat

    dev = devices[0] if devices else {}
    return {
        "pn": _first(dev, "pn", default=pn),
        "devcode": str(_first(dev, "devcode", default=devcode_override or DEFAULT_DEVCODE)),
        "devaddr": str(_first(dev, "devaddr", default=DEFAULT_DEVADDR)),
        "sn": _first(dev, "sn", default=pn),
        "alias": _first(dev, "devalias", "alias", default="PH1000"),
        "status": _first(dev, "status", "connection", "online", default="unknown"),
        "_raw": resp,
    }


def _device_params(device):
    return (f"pn={device['pn']}&devcode={device['devcode']}"
            f"&devaddr={device['devaddr']}&sn={device['sn']}&i18n=en_US")


# --------------------------------------------------------------------------- #
# LIVE snapshot (queryDeviceLastData)
# --------------------------------------------------------------------------- #
def _iter_par_entries(obj):
    """Yield (title, value, unit) from an arbitrary queryDeviceLastData payload."""
    if isinstance(obj, dict):
        name = _first(obj, "par", "name", "title", "id")
        val = obj.get("val") if "val" in obj else obj.get("value")
        if name is not None and val is not None:
            yield (str(name), val, obj.get("unit", ""))
        for v in obj.values():
            yield from _iter_par_entries(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_par_entries(v)


def fetch_live(token, secret, device):
    """Return ({canonical: {value, unit}}, last_update, raw). Reliable fields only."""
    resp = api_call(token, secret, LIVE_ACTION, _device_params(device))
    if resp.get("err") != 0:
        return {}, None, resp

    fields, last_update = {}, None
    for title, val, unit in _iter_par_entries(resp.get("dat", {})):
        low = str(title).lower()
        if low == "timestamp":
            last_update = val
            continue
        key = _match_field(title)
        if key and key not in fields:
            unit = unit or RELIABLE_FIELDS[key][1]
            fields[key] = {"value": val, "unit": unit}
    return fields, last_update, resp


# --------------------------------------------------------------------------- #
# 7-day history (queryDeviceDataOneDayPaging)
# --------------------------------------------------------------------------- #
def map_history_columns(titles):
    """Map canonical keys to column indices from the dat.title metadata.
    Raises ValueError if the title metadata is missing."""
    if not titles:
        raise ValueError("history response has no 'title' metadata to map columns by name")

    idx = {}
    for i, t in enumerate(titles):
        label = str(_first(t, "title", "name", default=t) if isinstance(t, dict) else t).lower()
        if "timestamp" in label and "timestamp" not in idx:
            idx["timestamp"] = i
            continue
        key = _match_field(label)
        if key and key not in idx:
            idx[key] = i
    return idx


def fetch_history(token, secret, device, days):
    """Fetch the last `days` days of logged reliable fields. Returns (rows, titles)."""
    rows = []
    sample_titles = None
    today = datetime.now(timezone.utc).date()

    for offset in range(days - 1, -1, -1):
        d = today - timedelta(days=offset)
        date_str = d.strftime("%Y-%m-%d")
        page = 0
        idx = None
        while True:
            resp = api_call(token, secret, "queryDeviceDataOneDayPaging",
                            f"{_device_params(device)}&date={date_str}"
                            f"&page={page}&pagesize=150")
            if resp.get("err") != 0:
                break
            dat = resp.get("dat", {})
            titles = dat.get("title", [])
            if sample_titles is None and titles:
                sample_titles = titles
            try:
                idx = idx or map_history_columns(titles)
            except ValueError:
                break

            page_rows = dat.get("row", [])
            for r in page_rows:
                fields = r.get("field", [])
                ts_i = idx.get("timestamp")
                if ts_i is None or ts_i >= len(fields):
                    continue
                row = {"timestamp": fields[ts_i], "work_state": "", "source": "measured"}
                for key in ("battery_v", "battery_a", "battery_w", "pinverter_w"):
                    i = idx.get(key)
                    row[key] = _to_float(fields[i]) if (i is not None and i < len(fields)) else None
                row["charge_state"] = charge_state(row.get("battery_w"))
                pv, load = derive_energy_balance(row.get("battery_w"), row.get("pinverter_w"))
                row["pv_power_w_est"] = pv
                row["load_power_w_est"] = load
                rows.append(row)

            if len(page_rows) < 150:
                break
            page += 1
            time.sleep(0.1)
        time.sleep(0.1)

    return rows, sample_titles


# --------------------------------------------------------------------------- #
# Merge + output
# --------------------------------------------------------------------------- #
def measured_row_from_live(fields, ts):
    """Flatten a fetch_live() result into a flat CSV/series row."""
    row = {"timestamp": ts, "source": "measured"}
    for col in CSV_COLUMNS:
        if col in ("timestamp", "charge_state", "pv_power_w_est", "load_power_w_est"):
            continue
        row[col] = fields.get(col, {}).get("value") if col in fields else ""
    row["charge_state"] = charge_state(row.get("battery_w"))
    pv, load = derive_energy_balance(row.get("battery_w"), row.get("pinverter_w"))
    row["pv_power_w_est"] = pv
    row["load_power_w_est"] = load
    return row


def merge_series(history_rows, measured_rows):
    """Combine history with measured polls; later/measured wins per timestamp."""
    by_ts = {}
    for r in history_rows:
        by_ts[str(r["timestamp"])] = r
    for r in measured_rows:
        by_ts[str(r["timestamp"])] = r
    return [by_ts[k] for k in sorted(by_ts)]


def csv_path(data_dir, label, device, month):
    fn = f"ph1000-{safe_slug(label)}-{safe_slug(device['alias'] or device['sn'])}-{month}.csv"
    return Path(data_dir) / fn


def append_measured_csv(data_dir, label, device, measured_row):
    """Append one measured poll into the per-device monthly CSV (dedup by timestamp)."""
    ts = str(measured_row["timestamp"])
    month = ts[:7] if len(ts) >= 7 and ts[4] == "-" else datetime.now(timezone.utc).strftime("%Y-%m")
    path = csv_path(data_dir, label, device, month)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if path.exists():
        with open(path, "r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                existing[r["timestamp"]] = r
    existing[ts] = {c: measured_row.get(c, "") for c in CSV_COLUMNS}

    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for k in sorted(existing):
            w.writerow(existing[k])
    return path


def load_measured_history(data_dir, label, device):
    """Read recent measured polls back from the monthly CSV(s) for the merge."""
    rows = []
    today = datetime.now(timezone.utc)
    months = {(today - timedelta(days=o)).strftime("%Y-%m") for o in (0, 31)}
    for month in months:
        path = csv_path(data_dir, label, device, month)
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                r["source"] = "measured"
                rows.append(r)
    return rows


def write_json(json_out, device, last_update, latest_fields, series):
    # Add energy-balance estimates to the live snapshot (clearly flagged estimated).
    latest_fields = dict(latest_fields)
    pv, load = derive_energy_balance(
        latest_fields.get("battery_w", {}).get("value"),
        latest_fields.get("pinverter_w", {}).get("value"),
    )
    if pv is not None:
        latest_fields["pv_power_w_est"] = {"value": round(pv), "unit": "W", "estimated": True}
    if load is not None:
        latest_fields["load_power_w_est"] = {"value": round(load), "unit": "W", "estimated": True}

    payload = {
        "device": {
            "pn": device["pn"], "sn": device["sn"], "alias": device["alias"],
            "devcode": device["devcode"],
            "status": status_label(device["status"]),
            "online": str(device["status"]) == "0",
            "last_update": last_update,
        },
        "latest": latest_fields,
        "series7d": series,
        "fields_note": "Only Battery V/A/W and PInverter are reliable from ShineMonitor "
                       "for this PH1000; PV power, load power and SOC are not exposed.",
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    Path(json_out).parent.mkdir(parents=True, exist_ok=True)
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return payload


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def process_account(token, secret, label, args):
    plants = get_plants(token, secret)
    if not plants:
        print(f"  [WARN] No plants for {label}")
        return 1

    plant = plants[0]
    device = discover_device(token, secret, plant.get("pid"), args.devcode)
    if not device:
        print(f"  [WARN] No device discovered for {label}")
        return 1

    print(f"  Device: alias={device['alias']} pn={device['pn']} "
          f"devcode={device['devcode']} devaddr={device['devaddr']} "
          f"status={status_label(device['status'])}")

    if args.discover:
        print(f"\n===== LIVE {LIVE_ACTION} (raw) =====")
        _, _, raw = fetch_live(token, secret, device)
        print(json.dumps(raw, indent=2, default=str)[:6000])
        print("\n===== HISTORY dat.title sample =====")
        _, titles = fetch_history(token, secret, device, days=1)
        print(json.dumps(titles, indent=2, default=str))
        return 0

    latest, last_update, _ = fetch_live(token, secret, device)
    now_ts = last_update or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    measured_now = measured_row_from_live(latest, now_ts)

    path = append_measured_csv(args.data_dir, label, device, measured_now)
    print(f"  Appended measured poll ({now_ts}) -> {path}")

    history_rows, _ = fetch_history(token, secret, device, args.history_days)
    measured_rows = load_measured_history(args.data_dir, label, device)
    series = merge_series(history_rows, measured_rows)

    if args.json_out:
        write_json(args.json_out, device, last_update, latest, series)
        print(f"  Wrote dashboard JSON -> {args.json_out} "
              f"(latest={len(latest)} fields, series={len(series)} points)")
    return 0


def main():
    p = argparse.ArgumentParser(description="PH1000 inverter telemetry (ShineMonitor)")
    p.add_argument("--credentials", default=str(CREDENTIALS_PATH))
    p.add_argument("--customer", help="Single account label (else all ph1000-flagged accounts)")
    p.add_argument("--devcode", help=f"Override device code (default {DEFAULT_DEVCODE})")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--json-out", help="Write dashboard JSON to this path")
    p.add_argument("--history-days", type=int, default=7)
    p.add_argument("--discover", action="store_true", help="Dump raw endpoints + titles and exit")
    args = p.parse_args()

    company_key, accounts = load_ph1000_accounts(args.credentials, args.customer)
    if not accounts:
        print("[ERROR] No PH1000 accounts found (flag accounts with \"ph1000\": true, "
              "or pass --customer).")
        sys.exit(1)

    rc = 0
    for label, username, password in accounts:
        print(f"\nAccount: {label}")
        token, secret = authenticate(username, password, company_key)
        if not token:
            print("  [ERROR] Auth failed")
            rc = 1
            continue
        rc = process_account(token, secret, label, args) or rc

    sys.exit(rc)


if __name__ == "__main__":
    main()
