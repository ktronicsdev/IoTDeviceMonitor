#!/usr/bin/env python3
"""
SolisCloud inverter ON/OFF watchdog.

Runs every 2 hours (GitHub Actions). For each configured Solis inverter it:

  1. Reads the live state from SolisCloud.
  2. Decides whether the inverter is OFF (switched off / not producing during
     daylight). Two detection modes:
       * AUTHORITATIVE  - if `onoff_cid` is configured, reads the on/off control
         register via the Control API (atRead). off_value -> OFF.
       * HEURISTIC (default, no Control permission needed) - during the core
         daylight window, fresh data showing pac == 0 means "not producing".
  3. If OFF and auto-control is configured + permitted, sends the remote ON
     command (Control API /v2/api/control) and emails the admin that it was
     re-enabled.
  4. If OFF but auto-control is NOT available, emails the admin to switch it on
     manually (throttled so it doesn't spam every 2 hours).

WHY a watchdog: this plant (Ktronics Imbulgoda) latched OFF for 4 days after
repeated `Uac-Unstable` grid faults and only came back when manually powered on
in the SolisCloud app. This catches that within ~2 hours instead of days.

Config lives under the `solis` key of credentials.json (see solis/README_SOLIS.md).

Usage:
    python check_solis_switch.py                 # check + act (+ email)
    python check_solis_switch.py --dry-run       # detect + email, never send control
    python check_solis_switch.py --list          # list inverters on the account
    python check_solis_switch.py --discover ID   # dump readable control cids (find onoff_cid)
    python check_solis_switch.py --status         # print live state, take no action
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Safe UTF-8 on Windows (matches the other scripts).
try:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
from config import CREDENTIALS_PATH  # noqa: E402
from solis_common import SolisClient, is_success  # noqa: E402

STATE_PATH = Path("state/solis_switch_state.json")
DEFAULT_ADMIN_EMAIL = "ktronicssolar@gmail.com"

# How long to wait before re-sending the "still OFF, switch it on manually" email
# for the same inverter, so a multi-hour outage doesn't spam every 2h run.
MANUAL_EMAIL_THROTTLE_HOURS = 12


# --------------------------------------------------------------------------- #
# Config / state
# --------------------------------------------------------------------------- #
def load_solis_config():
    """Load the `solis` block from credentials.json."""
    with open(CREDENTIALS_PATH, encoding="utf-8") as f:
        creds = json.load(f)
    solis = creds.get("solis")
    if not solis:
        raise SystemExit("No 'solis' section in credentials.json — see solis/README_SOLIS.md")
    return solis


def load_state():
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def now_utc():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
def local_now(cfg):
    """Local wall-clock at the plant (default Sri Lanka, UTC+5:30)."""
    offset = int(cfg.get("tz_offset_minutes", 330))
    return now_utc() + timedelta(minutes=offset)


def in_core_daylight(cfg):
    """True if it's the core PV window locally — when a healthy inverter MUST be
    producing. Outside this window pac==0 is normal (night/dawn/dusk), so the
    heuristic deliberately does nothing."""
    start = int(cfg.get("core_daylight_start_hour", 9))
    end = int(cfg.get("core_daylight_end_hour", 15))
    hour = local_now(cfg).hour
    return start <= hour < end


def parse_detail(resp):
    """Pull the bits we care about out of an inverterDetail response."""
    data = resp.get("data") or {}
    pac = data.get("pac")
    try:
        pac = float(pac)
    except (TypeError, ValueError):
        pac = None
    ts = data.get("dataTimestamp") or data.get("dataTimestampStr")
    return {
        "state": data.get("state"),          # 1 online, 2 offline, 3 alarm
        "pac": pac,                            # AC power (kW or W per pacUnit)
        "pac_unit": data.get("pacStr") or data.get("pacUnit"),
        "data_timestamp": ts,
        "raw": data,
    }


def data_is_fresh(detail, max_age_min=90):
    """Detail timestamp within max_age_min minutes. Stale data => can't trust
    pac==0 as 'off' (it might just be an old reading), so we treat it as unknown."""
    ts = detail.get("data_timestamp")
    if not ts:
        return False
    try:
        ms = int(ts)
    except (TypeError, ValueError):
        return False
    age = now_utc() - datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return timedelta(0) <= age <= timedelta(minutes=max_age_min)


def detect_off(client, cfg, inv, detail):
    """Return (is_off, reason, mode).

    mode 'control'   -> authoritative read of the on/off register.
    mode 'heuristic' -> daylight + pac==0 inference.
    mode 'unknown'   -> couldn't determine (don't act)."""
    onoff_cid = cfg.get("onoff_cid")
    off_value = str(cfg.get("off_value", ""))

    # Authoritative path: read the actual on/off switch register.
    if onoff_cid:
        resp = client.at_read(inv["inverter_id"], onoff_cid)
        if is_success(resp):
            value = str((resp.get("data") or {}).get("value", resp.get("msg", "")))
            if off_value and value == off_value:
                return True, f"on/off register (cid {onoff_cid}) = OFF ({value})", "control"
            return False, f"on/off register (cid {onoff_cid}) = {value}", "control"
        # fall through to heuristic if the read failed
        print(f"  [warn] atRead(cid={onoff_cid}) failed: {resp.get('msg') or resp.get('code')}")

    # Heuristic path: only meaningful during the core daylight window.
    if not in_core_daylight(cfg):
        return False, "outside core daylight window — heuristic skipped", "unknown"
    if not data_is_fresh(detail):
        return False, "telemetry stale — cannot infer", "unknown"
    if detail["pac"] is None:
        return False, "no pac in telemetry — cannot infer", "unknown"
    if detail["pac"] <= 0:
        return True, f"daylight + fresh data + pac={detail['pac']} (zero output)", "heuristic"
    return False, f"producing pac={detail['pac']}{detail.get('pac_unit') or ''}", "heuristic"


# --------------------------------------------------------------------------- #
# Action + email
# --------------------------------------------------------------------------- #
def try_enable(client, cfg, inv, dry_run):
    """Send the remote ON command. Returns (attempted, ok, message)."""
    onoff_cid = cfg.get("onoff_cid")
    on_value = cfg.get("on_value")
    if not onoff_cid or on_value is None:
        return False, False, ("auto-control not configured (onoff_cid/on_value missing) — "
                              "manual switch-on required")
    if dry_run:
        return True, False, f"[dry-run] would send control cid={onoff_cid} value={on_value}"
    resp = client.control(inv["inverter_id"], onoff_cid, on_value)
    if is_success(resp):
        return True, True, f"control cid={onoff_cid} value={on_value} accepted"
    return True, False, (f"control failed: {resp.get('msg') or resp.get('code')} "
                         "(is the Control API permission enabled?)")


def send_admin_email(cfg, subject, body):
    """Send via the shared SMTP util. Returns True/False; never raises."""
    try:
        from email_utils import send_email_smtp
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] email_utils unavailable: {e}")
        return False
    admin = cfg.get("admin_email", DEFAULT_ADMIN_EMAIL)
    return send_email_smtp(admin, subject, body)


def handle_inverter(client, cfg, inv, state, dry_run):
    """Check one inverter, act if needed, update its state entry. Returns a
    short status string for the run summary."""
    label = inv.get("label", inv.get("inverter_id"))
    print(f"\n=== {label} (id={inv['inverter_id']}) ===")

    detail_resp = client.inverter_detail(inv["inverter_id"], inv.get("sn"))
    if not is_success(detail_resp):
        msg = detail_resp.get("msg") or detail_resp.get("code")
        print(f"  [error] inverterDetail failed: {msg}")
        return f"{label}: detail error ({msg})"

    detail = parse_detail(detail_resp)
    print(f"  state={detail['state']} pac={detail['pac']}{detail.get('pac_unit') or ''} "
          f"ts={detail['data_timestamp']}")

    is_off, reason, mode = detect_off(client, cfg, inv, detail)
    print(f"  detect: off={is_off} mode={mode} ({reason})")

    key = str(inv["inverter_id"])
    entry = state.get(key, {})
    entry.update({
        "label": label,
        "last_check": now_utc().isoformat(),
        "last_state": detail["state"],
        "last_pac": detail["pac"],
        "last_reason": reason,
    })

    result = f"{label}: "
    if not is_off:
        entry["off"] = False
        result += "OK (on/producing)" if mode != "unknown" else f"skipped ({reason})"
        state[key] = entry
        return result

    # --- inverter is OFF: act ------------------------------------------------ #
    entry["off"] = True
    attempted, ok, action_msg = try_enable(client, cfg, inv, dry_run)
    print(f"  action: {action_msg}")

    if attempted and ok:
        entry["last_enabled_at"] = now_utc().isoformat()
        send_admin_email(
            cfg,
            f"🔌 Solis: {label} was OFF — auto re-enabled",
            _enabled_body(label, inv, detail, reason, action_msg),
        )
        result += "was OFF -> auto-enabled (email sent)"
    else:
        # Could not / did not auto-enable -> notify, but throttle the manual nag.
        last_nag = entry.get("last_manual_email_at")
        throttled = False
        if last_nag and not dry_run:
            try:
                age = now_utc() - datetime.fromisoformat(last_nag)
                throttled = age < timedelta(hours=MANUAL_EMAIL_THROTTLE_HOURS)
            except ValueError:
                throttled = False
        if not throttled:
            send_admin_email(
                cfg,
                f"⚠️ Solis: {label} is OFF — manual switch-on needed",
                _manual_body(label, inv, detail, reason, action_msg, dry_run),
            )
            entry["last_manual_email_at"] = now_utc().isoformat()
            result += "is OFF -> notified admin (manual action)"
        else:
            result += "is OFF -> email throttled (already notified)"

    state[key] = entry
    return result


# --------------------------------------------------------------------------- #
# Email bodies
# --------------------------------------------------------------------------- #
def _portal_link(inv):
    sid = inv.get("station_id", "")
    iid = inv.get("inverter_id", "")
    if sid and iid:
        return (f"https://www.soliscloud.com/station/inverter/inverterdetail?"
                f"id={iid}&stationId={sid}")
    return "https://www.soliscloud.com/"


def _enabled_body(label, inv, detail, reason, action_msg):
    return (
        f"SolisCloud watchdog re-enabled an inverter that was switched OFF.\n\n"
        f"Inverter : {label}\n"
        f"Detected : {reason}\n"
        f"Action   : {action_msg}\n"
        f"State    : {detail['state']}  pac={detail['pac']}{detail.get('pac_unit') or ''}\n\n"
        f"Open in SolisCloud:\n{_portal_link(inv)}\n\n"
        f"If this keeps recurring, the root cause is most likely unstable grid "
        f"voltage (Uac-Unstable / over-voltage) tripping the inverter into a "
        f"latched OFF state. Ask CEB/LECO to check the supply voltage and have "
        f"the installer review the grid-protection settings and AC cable sizing.\n"
    )


def _manual_body(label, inv, detail, reason, action_msg, dry_run):
    head = "[DRY-RUN] " if dry_run else ""
    return (
        f"{head}SolisCloud watchdog detected an inverter that appears to be OFF "
        f"and could NOT auto-enable it.\n\n"
        f"Inverter : {label}\n"
        f"Detected : {reason}\n"
        f"Why no auto-enable: {action_msg}\n"
        f"State    : {detail['state']}  pac={detail['pac']}{detail.get('pac_unit') or ''}\n\n"
        f"ACTION NEEDED: open SolisCloud and switch the inverter ON.\n"
        f"{_portal_link(inv)}\n\n"
        f"To enable automatic switch-on, configure `onoff_cid` / `on_value` in the\n"
        f"`solis` section of credentials.json (see solis/README_SOLIS.md) and make sure the\n"
        f"Solis Control API permission is enabled for your API key.\n"
    )


# --------------------------------------------------------------------------- #
# Auxiliary modes
# --------------------------------------------------------------------------- #
def mode_list(client):
    resp = client.inverter_list()
    if not is_success(resp):
        print(f"inverterList failed: {resp.get('msg') or resp.get('code')}")
        return 1
    records = (resp.get("data") or {}).get("page", {}).get("records", []) \
        or (resp.get("data") or {}).get("records", [])
    print(f"Found {len(records)} inverter(s):")
    for r in records:
        print(f"  label={r.get('name') or r.get('stationName')}  "
              f"id={r.get('id')}  sn={r.get('sn')}  stationId={r.get('stationId')}  "
              f"state={r.get('state')}")
    return 0


def mode_discover(client, inverter_id):
    resp = client.at_read_list(inverter_id)
    print(json.dumps(resp, indent=2))
    print("\nLook for the on/off control entry above; put its cid in `onoff_cid` "
          "and the ON/OFF values in `on_value`/`off_value` in credentials.json.")
    return 0 if is_success(resp) else 1


def mode_status(client, cfg, inverters):
    for inv in inverters:
        resp = client.inverter_detail(inv["inverter_id"], inv.get("sn"))
        d = parse_detail(resp)
        print(f"{inv.get('label')}: state={d['state']} pac={d['pac']}"
              f"{d.get('pac_unit') or ''} ts={d['data_timestamp']} "
              f"(success={is_success(resp)})")
    return 0


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="SolisCloud inverter ON/OFF watchdog")
    ap.add_argument("--dry-run", action="store_true",
                    help="detect and email, but never send a control command")
    ap.add_argument("--list", action="store_true",
                    help="list inverters on the account and exit")
    ap.add_argument("--discover", metavar="INVERTER_ID",
                    help="dump readable control registers for an inverter and exit")
    ap.add_argument("--status", action="store_true",
                    help="print live state for configured inverters and exit")
    args = ap.parse_args()

    cfg = load_solis_config()
    if not cfg.get("key_id") or not cfg.get("key_secret"):
        raise SystemExit(
            "Solis API credentials missing: set solis.key_id and solis.key_secret in "
            "credentials.json (SolisCloud -> Service -> API Management). See solis/README_SOLIS.md")
    client = SolisClient(
        cfg.get("key_id"),
        cfg.get("key_secret"),
        base_url=cfg.get("api_base", "https://www.soliscloud.com:13333"),
    )

    if args.list:
        return mode_list(client)
    if args.discover:
        return mode_discover(client, args.discover)

    inverters = cfg.get("inverters", [])
    if not inverters:
        raise SystemExit("No inverters configured under solis.inverters in credentials.json")

    if args.status:
        return mode_status(client, cfg, inverters)

    state = load_state()
    summary = []
    for inv in inverters:
        if not inv.get("inverter_id"):
            print(f"[skip] inverter without inverter_id: {inv}")
            continue
        try:
            summary.append(handle_inverter(client, cfg, inv, state, args.dry_run))
        except Exception as e:  # noqa: BLE001 - one bad inverter must not abort the rest
            print(f"  [error] {inv.get('label')}: {e}")
            summary.append(f"{inv.get('label')}: error ({e})")
    save_state(state)

    print("\n📊 Summary:")
    for line in summary:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
