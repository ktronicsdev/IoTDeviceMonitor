#!/usr/bin/env python3
"""
Ingest a single BMS reading pushed by an ESP32 (via GitHub repository_dispatch).

Flow (mirrors the ShineMonitor pattern):
  1. Read the reading from the BMS_PAYLOAD env var (GitHub client_payload JSON).
  2. Append it to a per-device CSV history in bms-module/data/.
  3. Keep a human-readable "latest" snapshot in bms-module/state/bms_latest.json.
  4. Check thresholds (low SOC/voltage, high temp, over-current, cell imbalance).
  5. Email the admin when a threshold is newly crossed, with a re-alert cooldown
     so we don't spam on every 15-minute push.

Reuses the shared SMTP/HTML helpers from the ShineMonitor scripts package.
"""

import os
import sys
import json
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- reuse the existing email stack from the ShineMonitor scripts -----------
REPO_ROOT = Path(__file__).resolve().parents[2]
SHINE_SCRIPTS = REPO_ROOT / "src" / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SHINE_SCRIPTS))
from email_utils import send_email_smtp  # noqa: E402

# --- paths ------------------------------------------------------------------
MODULE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = MODULE_DIR / "data"
STATE_DIR = MODULE_DIR / "state"
LATEST_FILE = STATE_DIR / "bms_latest.json"
ALERT_STATE_FILE = STATE_DIR / "bms_alerts_state.json"

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "ktronicssolar@gmail.com")

# --- alert thresholds (override via env if needed) --------------------------
SOC_MIN = float(os.getenv("BMS_SOC_MIN", "15"))            # % - low battery
VOLT_MIN = float(os.getenv("BMS_VOLT_MIN", "48"))          # V  - pack undervoltage (16S LFP ~ 48V)
VOLT_MAX = float(os.getenv("BMS_VOLT_MAX", "58.4"))        # V  - pack overvoltage
TEMP_MAX = float(os.getenv("BMS_TEMP_MAX", "50"))          # C  - over-temperature
CURRENT_MAX = float(os.getenv("BMS_CURRENT_MAX", "100"))   # A  - over-current (abs)
DELTA_CELL_MAX = float(os.getenv("BMS_DELTA_CELL_MAX", "0.1"))  # V - cell imbalance

REALERT_COOLDOWN_HOURS = float(os.getenv("BMS_REALERT_HOURS", "4"))

CSV_FIELDS = ["timestamp", "voltage", "current", "power", "soc",
              "temp", "min_cell", "max_cell", "delta_cell"]


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8") or "null") or default
        except json.JSONDecodeError:
            return default
    return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def parse_payload():
    """Read the reading JSON from BMS_PAYLOAD env var."""
    raw = os.getenv("BMS_PAYLOAD", "").strip()
    if not raw:
        print("ERROR: BMS_PAYLOAD env var is empty - nothing to ingest.")
        sys.exit(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: BMS_PAYLOAD is not valid JSON: {e}")
        sys.exit(1)

    device = str(data.get("device", "")).strip()
    if not device:
        print("ERROR: payload missing 'device' field.")
        sys.exit(1)
    return device, data


def append_csv(device, reading):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = DATA_DIR / f"{device}.csv"
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(reading)
    return csv_path


def evaluate_alerts(device, reading):
    """Return a list of (code, human_message) for any threshold crossed."""
    alerts = []

    def num(key):
        try:
            return float(reading.get(key))
        except (TypeError, ValueError):
            return None

    soc, volt, temp = num("soc"), num("voltage"), num("temp")
    current, delta = num("current"), num("delta_cell")

    if soc is not None and soc < SOC_MIN:
        alerts.append(("low_soc", f"Low battery: SOC {soc:.0f}% (< {SOC_MIN:.0f}%)"))
    if volt is not None and volt < VOLT_MIN:
        alerts.append(("under_voltage", f"Pack undervoltage: {volt:.2f} V (< {VOLT_MIN:.1f} V)"))
    if volt is not None and volt > VOLT_MAX:
        alerts.append(("over_voltage", f"Pack overvoltage: {volt:.2f} V (> {VOLT_MAX:.1f} V)"))
    if temp is not None and temp > TEMP_MAX:
        alerts.append(("over_temp", f"Over-temperature: {temp:.1f} C (> {TEMP_MAX:.0f} C)"))
    if current is not None and abs(current) > CURRENT_MAX:
        alerts.append(("over_current", f"Over-current: {current:.1f} A (> {CURRENT_MAX:.0f} A)"))
    if delta is not None and delta > DELTA_CELL_MAX:
        alerts.append(("cell_imbalance", f"Cell imbalance: {delta:.3f} V (> {DELTA_CELL_MAX:.3f} V)"))

    return alerts


def should_send(state, device, code, now):
    """Cooldown: only re-alert after REALERT_COOLDOWN_HOURS."""
    key = f"{device}:{code}"
    last = state.get(key, {}).get("last_sent")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (now - last_dt) >= timedelta(hours=REALERT_COOLDOWN_HOURS)


def format_email(device, reading, alerts):
    lines = [
        "KT BMS MONITOR - BATTERY ALERT",
        "=" * 40,
        f"Device : {device}",
        f"Time   : {reading['timestamp']}",
        "",
        "Alert Summary:",
    ]
    for _, msg in alerts:
        lines.append(f"  - {msg}")
    lines += [
        "",
        "Current Reading:",
        f"  Voltage    : {reading.get('voltage')} V",
        f"  Current    : {reading.get('current')} A",
        f"  Power      : {reading.get('power')} W",
        f"  SOC        : {reading.get('soc')} %",
        f"  Temp       : {reading.get('temp')} C",
        f"  Cell delta : {reading.get('delta_cell')} V "
        f"(min {reading.get('min_cell')} / max {reading.get('max_cell')})",
        "",
        "Recommended Actions:",
        "  - Check the battery and inverter at the site.",
        "  - Review live data on the device LAN dashboard.",
    ]
    return "\n".join(lines)


def main():
    now = datetime.now(timezone.utc)
    device, data = parse_payload()

    reading = {
        "timestamp": now.isoformat(timespec="seconds"),
        "voltage": data.get("voltage"),
        "current": data.get("current"),
        "power": data.get("power"),
        "soc": data.get("soc"),
        "temp": data.get("temp"),
        "min_cell": data.get("min_cell"),
        "max_cell": data.get("max_cell"),
        "delta_cell": data.get("delta_cell"),
    }

    csv_path = append_csv(device, reading)
    print(f"Appended reading for '{device}' -> {csv_path}")

    # human-readable latest snapshot (like the flyer's "only latest data stored")
    latest = load_json(LATEST_FILE, {})
    latest[device] = reading
    save_json(LATEST_FILE, latest)

    alerts = evaluate_alerts(device, reading)
    if not alerts:
        print("No thresholds crossed - all normal.")
        return

    state = load_json(ALERT_STATE_FILE, {})
    to_send = [(c, m) for (c, m) in alerts if should_send(state, device, c, now)]

    if not to_send:
        print(f"{len(alerts)} alert(s) active but all within cooldown - not resending.")
        return

    subject = f"🔔 BMS ALERT: {device} ({len(to_send)} issue(s))"
    body = format_email(device, reading, to_send)
    ok = send_email_smtp(ADMIN_EMAIL, subject, body)
    print(f"Alert email to {ADMIN_EMAIL}: {'sent' if ok else 'FAILED'}")

    if not ok:
        # Don't start the cooldown on a send we never made - otherwise an SMTP
        # hiccup silently swallows the alert for REALERT_COOLDOWN_HOURS.
        print("Cooldown NOT recorded - alert will be retried on the next reading.")
        sys.exit(1)

    for code, _ in to_send:
        state[f"{device}:{code}"] = {"last_sent": now.isoformat(timespec="seconds")}
    save_json(ALERT_STATE_FILE, state)


if __name__ == "__main__":
    main()
