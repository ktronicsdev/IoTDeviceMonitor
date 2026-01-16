#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from config import CREDENTIALS_PATH


# Matches: <anything>-YYYY-MM.csv  (your monthly output files)
CSV_PATTERN = re.compile(r"^(?P<plant_key>.+)-(?P<ym>\d{4}-\d{2})\.csv$")


@dataclass(frozen=True)
class DayPoint:
    d: date
    kwh: float


def parse_float(x: str) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def month_str(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def add_months(ym: str, delta: int) -> str:
    y, m = map(int, ym.split("-"))
    m0 = (y * 12 + (m - 1)) + delta
    y2 = m0 // 12
    m2 = m0 % 12 + 1
    return f"{y2:04d}-{m2:02d}"


def daterange(start: date, end_inclusive: date) -> List[date]:
    out = []
    d = start
    while d <= end_inclusive:
        out.append(d)
        d += timedelta(days=1)
    return out


def load_daily_series(data_dir: Path, platform_filter: Optional[str] = None) -> Dict[str, Dict[date, float]]:
    """
    Load all monthly csvs in data_dir and merge into per-plant daily maps.
    plant_key is filename prefix (everything before -YYYY-MM.csv).

    UC10: If platform_filter is specified, only load files starting with that prefix.
    E.g., platform_filter="dessmonitor" will only load "dessmonitor-*.csv" files.
    """
    plants: Dict[str, Dict[date, float]] = {}

    # Determine glob pattern based on platform filter
    if platform_filter:
        glob_pattern = f"{platform_filter}-*.csv"
    else:
        glob_pattern = "*.csv"

    for p in sorted(data_dir.glob(glob_pattern)):
        m = CSV_PATTERN.match(p.name)
        if not m:
            continue
        plant_key = m.group("plant_key")

        with p.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                continue
            if "date" not in reader.fieldnames or "kwh" not in reader.fieldnames:
                continue

            for row in reader:
                ds = (row.get("date") or "").strip()
                vs = (row.get("kwh") or "").strip()
                if not ds:
                    continue
                try:
                    d = datetime.strptime(ds, "%Y-%m-%d").date()
                except Exception:
                    continue
                plants.setdefault(plant_key, {})[d] = parse_float(vs)

    return plants


def compute_month_totals(daily: Dict[date, float]) -> Dict[str, float]:
    """
    Sum daily kwh into YYYY-MM totals.
    """
    totals: Dict[str, float] = {}
    for d, v in daily.items():
        totals[month_str(d)] = totals.get(month_str(d), 0.0) + float(v)
    return totals


def avg(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def consecutive_condition(days: List[date], daily: Dict[date, float], predicate) -> Tuple[int, List[Tuple[date, float]]]:
    """
    Returns max consecutive run length and the last best run (as date,kwh list).
    """
    max_run = 0
    cur_run = 0
    cur: List[Tuple[date, float]] = []
    best: List[Tuple[date, float]] = []

    for d in days:
        v = daily.get(d, 0.0)
        if predicate(d, v):
            cur_run += 1
            cur.append((d, v))
            if cur_run >= max_run:
                max_run = cur_run
                best = list(cur)
        else:
            cur_run = 0
            cur = []
    return max_run, best


def read_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def write_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out-dir", default="alerts")
    ap.add_argument("--state-file", default="state/alerts_state.json")

    # Daily rules
    ap.add_argument("--red-pct", type=float, default=20.0, help="Red: < this %% of baseline")
    ap.add_argument("--red-days", type=int, default=3, help="Red: consecutive days")
    ap.add_argument("--daily-baseline-days", type=int, default=14, help="Daily baseline lookback days")

    # Monthly rules
    ap.add_argument("--orange-pct", type=float, default=40.0, help="Orange: < this %% of baseline")
    ap.add_argument("--orange-months", type=int, default=3, help="Orange: consecutive months")
    ap.add_argument("--monthly-baseline-months", type=int, default=6, help="Monthly baseline lookback months")

    # Ignore rule
    ap.add_argument("--ignore-zero-months", type=int, default=1, help="If month total == 0 for N months, ignore plant")

    # UC10: Multi-platform support
    ap.add_argument("--platform", default=None, help="Filter CSV files by platform prefix (e.g., 'dessmonitor' for dessmonitor-*.csv)")
    ap.add_argument("--output-file", default=None, help="Custom output file path for alerts text")
    ap.add_argument("--json-output", default=None, help="Custom output file path for alerts JSON")

    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    state_path = Path(args.state_file)
    out_dir.mkdir(parents=True, exist_ok=True)

    today = utc_today()

    # UC10: Pass platform filter to load only relevant CSV files
    plants_daily = load_daily_series(data_dir, platform_filter=args.platform)
    state = read_state(state_path)

    # UC10: Determine output file paths
    if args.output_file:
        output_txt_path = Path(args.output_file)
        output_txt_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        output_txt_path = out_dir / "alerts.txt"

    if args.json_output:
        output_json_path = Path(args.json_output)
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        output_json_path = out_dir / "alerts.json"

    # UC10: Platform label for reporting
    platform_label = args.platform.upper() if args.platform else "SHINEMONITOR"

    alerts: List[dict] = []
    suppressed: List[dict] = []
    ignored: List[dict] = []

    for plant_key, daily in plants_daily.items():
        if not daily:
            continue

        # --- Monthly ignore rule: if a full month total is 0 for 1 consecutive month, ignore ---
        month_totals = compute_month_totals(daily)
        if month_totals:
            latest_month = max(month_totals.keys())
            # Check ignore window: last N months including latest_month
            ignore_months = [add_months(latest_month, -i) for i in range(args.ignore_zero_months)]
            if all(month_totals.get(m, 0.0) == 0.0 for m in ignore_months):
                ignored.append({
                    "plant_key": plant_key,
                    "reason": f"ignored: month total == 0 for {args.ignore_zero_months} consecutive month(s)",
                    "months": ignore_months,
                })
                continue

        # --- RED alert: < 20% baseline for 3 consecutive days ---
        window_days = daterange(today - timedelta(days=args.red_days - 1), today)

        baseline_end = window_days[0] - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=args.daily_baseline_days - 1)
        baseline_days = daterange(baseline_start, baseline_end)

        baseline_avg = avg([daily.get(d, 0.0) for d in baseline_days])
        # If baseline is 0, we can’t compute percentage meaningfully; still detect 0-run for suppression rule
        red_threshold = (baseline_avg * (args.red_pct / 100.0)) if baseline_avg > 0 else 0.0

        def is_red(d: date, v: float) -> bool:
            if baseline_avg == 0.0:
                # If baseline is 0, treat red only if it's also 0 (but that's handled by suppression/ignore rules)
                return v == 0.0
            return v < red_threshold

        red_run, red_details = consecutive_condition(window_days, daily, is_red)

        # --- Special suppression: if production is 0 for 3 days AND red already sent => suppress ---
        def is_zero(d: date, v: float) -> bool:
            return v == 0.0

        zero_run, zero_details = consecutive_condition(window_days, daily, is_zero)

        red_already_sent = bool(state.get("red_sent", {}).get(plant_key))
        if zero_run >= 3 and red_already_sent:
            suppressed.append({
                "plant_key": plant_key,
                "reason": "suppressed: 0 kWh for 3 days and red already sent",
                "window": [str(d) for d in window_days],
                "zero_run": [{"date": str(d), "kwh": v} for d, v in zero_details],
            })
            continue

        if red_run >= args.red_days and baseline_avg > 0:
            alerts.append({
                "severity": "RED",
                "plant_key": plant_key,
                "today": str(today),
                "baseline_avg_kwh_per_day": round(baseline_avg, 4),
                "threshold_kwh_per_day": round(red_threshold, 4),
                "rule": f"< {args.red_pct}% baseline for {args.red_days} consecutive days",
                "window": [str(d) for d in window_days],
                "details": [{"date": str(d), "kwh": v} for d, v in red_details],
            })

        # --- ORANGE alert: < 40% baseline for 3 consecutive months ---
        if month_totals:
            latest_month = max(month_totals.keys())
            orange_months = [add_months(latest_month, -i) for i in reversed(range(args.orange_months))]
            # Baseline months: months immediately before orange window
            baseline_start_month = add_months(orange_months[0], -args.monthly_baseline_months)
            baseline_months = [add_months(baseline_start_month, i) for i in range(args.monthly_baseline_months)]

            baseline_month_vals = [month_totals.get(m, 0.0) for m in baseline_months]
            monthly_baseline_avg = avg(baseline_month_vals)
            orange_threshold = monthly_baseline_avg * (args.orange_pct / 100.0) if monthly_baseline_avg > 0 else 0.0

            orange_vals = [(m, month_totals.get(m, 0.0)) for m in orange_months]
            orange_hit = (
                monthly_baseline_avg > 0
                and all(v < orange_threshold for _, v in orange_vals)
            )

            if orange_hit:
                alerts.append({
                    "severity": "ORANGE",
                    "plant_key": plant_key,
                    "today": str(today),
                    "baseline_avg_kwh_per_month": round(monthly_baseline_avg, 4),
                    "threshold_kwh_per_month": round(orange_threshold, 4),
                    "rule": f"< {args.orange_pct}% baseline for {args.orange_months} consecutive months",
                    "months": orange_months,
                    "details": [{"month": m, "kwh": round(v, 4)} for m, v in orange_vals],
                })

    # --- Write outputs ---
    result = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "alerts": alerts,
        "suppressed": suppressed,
        "ignored": ignored,
    }

    # UC10: Write to custom JSON output path
    output_json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    lines: List[str] = []

    # --- Header with prominent status ---
    # UC10: Include platform label in header
    lines.append("=" * 80)
    lines.append("║" + " " * 78 + "║")
    lines.append("║" + f"[{platform_label}] PRODUCTION MONITOR".center(78) + "║")
    lines.append("║" + " " * 78 + "║")
    if not alerts:
        lines.append("║" + "✓ ALL SYSTEMS OPERATIONAL".center(78) + "║")
        lines.append("║" + " " * 78 + "║")
        lines.append("║" + f"Status: NO ALERTS DETECTED".center(78) + "║")
    else:
        lines.append("║" + "⚠ ATTENTION REQUIRED ⚠".center(78) + "║")
        lines.append("║" + " " * 78 + "║")
        red_count = sum(1 for a in alerts if a["severity"] == "RED")
        orange_count = sum(1 for a in alerts if a["severity"] == "ORANGE")
        status_line = f"Status: {red_count} CRITICAL, {orange_count} WARNING"
        lines.append("║" + status_line.center(78) + "║")

    lines.append("║" + " " * 78 + "║")
    lines.append("║" + f"Date: {today}  |  Total Plants Monitored: {len(plants_daily)}".center(78) + "║")
    lines.append("║" + " " * 78 + "║")
    lines.append("=" * 80)
    lines.append("")

    # --- Summary Section ---
    if alerts:
        lines.append("┌─ ALERT SUMMARY " + "─" * 62 + "┐")
        lines.append("│")

        for a in alerts:
            severity_symbol = "🔴" if a["severity"] == "RED" else "🟠"
            lines.append(f"│ {severity_symbol} [{a['severity']}] {a['plant_key']}")
            lines.append("│")
            if a["severity"] == "RED":
                baseline = a['baseline_avg_kwh_per_day']
                latest_kwh = a['details'][-1]['kwh'] if a['details'] else 0
                drop_pct = ((baseline - latest_kwh) / baseline * 100) if baseline > 0 else 0
                lines.append(f"│   Issue: Production dropped {drop_pct:.1f}% below normal")
                lines.append(f"│   Normal: {baseline:.2f} kWh/day  →  Current: {latest_kwh:.2f} kWh/day")
                lines.append(f"│   Rule: {a['rule']}")
            else:
                baseline = a['baseline_avg_kwh_per_month']
                latest_kwh = a['details'][-1]['kwh'] if a['details'] else 0
                drop_pct = ((baseline - latest_kwh) / baseline * 100) if baseline > 0 else 0
                lines.append(f"│   Issue: Production dropped {drop_pct:.1f}% below normal")
                lines.append(f"│   Normal: {baseline:.2f} kWh/month  →  Current: {latest_kwh:.2f} kWh/month")
                lines.append(f"│   Rule: {a['rule']}")
            lines.append("│")

        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        # --- Recommended Actions ---
        lines.append("┌─ RECOMMENDED ACTIONS " + "─" * 56 + "┐")
        lines.append("│")
        for a in alerts:
            if a["severity"] == "RED":
                lines.append(f"│ {a['plant_key']}:")
                lines.append("│   1. Check inverter status and error codes")
                lines.append("│   2. Verify grid connection and breaker status")
                lines.append("│   3. Inspect panels for shading or physical damage")
                lines.append("│   4. Contact maintenance team if issue persists")
            else:
                lines.append(f"│ {a['plant_key']}:")
                lines.append("│   1. Review monthly production trends")
                lines.append("│   2. Check for seasonal factors (weather, shading)")
                lines.append("│   3. Schedule maintenance inspection")
        lines.append("│")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        # --- Detailed Breakdown ---
        lines.append("┌─ DETAILED BREAKDOWN " + "─" * 57 + "┐")
        lines.append("│")
        for a in alerts:
            lines.append(f"│ [{a['severity']}] {a['plant_key']}")
            lines.append("│")
            if a["severity"] == "RED":
                lines.append(f"│   Baseline (avg/day): {a['baseline_avg_kwh_per_day']:.4f} kWh")
                lines.append(f"│   Alert Threshold: < {a['threshold_kwh_per_day']:.4f} kWh/day")
                lines.append("│   Recent Production:")
                for r in a["details"]:
                    pct = (r['kwh'] / a['baseline_avg_kwh_per_day'] * 100) if a['baseline_avg_kwh_per_day'] > 0 else 0
                    lines.append(f"│     {r['date']}: {r['kwh']:.4f} kWh ({pct:.1f}% of baseline)")
            else:
                lines.append(f"│   Baseline (avg/month): {a['baseline_avg_kwh_per_month']:.4f} kWh")
                lines.append(f"│   Alert Threshold: < {a['threshold_kwh_per_month']:.4f} kWh/month")
                lines.append("│   Recent Months:")
                for r in a["details"]:
                    pct = (r['kwh'] / a['baseline_avg_kwh_per_month'] * 100) if a['baseline_avg_kwh_per_month'] > 0 else 0
                    lines.append(f"│     {r['month']}: {r['kwh']:.4f} kWh ({pct:.1f}% of baseline)")
            lines.append("│")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

    # --- Suppressed/Ignored Section ---
    if suppressed or ignored:
        lines.append("┌─ ADDITIONAL INFORMATION " + "─" * 52 + "┐")
        lines.append("│")

        if ignored:
            lines.append("│ IGNORED PLANTS (No production for extended period):")
            for x in ignored:
                lines.append(f"│   • {x['plant_key']}")
                lines.append(f"│     Reason: {x['reason']}")
            lines.append("│")

        if suppressed:
            lines.append("│ SUPPRESSED ALERTS (Already notified, zero production continues):")
            for x in suppressed:
                lines.append(f"│   • {x['plant_key']}")
                lines.append(f"│     Reason: {x['reason']}")
            lines.append("│")

        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

    # UC10: Write to custom text output path
    output_txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # --- 3-Day Auto-Ignore Rule for Admin Alerts ---
    # Track when alerts were first seen and how many times sent
    # Auto-ignore after 3 consecutive days to prevent alert fatigue
    auto_ignore_days = 3
    today_date = today
    all_generated_alerts = list(alerts)  # Keep copy of all generated alerts for cleanup
    alerts_to_send = []
    auto_ignored = []

    for a in all_generated_alerts:
        plant_key = a["plant_key"]
        severity = a["severity"]
        alert_key = f"{plant_key}:{severity}"

        # Initialize state for this alert if not exists
        if alert_key not in state:
            state[alert_key] = {
                "first_seen": str(today_date),
                "last_sent": None,
                "send_count": 0,
                "ignored_date": None
            }

        alert_state = state[alert_key]
        first_seen = date.fromisoformat(alert_state['first_seen'])
        days_active = (today_date - first_seen).days

        # Auto-ignore rule: if alert active for >= auto_ignore_days, skip it
        if days_active >= auto_ignore_days and not alert_state.get('ignored_date'):
            alert_state['ignored_date'] = str(today_date)
            auto_ignored.append({
                "plant_key": plant_key,
                "severity": severity,
                "days_active": days_active,
                "first_seen": str(first_seen),
                "reason": f"Auto-ignored after {auto_ignore_days} days"
            })
            continue

        # If already ignored, skip
        if alert_state.get('ignored_date'):
            continue

        # Update state and add to alerts_to_send
        alert_state['last_sent'] = str(today_date)
        alert_state['send_count'] += 1
        alerts_to_send.append(a)

    # Replace alerts with filtered list (only alerts not auto-ignored)
    alerts = alerts_to_send

    # Update result with auto-ignored alerts
    if auto_ignored:
        result["auto_ignored"] = auto_ignored

    # --- Cleanup: Reset state when alert is resolved ---
    # If a plant no longer has an alert, remove its state entry
    current_alert_keys = {f"{a['plant_key']}:{a['severity']}" for a in all_generated_alerts}
    all_alert_keys = [k for k in list(state.keys()) if k != "red_sent"]  # Keep old red_sent for compatibility
    for alert_key in all_alert_keys:
        if alert_key not in current_alert_keys and ":" in alert_key:
            # Alert resolved, remove from state
            del state[alert_key]

    # --- Generate Customer-Specific Alerts ---
    # Load credentials to map plants to customers
    credentials_path = CREDENTIALS_PATH
    customer_alerts_output = {}

    if credentials_path.exists():
        try:
            with open(credentials_path, 'r') as f:
                credentials_data = json.load(f)

            # Build plant-to-customer mapping
            plant_to_customer = {}
            for account in credentials_data.get('accounts', []):
                customer_label = account.get('label', '')
                customer_email = account.get('email')
                if customer_label:
                    # Normalize for matching
                    normalized = re.sub(r'[^a-z0-9]', '', customer_label.lower())
                    plant_to_customer[normalized] = {
                        'label': customer_label,
                        'email': customer_email
                    }

            # Map alerts to customers
            customer_alerts_map = {}
            for alert in alerts:  # Only alerts being sent (not auto-ignored)
                plant_key = alert['plant_key']
                severity = alert['severity']

                # Try to match plant to customer
                plant_normalized = re.sub(r'[^a-z0-9]', '', plant_key.lower())
                matched_customer = None

                for customer_norm, customer_info in plant_to_customer.items():
                    if customer_norm in plant_normalized or plant_normalized.startswith(customer_norm[:6]):
                        matched_customer = customer_info
                        break

                if matched_customer and matched_customer['email']:
                    customer_label = matched_customer['label']
                    if customer_label not in customer_alerts_map:
                        customer_alerts_map[customer_label] = {
                            'email': matched_customer['email'],
                            'alerts': []
                        }

                    customer_alerts_map[customer_label]['alerts'].append({
                        'plant': plant_key,
                        'level': severity,
                        'issue': alert.get('rule', 'Production anomaly detected'),
                        'normal': alert.get('baseline_avg_kwh_per_day') or alert.get('baseline_avg_kwh_per_month', 0),
                        'current': alert['details'][-1] if alert.get('details') else {}
                    })

            customer_alerts_output = {'customer_alerts': customer_alerts_map}

            # Write customer alerts JSON
            customer_alerts_file = out_dir / "customer_alerts.json"
            customer_alerts_file.write_text(json.dumps(customer_alerts_output, indent=2), encoding="utf-8")

        except Exception as e:
            print(f"Warning: Could not generate customer alerts: {e}", file=sys.stderr)

    write_state(state_path, state)

    # Exit code: 2 means "alerts found" (handy for workflow conditional)
    return 2 if alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())
