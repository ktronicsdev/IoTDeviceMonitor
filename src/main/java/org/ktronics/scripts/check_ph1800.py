#!/usr/bin/env python3
"""
Generic ShineMonitor inverter telemetry — "PH1800" mode (all inverters).

Unlike check_ph1000.py (a bespoke workaround for Mifanza's broken devcode-697 feed),
this is a **pure pass-through**: for every account flagged ``"ph1800": true`` it loops
all of the account's plants, auto-detects each device's devcode, and emits EXACTLY what
ShineMonitor returns — every live field, the raw history, and the plant profile.
**No estimates, no energy integration, no register decode, no BMS — no computed fields.**

Multi-tenant + privacy: one JSON per account, written to ``<sha256(username:password)>.json``
so a plant's data file is not enumerable without its credentials. The dashboard derives the
same filename from the username (embedded in the per-plant page) + the password the user types.

Usage:
    python check_ph1800.py --customer Gayan-IMH --discover
    python check_ph1800.py --out-dir publish --history-days 7   # all ph1800-flagged accounts
"""

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
from config import CREDENTIALS_PATH  # noqa: E402

API_URL = "https://web.shinemonitor.com/public/"
LIVE_ACTION = "queryDeviceLastData"
HISTORY_PAGESIZE = 100


# --------------------------------------------------------------------------- #
# ShineMonitor API helpers (copied from check_ph1000.py — generic, device-agnostic)
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


def get_plant_info(token, secret, plant_id):
    resp = api_call(token, secret, "queryPlantInfo", f"plantid={plant_id}")
    return resp.get("dat", {}) if resp.get("err") == 0 else {}


# --------------------------------------------------------------------------- #
# Small helpers
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


def safe_slug(text):
    s = "".join(ch if ch.isalnum() else "-" for ch in str(text).lower())
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def status_label(status):
    """ShineMonitor device status: 0 == Online."""
    return "Online" if str(status) == "0" else f"Offline ({status})"


def account_file(username, password):
    """Per-account data filename: not enumerable without the credentials (soft privacy)."""
    return hashlib.sha256(f"{username}:{password}".encode()).hexdigest() + ".json"


# Thin per-plant page shell: loads the shared app and supplies the (non-secret) username; the
# user types the password and the app fetches sha256(username:password).json from ph1800-live.
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{label} · Solar</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='26' font-size='26'>☀️</text></svg>">
<link rel="stylesheet" href="../app.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>window.PH1800 = {{ username: {username}, label: {label_js} }};</script>
</head>
<body>
<div id="root"></div>
<script src="../app.js"></script>
</body>
</html>
"""


def ensure_page(pages_dir, label, username):
    """Create site/ph1800/<label>/index.html from the template if it doesn't exist yet.

    Returns True if a new page was created. Never overwrites an existing one.
    """
    page = Path(pages_dir) / label / "index.html"
    if page.exists():
        return False
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        PAGE_TEMPLATE.format(label=label,
                             username=json.dumps(username),
                             label_js=json.dumps(label)),
        encoding="utf-8")
    return True


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
def load_ph1800_accounts(creds_path, customer=None):
    """Return (company_key, [(label, username, password)]) for ph1800-flagged accounts.

    Included if flagged "ph1800": true, OR if --customer matches the label.
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
        elif not acc.get("ph1800"):
            continue
        out.append((label, acc.get("username"), acc.get("password")))
    return company_key, out


# --------------------------------------------------------------------------- #
# Device discovery (generic — devcode auto-detected, NOT hardcoded)
# --------------------------------------------------------------------------- #
def discover_devices(token, secret, plant_id):
    """All devices under a plant, with their REAL devcode read from ShineMonitor.

    `webQueryDeviceEs?pn=<collector pn>` returns each device with its own `devcode`, so no
    device code needs to be known in advance (verified live against Mifanza -> devcode 697).
    """
    devices = []
    for col in get_collectors(token, secret, plant_id):
        if not isinstance(col, dict):
            continue
        pn = _first(col, "pn", "sn", default="")
        if not pn:
            continue
        resp = api_call(token, secret, "webQueryDeviceEs", f"pn={pn}")
        dat = resp.get("dat", {}) if resp.get("err") == 0 else {}
        for dev in (dat.get("device", []) if isinstance(dat, dict) else dat) or []:
            devices.append({
                "pn": _first(dev, "pn", default=pn),
                "devcode": str(_first(dev, "devcode", default="")),
                "devaddr": str(_first(dev, "devaddr", default="1")),
                "sn": _first(dev, "sn", default=pn),
                "alias": _first(dev, "devalias", "alias", default=""),
                "status": _first(dev, "status", "connection", "online", default="unknown"),
            })
    return devices


def _device_params(device):
    return (f"pn={device['pn']}&devcode={device['devcode']}"
            f"&devaddr={device['devaddr']}&sn={device['sn']}&i18n=en_US")


# --------------------------------------------------------------------------- #
# Raw pass-through: live + history (EVERY field ShineMonitor returns)
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


def fetch_live_raw(token, secret, device):
    """Return ({slug: {label, value, unit}}, last_update, raw) for EVERY live field as-is."""
    resp = api_call(token, secret, LIVE_ACTION, _device_params(device))
    if resp.get("err") != 0:
        return {}, None, resp
    fields, last_update = {}, None
    for title, val, unit in _iter_par_entries(resp.get("dat", {})):
        if str(title).lower() == "timestamp":
            last_update = val
            continue
        key = safe_slug(title)
        if key and key not in fields:
            fields[key] = {"label": str(title), "value": val, "unit": unit or ""}
    return fields, last_update, resp


def fetch_history_raw(token, secret, device, days, tz_offset=0):
    """Last `days` days of the full "Data Details" table, as-is. Returns (rows, fields).

    `fields` is the ordered list of {key,label} numeric columns (for the dashboard chart).
    The device logs in plant-local time, so "today" uses tz_offset (plant address.timezone).
    """
    rows, fields = [], []
    today = (datetime.now(timezone.utc) + timedelta(seconds=tz_offset)).date()
    for offset in range(days - 1, -1, -1):
        date_str = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        page, colmap, ts_idx = 0, None, None
        while page < 20:
            resp = api_call(token, secret, "queryDeviceDataOneDayPaging",
                            f"{_device_params(device)}&date={date_str}"
                            f"&page={page}&pagesize={HISTORY_PAGESIZE}")
            if resp.get("err") != 0:
                break
            dat = resp.get("dat", {})
            titles = dat.get("title", []) or []
            if colmap is None:
                colmap = []
                for t in titles:
                    label = str(_first(t, "title", "name", default=t) if isinstance(t, dict) else t)
                    colmap.append((safe_slug(label) or label.lower(), label))
                ts_idx = next((i for i, (k, _l) in enumerate(colmap) if "timestamp" in k), None)
                if not fields:
                    fields = [{"key": k, "label": lbl}
                              for i, (k, lbl) in enumerate(colmap) if i != ts_idx]
            if ts_idx is None:
                break
            page_rows = dat.get("row", []) or []
            for r in page_rows:
                f = r.get("field", [])
                if ts_idx >= len(f):
                    continue
                row = {"timestamp": f[ts_idx]}
                for i, (k, _lbl) in enumerate(colmap):
                    if i == ts_idx or i >= len(f):
                        continue
                    fv = _to_float(f[i])
                    row[k] = fv if fv is not None else f[i]
                rows.append(row)
            if len(page_rows) < HISTORY_PAGESIZE:
                break
            page += 1
            time.sleep(0.1)
        time.sleep(0.1)
    return rows, fields


def build_plant_block(plant_info, device):
    """Plant profile straight from ShineMonitor (no computed energy/gains)."""
    addr = (plant_info or {}).get("address", {}) or {}
    return {
        "type": "PH1800",
        "display_mode": "raw",
        "name": (plant_info or {}).get("name") or device.get("alias") or "Plant",
        "nominal_power_kw": _to_float((plant_info or {}).get("nominalPower")),
        "install": (plant_info or {}).get("install"),
        "country": addr.get("country"),
        "lat": _to_float(_first(addr, "lat", "latitude", "lati")),
        "lon": _to_float(_first(addr, "lng", "lon", "longitude", "long", "longi")),
    }


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def process_plant(token, secret, plant, days):
    """Return a raw block for one plant, or None if it has no readable device."""
    pid = plant.get("pid")
    devices = discover_devices(token, secret, pid)
    if not devices:
        return None
    device = devices[0]                                   # one inverter per plant (typical)
    tz_off = int((plant.get("address", {}) or {}).get("timezone", 0) or 0)
    latest, last_update, _ = fetch_live_raw(token, secret, device)
    series, fields = fetch_history_raw(token, secret, device, days, tz_off)
    plant_block = build_plant_block(get_plant_info(token, secret, pid), device)
    return {
        "device": {
            "pn": device["pn"], "sn": device["sn"], "alias": device["alias"],
            "devcode": device["devcode"],
            "status": status_label(device["status"]),
            "online": str(device["status"]) == "0",
            "last_update": last_update,
        },
        "plant": plant_block,
        "latest": latest,
        "series_fields": fields,
        "series": series,
    }


def process_account(token, secret, label, username, password, args):
    plants = get_plants(token, secret)
    if not plants:
        print(f"  [WARN] No plants for {label}")
        return 1

    blocks = []
    for plant in plants:
        try:
            blk = process_plant(token, secret, plant, args.history_days)
        except Exception as e:                            # one bad plant must not sink the account
            print(f"  [WARN] plant {plant.get('name')} failed: {e}")
            blk = None
        if not blk:
            continue
        blocks.append(blk)
        print(f"  Plant: {blk['plant']['name']} | device {blk['device']['alias']} "
              f"(devcode {blk['device']['devcode']}) | {len(blk['latest'])} live fields, "
              f"{len(blk['series'])} history points")

    if not blocks:
        print(f"  [WARN] No readable devices for {label}")
        return 1

    if args.discover:
        print(json.dumps(blocks[0]["latest"], indent=2, default=str)[:4000])
        return 0

    payload = {
        "account": label,
        "plants": blocks,
        "display_mode": "raw",
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    if args.out_dir:
        out = Path(args.out_dir) / account_file(username, password)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        print(f"  Wrote {out.name} ({len(blocks)} plant(s))")
    return 0


def main():
    p = argparse.ArgumentParser(description="Generic ShineMonitor telemetry (PH1800 / all inverters)")
    p.add_argument("--credentials", default=str(CREDENTIALS_PATH))
    p.add_argument("--customer", help="Single account label (else all ph1800-flagged accounts)")
    p.add_argument("--out-dir", help="Write per-account <hash>.json files here")
    p.add_argument("--pages-dir", help="Ensure a <pages-dir>/<label>/index.html shell exists per account")
    p.add_argument("--history-days", type=int, default=7)
    p.add_argument("--discover", action="store_true", help="Dump the first plant's live fields and exit")
    args = p.parse_args()

    company_key, accounts = load_ph1800_accounts(args.credentials, args.customer)
    if not accounts:
        print("[ERROR] No PH1800 accounts found (flag accounts with \"ph1800\": true, "
              "or pass --customer).")
        sys.exit(1)

    if args.pages_dir:
        created = [label for label, username, _pw in accounts if ensure_page(args.pages_dir, label, username)]
        print(f"[pages] created {len(created)} new page(s): {created}" if created else "[pages] all pages exist")
        if not args.out_dir:
            sys.exit(0)

    rc = 0
    for label, username, password in accounts:
        print(f"\nAccount: {label}")
        token, secret = authenticate(username, password, company_key)
        if not token:
            print("  [ERROR] Auth failed")
            rc = 1
            continue
        rc = process_account(token, secret, label, username, password, args) or rc
    sys.exit(rc)


if __name__ == "__main__":
    main()
