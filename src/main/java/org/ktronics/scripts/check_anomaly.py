#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Optional


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


def load_daily_series(data_dir: Path) -> Dict[str, Dict[date, float]]:
    """
    Load all monthly csvs in data_dir and merge into per-plant daily maps.
    plant_key is filename prefix (everything before -YYYY-MM.csv).
    """
    plants: Dict[str, Dict[date, float]] = {}

    for p in sorted(data_dir.glob("*.csv")):
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
    ap.add_argument("--red-pct", type=float, default=20.0, help="Red: < this % of baseline")
    ap.add_argument("--red-days", type=int, default=3, help="Red: consecutive days")
    ap.add_argument("--daily-baseline-days", type=int, default=14, help="Daily baseline lookback days")

    # Monthly rules
    ap.add_argument("--orange-pct", type=float, default=40.0, help="Orange: < this % of baseline")
    ap.add_argument("--orange-months", type=int, default=3, help="Orange: consecutive months")
    ap.add_argument("--monthly-baseline-months", type=int, default=6, help="Monthly baseline lookback months")

    # Ignore rule
    ap.add_argument("--ignore-zero-months", type=int, default=1, help="If month total == 0 for N months, ignore plant")

    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    state_path = Path(args.state_file)
    out_dir.mkdir(parents=True, exist_ok=True)

    today = utc_today()

    plants_daily = load_daily_series(data_dir)
    state = read_state(state_path)

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

    (out_dir / "alerts.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    lines: List[str] = []

    # --- Header with prominent status ---
    lines.append("=" * 80)
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

    (out_dir / "alerts.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # --- Update state: record red sent plants for suppression ---
    st_red = state.get("red_sent", {})
    for a in alerts:
        if a.get("severity") == "RED":
            st_red[ a["plant_key"] ] = {
                "last_sent": datetime.utcnow().isoformat() + "Z",
                "rule": a.get("rule"),
            }
    state["red_sent"] = st_red
    write_state(state_path, state)

    # Exit code: 2 means "alerts found" (handy for workflow conditional)
    return 2 if alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())
