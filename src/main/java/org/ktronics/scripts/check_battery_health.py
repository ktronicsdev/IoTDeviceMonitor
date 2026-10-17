#!/usr/bin/env python3
"""
SolisCloud battery health report — monthly (GitHub Actions).

For every inverter on the SolisCloud account that reports a battery it reads the
battery state of health (SOH) and lifetime throughput, works out how fast SOH is
falling since the inverter was commissioned, and emails the admin one table.

No state is kept: SOH is itself cumulative, so the yearly fade rate is simply
(100 - SOH) / years in service. A battery is flagged when it fades faster than
--max-fade %/yr or drops below --min-soh %. LFP packs (BYD, Pylontech, ...)
normally fade about 1-2 %/yr; under 3 months in service the rate is not judged.

Config: the `solis` block of credentials.json (same key as the switch watchdog).

Usage:
    python check_battery_health.py              # report + email the admin
    python check_battery_health.py --dry-run    # print the report only
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import CREDENTIALS_PATH  # noqa: E402
from solis_common import SolisClient, is_success  # noqa: E402

DEFAULT_ADMIN_EMAIL = "ktronicssolar@gmail.com"
EMAIL_CHANNEL = "emails.battery_health_admin"
DEFAULT_MAX_FADE = 3.0     # %/yr
DEFAULT_MIN_SOH = 85.0     # %
MIN_YEARS_TO_JUDGE = 0.25  # younger than ~3 months: rate is noise
REQUEST_GAP_S = 1.0        # stay well under the per-key rate limit

_UNIT_TO_KWH = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0, "GWh": 1_000_000.0}


def load_solis_config():
    with open(CREDENTIALS_PATH, encoding="utf-8") as f:
        solis = json.load(f).get("solis")
    if not solis:
        raise SystemExit("No 'solis' section in credentials.json — see solis/README_SOLIS.md")
    return solis


def to_kwh(value, unit):
    """SolisCloud returns energy with a separate unit string (kWh/MWh/...)."""
    try:
        return float(value) * _UNIT_TO_KWH.get(unit or "kWh", 1.0)
    except (TypeError, ValueError):
        return None


def classify(soh, years, max_fade=DEFAULT_MAX_FADE, min_soh=DEFAULT_MIN_SOH):
    """Return (fade_per_year or None, status). Status: OK, WARN or NEW."""
    if soh < min_soh:
        fade = (100.0 - soh) / years if years and years > 0 else None
        return fade, "WARN"
    if not years or years < MIN_YEARS_TO_JUDGE:
        return None, "NEW"
    fade = (100.0 - soh) / years
    return fade, ("WARN" if fade > max_fade else "OK")


def battery_row(detail, now=None, max_fade=DEFAULT_MAX_FADE, min_soh=DEFAULT_MIN_SOH):
    """Turn an inverterDetail payload into a report row, or None if no battery."""
    soh = detail.get("batteryHealthSoh")
    try:
        soh = float(soh)
    except (TypeError, ValueError):
        return None
    if soh <= 0:
        return None
    now = now or datetime.now(timezone.utc)
    years = None
    commissioned = None
    fis = detail.get("fisGenerateTime")
    if fis:
        commissioned = datetime.fromtimestamp(int(fis) / 1000, tz=timezone.utc)
        years = (now - commissioned).days / 365.25
    fade, status = classify(soh, years, max_fade, min_soh)
    return {
        "station": detail.get("stationName") or "?",
        "name": detail.get("name") or "",
        "sn": str(detail.get("sn") or ""),
        "battery": detail.get("batteryType") or "?",
        "soh": soh,
        "soc": detail.get("batteryCapacitySoc"),
        "charged_kwh": to_kwh(detail.get("batteryTotalChargeEnergy"),
                              detail.get("batteryTotalChargeEnergyStr")),
        "discharged_kwh": to_kwh(detail.get("batteryTotalDischargeEnergy"),
                                 detail.get("batteryTotalDischargeEnergyStr")),
        "commissioned": commissioned.date().isoformat() if commissioned else "?",
        "years": years,
        "fade": fade,
        "status": status,
    }


def format_report(rows, max_fade=DEFAULT_MAX_FADE, min_soh=DEFAULT_MIN_SOH):
    warn = [r for r in rows if r["status"] == "WARN"]
    lines = [
        f"SolisCloud battery health — {len(rows)} batteries, {len(warn)} need attention",
        f"Flag rule: SOH below {min_soh:g} % or fading faster than {max_fade:g} %/yr "
        "(LFP normally 1-2 %/yr).",
        "",
        f"{'Status':6} {'SOH':>5} {'Fade/yr':>8} {'In use':>7} {'Discharged':>11}  Plant (inverter)",
    ]
    order = {"WARN": 0, "NEW": 2, "OK": 1}
    for r in sorted(rows, key=lambda r: (order[r["status"]], r["soh"])):
        fade = f"{r['fade']:.1f} %" if r["fade"] is not None else "-"
        years = f"{r['years']:.1f} y" if r["years"] is not None else "?"
        dis = f"{r['discharged_kwh']:,.0f} kWh" if r["discharged_kwh"] is not None else "?"
        lines.append(f"{r['status']:6} {r['soh']:4.0f}% {fade:>8} {years:>7} {dis:>11}  "
                     f"{r['station']} ({r['name'] or r['sn']}, {r['battery']})")
    if warn:
        lines += ["", "Check the flagged batteries: BMS alarms, cell balance, and whether the "
                  "overdischarge floor or temperature is too harsh."]
    return "\n".join(lines)


def list_inverters(client):
    """All inverters on the account (paginated)."""
    out, page = [], 1
    while True:
        resp = client.inverter_list(page_no=page, page_size=100)
        if not is_success(resp):
            raise SystemExit(f"inverterList failed: {resp.get('msg') or resp.get('code')}")
        page_data = (resp.get("data") or {}).get("page") or {}
        records = page_data.get("records") or []
        out += records
        if not records or len(out) >= int(page_data.get("total") or 0):
            return out
        page += 1
        time.sleep(REQUEST_GAP_S)


def collect_rows(client, inverters, max_fade, min_soh):
    rows = []
    for inv in inverters:
        time.sleep(REQUEST_GAP_S)
        resp = client.inverter_detail(inv.get("id"), inv.get("sn"))
        if not is_success(resp):
            print(f"  [warn] detail failed for {inv.get('sn')}: {resp.get('msg')}")
            continue
        row = battery_row(resp.get("data") or {}, max_fade=max_fade, min_soh=min_soh)
        if row:
            rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser(description="SolisCloud battery health report")
    ap.add_argument("--dry-run", action="store_true", help="print the report, send no email")
    ap.add_argument("--max-fade", type=float, default=DEFAULT_MAX_FADE,
                    help="flag fading faster than this many %%/yr")
    ap.add_argument("--min-soh", type=float, default=DEFAULT_MIN_SOH,
                    help="flag SOH below this %%")
    args = ap.parse_args()

    cfg = load_solis_config()
    client = SolisClient(cfg["key_id"], cfg["key_secret"],
                         **({"base_url": cfg["api_base"]} if cfg.get("api_base") else {}))
    rows = collect_rows(client, list_inverters(client), args.max_fade, args.min_soh)
    report = format_report(rows, args.max_fade, args.min_soh)
    print(report)
    if args.dry_run or not rows:
        return 0

    from email_utils import send_email_smtp
    warn = sum(r["status"] == "WARN" for r in rows)
    subject = (f"🔋 Battery health: {warn} of {len(rows)} need attention" if warn
               else f"🔋 Battery health: all {len(rows)} OK")
    admin = cfg.get("admin_email", DEFAULT_ADMIN_EMAIL)
    return 0 if send_email_smtp(admin, subject, report, channel=EMAIL_CHANNEL) else 1


if __name__ == "__main__":
    sys.exit(main())
