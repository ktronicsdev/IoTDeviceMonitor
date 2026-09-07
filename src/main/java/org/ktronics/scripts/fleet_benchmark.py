#!/usr/bin/env python3
"""
Fleet benchmark: was it the weather, or was it the plant?

When a customer disputes low production, "it was cloudy" is unprovable and
usually wrong. This turns the fleet itself into the weather reference: every
plant in data/ sees roughly the same Sri Lankan sky on the same day, so the
fleet's *collective* daily output is a usable irradiance proxy that nobody can
argue with — it is the customer's neighbours' own meters.

The fleet index for a day is the mean, across producing plants, of that plant's
output as a fraction of its own best day in the window. Normalising per plant
cancels out differing array sizes, so a 3 kW and a 10 kW system contribute
equally. ~85-90% means a good day island-wide; ~60% means a genuinely poor one.

Then one plant is held against it. If the fleet had a good week and the target
plant did not, the shortfall is the plant's, and the size of it is quantified.

    # a plant already tracked in data/ - expectation calibrated from its own best days
    python fleet_benchmark.py --target gayan-imh-imbulgoda-3kw

    # a plant on another platform, from the portal's own export
    python fleet_benchmark.py --target-csv surath.csv --label "Surath 5KV"

    # assert the array's capability instead of inferring it - use this when the
    # plant was throttled across the whole window and never showed its ceiling
    python fleet_benchmark.py --target-csv surath.csv --label "Surath 5KV" --kwp 4.96

    # customer-ready page
    python fleet_benchmark.py --target-csv surath.csv --label "Surath 5KV" \\
        --kwp 4.96 --html reports/surath.html

Target CSV is whatever the portal exports, as long as it has date and kwh
columns (SolisCloud's Export button produces this).
"""

import argparse
import csv
import re
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

try:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DATA_DIR = Path("data")

# Only the monthly files carry daily rows; the yearly ones are month,kwh and
# would silently pollute a daily series if globbed in.
MONTHLY_FILE = re.compile(r"-\d{4}-\d{2}$")
PERIOD_SUFFIX = re.compile(r"-\d{4}(-\d{2})?$")

# A plant that never broke 1 kWh in the window is dead, ignored, or newly
# commissioned. Including it would drag the index down and understate the
# weather, which is the direction that loses arguments.
MIN_PEAK_KWH = 1.0

# Specific yield of a clear day in Sri Lanka, kWh per kWp. Used only to turn the
# fleet index into an expected-kWh line; override if you have a better figure.
DEFAULT_KWH_PER_KWP = 3.9


def read_daily_csv(path):
    """{date_str: kwh} from any CSV with date and kwh columns."""
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            keys = {k.strip().lower(): k for k in row if k}
            dk = keys.get("date") or keys.get("day")
            vk = keys.get("kwh") or keys.get("energy_kwh") or keys.get("yield")
            if not dk or not vk:
                continue
            day = (row[dk] or "").strip()[:10]
            try:
                out[day] = float(row[vk] or 0)
            except ValueError:
                continue
    return out


def load_fleet(data_dir, start, end):
    """{plant_base_name: {date: kwh}} across the window, monthly files only."""
    series = defaultdict(dict)
    for path in Path(data_dir).glob("*.csv"):
        if not MONTHLY_FILE.search(path.stem):
            continue
        base = PERIOD_SUFFIX.sub("", path.stem)
        for day, kwh in read_daily_csv(path).items():
            if start <= day <= end:
                series[base][day] = kwh
    return dict(series)


def fleet_index(series):
    """{date: percent} — mean of each plant's output as a share of its own best
    day. Plants that never produced are excluded, not counted as zero."""
    live = {p: v for p, v in series.items() if v and max(v.values()) > MIN_PEAK_KWH}
    index, counts = {}, {}
    for day in sorted({d for v in live.values() for d in v}):
        shares = [v[day] / max(v.values()) for v in live.values() if day in v]
        if shares:
            index[day] = statistics.mean(shares) * 100
            counts[day] = len(shares)
    return index, counts, len(live)


def partial_days(index, counts, today=None):
    """Days that must not be benchmarked because the data is still being written.

    Every plant has a row for today from the moment the mid-day collection runs,
    so a missing-plant count never reveals a partial day - the rows are simply
    half-filled. The only reliable signal is the calendar: today is partial until
    the day ends. A stale dataset (newest row older than today) drops nothing."""
    today = today or date.today().isoformat()
    return [d for d in index if d >= today]


def estimate_capability(index, target):
    """The plant's demonstrated output on a notional 100%-fleet-index day.

    Taken from the plant's own best ratio of actual/index rather than from an
    assumed specific yield, because the fleet index is *relative* (share of each
    plant's best day in the window) and multiplying it by an absolute kWh/kWp
    constant mixes two different baselines - that manufactures a shortfall on
    perfectly healthy plants.

    Uses a high percentile rather than the maximum, and never the maximum
    itself: cloud-edge enhancement briefly beats clear sky, and letting one such
    day set the yardstick would charge the plant a shortfall on every other day.
    Erring low is deliberate - it understates the loss, which is the safe
    direction when the number is going in front of a customer."""
    ratios = sorted(target[d] / (index[d] / 100)
                    for d in index if d in target and index[d] > 1.0)
    if not ratios:
        return None
    if len(ratios) == 1:
        return ratios[0]
    idx = min(round(0.9 * (len(ratios) - 1)), len(ratios) - 2)
    return ratios[idx]


def weather_correlation(index, target):
    """Pearson r between the fleet's weather and the plant's output.

    This is the strongest single number in a dispute. A healthy plant tracks the
    sky: bright days up, dull days down, r typically above 0.7. A plant pinned by
    an export block, a clipped inverter or a stuck limit produces the same amount
    whatever the weather, so r collapses toward zero. Near-zero r says the losses
    cannot be weather, without needing to agree on any expected value.

    None when there is too little data or no variation to correlate."""
    days = [d for d in sorted(index) if d in target]
    if len(days) < 3:
        return None
    try:
        return statistics.correlation([index[d] for d in days], [target[d] for d in days])
    except statistics.StatisticsError:   # zero variance in either series
        return None


# Below this, output is judged not to follow the weather at all — self
# calibration cannot see the plant's real ceiling and will understate the loss.
WEATHER_FOLLOWING_R = 0.4


def build_report(index, target, capability):
    """Per-day rows of fleet index, actual kWh, expected kWh and shortfall."""
    rows = []
    for day in sorted(index):
        actual = target.get(day)
        expected = (capability * index[day] / 100) if capability is not None else None
        shortfall = (expected - actual) if (expected is not None and actual is not None) else None
        rows.append({"date": day, "index": index[day], "actual": actual,
                     "expected": expected, "shortfall": shortfall})
    return rows


def main():
    ap = argparse.ArgumentParser(
        description="Benchmark one plant against the fleet's own weather signal")
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--start", help="YYYY-MM-DD (default: 30 days back)")
    ap.add_argument("--end", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--target", help="plant base name already tracked in data/")
    ap.add_argument("--target-csv", help="external daily CSV (date,kwh) e.g. a SolisCloud export")
    ap.add_argument("--label", help="display name for the target plant")
    ap.add_argument("--kwp", type=float, help="target array size, enables expected-vs-actual")
    ap.add_argument("--kwh-per-kwp", type=float, default=DEFAULT_KWH_PER_KWP,
                    help=f"clear-day specific yield (default {DEFAULT_KWH_PER_KWP})")
    ap.add_argument("--include-partial", action="store_true",
                    help="keep the most recent day even if not all plants have reported")
    ap.add_argument("--html", help="write a standalone HTML report to this path")
    args = ap.parse_args()

    end = args.end or date.today().isoformat()
    start = args.start or (date.fromisoformat(end) - timedelta(days=30)).isoformat()

    series = load_fleet(args.data_dir, start, end)
    if not series:
        raise SystemExit(f"No daily data found in {args.data_dir} for {start}..{end}")
    index, counts, n_plants = fleet_index(series)

    if not args.include_partial:
        for d in partial_days(index, counts):
            print(f"[skip] {d}: still in progress, data collected mid-day "
                  f"- use --include-partial to keep it")
            del index[d]
        if not index:
            raise SystemExit("Nothing left to benchmark after dropping partial days")

    if args.target_csv:
        target = read_daily_csv(args.target_csv)
        label = args.label or Path(args.target_csv).stem
    elif args.target:
        target = series.get(args.target)
        if target is None:
            raise SystemExit(f"Plant '{args.target}' not in {args.data_dir}. "
                             f"Available: {', '.join(sorted(series)[:5])} ...")
        label = args.label or args.target
    else:
        raise SystemExit("Give --target (a plant in data/) or --target-csv (a portal export)")

    if args.kwp:
        # Absolute mode: the operator asserts the array's clear-day capability.
        capability = args.kwp * args.kwh_per_kwp
        basis = f"{args.kwp} kWp x {args.kwh_per_kwp} kWh/kWp asserted"
    else:
        capability = estimate_capability(index, target)
        basis = "calibrated from this plant's own best days"
    if capability is None:
        raise SystemExit(f"No overlapping days between the fleet window and {label}")
    if capability <= 0:
        raise SystemExit(
            f"{label} produced nothing between {min(index)} and {max(index)}. "
            f"A dead plant cannot be benchmarked against the weather - check that it "
            f"is switched on and reporting before arguing about output.")

    rows = build_report(index, target, capability)
    render_text(rows, label, n_plants, capability, basis)

    r = weather_correlation(index, target)
    if r is not None:
        print(f"\nWeather correlation     : r = {r:+.2f}")
        if r < WEATHER_FOLLOWING_R:
            print(f"  {label} does NOT follow the weather. Its output stayed flat while the\n"
                  f"  fleet's varied, so the losses cannot be explained by cloud. A plant\n"
                  f"  clamped for the whole window never shows its real ceiling, so the\n"
                  f"  shortfall above is a FLOOR - pass --kwp to benchmark it absolutely.")
        else:
            print(f"  Output tracks the fleet's weather, so day-to-day variation here is sky, "
                  f"not fault.")

    if args.html:
        out = Path(args.html)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(rows, label, n_plants, capability, basis),
                       encoding="utf-8")
        print(f"\nWrote {out}")
    return 0


def render_text(rows, label, n_plants, capability, basis):
    print(f"\nFleet weather reference : {n_plants} producing plants")
    print(f"Target                  : {label}")
    print(f"Window                  : {rows[0]['date']} .. {rows[-1]['date']}")
    print(f"Expectation basis       : {capability:.1f} kWh on a 100% day ({basis})\n")

    cols = f"{'date':<12}{'fleet':>7}{'actual':>9}{'expected':>10}{'short':>8}"
    print(cols)
    print("-" * len(cols))

    for r in rows:
        actual = "--" if r["actual"] is None else f"{r['actual']:.1f}"
        expected = "--" if r["expected"] is None else f"{r['expected']:.1f}"
        short = "--" if r["shortfall"] is None else f"{r['shortfall']:+.1f}"
        print(f"{r['date']:<12}{r['index']:>6.0f}%{actual:>9}{expected:>10}{short:>8}")

    idx = [r["index"] for r in rows]
    acts = [r["actual"] for r in rows if r["actual"] is not None]
    print("-" * len(cols))
    print(f"\nFleet index  mean {statistics.mean(idx):.0f}%   "
          f"best {max(idx):.0f}%   worst {min(idx):.0f}%")
    if acts:
        print(f"{label}  mean {statistics.mean(acts):.1f} kWh/day   "
              f"best {max(acts):.1f}   worst {min(acts):.1f}")
    shorts = [r["shortfall"] for r in rows if r["shortfall"] is not None]
    if shorts:
        total = sum(shorts)
        print(f"Cumulative shortfall vs weather-adjusted expectation: {total:+.0f} kWh "
              f"over {len(shorts)} days ({total/len(shorts):+.1f} kWh/day)")
        print("\nA high fleet index with a large shortfall means the weather was fine "
              "and the loss is the plant's.")


def _svg(rows):
    """Inline SVG: actual vs expected bars with the fleet index as a line.
    No chart library - this has to open on a customer's phone from a file."""
    W, H, PAD_L, PAD_B, PAD_T = 900, 340, 46, 46, 16
    plot_w, plot_h = W - PAD_L - 16, H - PAD_B - PAD_T
    vals = [v for r in rows for v in (r["actual"], r["expected"]) if v is not None]
    y_max = max(vals + [1]) * 1.15
    n = max(len(rows), 1)
    step = plot_w / n

    def y(v):
        return PAD_T + plot_h - (v / y_max) * plot_h

    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
             f'aria-label="Daily yield against fleet weather index">']
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        gy = PAD_T + plot_h - frac * plot_h
        parts.append(f'<line x1="{PAD_L}" y1="{gy:.1f}" x2="{W-16}" y2="{gy:.1f}" '
                     f'stroke="var(--grid)" stroke-width="1"/>')
        parts.append(f'<text x="{PAD_L-8}" y="{gy+4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="var(--muted)">{y_max*frac:.0f}</text>')

    for i, r in enumerate(rows):
        x0 = PAD_L + i * step
        if r["expected"] is not None:
            parts.append(f'<rect x="{x0+step*0.12:.1f}" y="{y(r["expected"]):.1f}" '
                         f'width="{step*0.34:.1f}" height="{PAD_T+plot_h-y(r["expected"]):.1f}" '
                         f'fill="var(--expected)"><title>{r["date"]} expected '
                         f'{r["expected"]:.1f} kWh</title></rect>')
        if r["actual"] is not None:
            parts.append(f'<rect x="{x0+step*0.5:.1f}" y="{y(r["actual"]):.1f}" '
                         f'width="{step*0.34:.1f}" height="{PAD_T+plot_h-y(r["actual"]):.1f}" '
                         f'fill="var(--actual)"><title>{r["date"]} actual '
                         f'{r["actual"]:.1f} kWh</title></rect>')

    pts = " ".join(f'{PAD_L + i*step + step/2:.1f},{PAD_T + plot_h - (r["index"]/100)*plot_h:.1f}'
                   for i, r in enumerate(rows))
    parts.append(f'<polyline points="{pts}" fill="none" stroke="var(--fleet)" '
                 f'stroke-width="2.5" stroke-linejoin="round"/>')

    every = max(1, len(rows) // 12)
    for i, r in enumerate(rows):
        if i % every == 0:
            parts.append(f'<text x="{PAD_L + i*step + step/2:.1f}" y="{H-16}" '
                         f'text-anchor="middle" font-size="10" fill="var(--muted)">'
                         f'{r["date"][5:]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_html(rows, label, n_plants, capability, basis):
    idx = [r["index"] for r in rows]
    acts = [r["actual"] for r in rows if r["actual"] is not None]
    shorts = [r["shortfall"] for r in rows if r["shortfall"] is not None]

    tiles = [("Fleet index, mean", f"{statistics.mean(idx):.0f}%",
              f"{n_plants} plants, same sky"),
             ("Actual, mean", f"{statistics.mean(acts):.1f} kWh" if acts else "--", label)]
    if shorts:
        tiles.append(("Cumulative shortfall", f"{sum(shorts):+.0f} kWh",
                      f"over {len(shorts)} days"))

    tile_html = "".join(
        f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div>'
        f'<div class="s">{s}</div></div>' for k, v, s in tiles)

    body_rows = []
    for r in rows:
        actual = "--" if r["actual"] is None else f"{r['actual']:.1f}"
        expected = "--" if r["expected"] is None else f"{r['expected']:.1f}"
        shortfall = "--" if r["shortfall"] is None else f"{r['shortfall']:+.1f}"
        cls = "bad" if (r["shortfall"] or 0) > 0 else "ok"
        body_rows.append(
            f"<tr><td>{r['date']}</td><td>{r['index']:.0f}%</td><td>{actual}</td>"
            f"<td>{expected}</td><td class=\"{cls}\">{shortfall}</td></tr>")
    body = "".join(body_rows)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{label} - production vs fleet weather</title>
<style>
:root{{--bg:#fbfbfa;--fg:#1a1a18;--muted:#6b6b66;--card:#fff;--line:#e6e5e1;
--grid:#eeeeea;--actual:#3b7dd8;--expected:#c9c8c2;--fleet:#e08a2e;--bad:#c0392b;--ok:#2d7a3e;}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
--bg:#1a1a18;--fg:#f0efea;--muted:#9a9a92;--card:#232320;--line:#33332e;
--grid:#2c2c28;--expected:#4a4a44;}}}}
:root[data-theme="dark"]{{--bg:#1a1a18;--fg:#f0efea;--muted:#9a9a92;--card:#232320;
--line:#33332e;--grid:#2c2c28;--expected:#4a4a44;}}
body{{background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,sans-serif;margin:0;padding:28px 20px;}}
.wrap{{max-width:960px;margin:0 auto;}}
h1{{font-size:20px;margin:0 0 4px;}} .sub{{color:var(--muted);margin:0 0 22px;}}
.tiles{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:22px;}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;flex:1 1 180px;}}
.tile .k{{color:var(--muted);font-size:12px;}} .tile .v{{font-size:22px;font-weight:600;margin:2px 0;}}
.tile .s{{color:var(--muted);font-size:12px;}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:22px;}}
.legend{{display:flex;gap:18px;font-size:12px;color:var(--muted);margin-top:10px;flex-wrap:wrap;}}
.sw{{display:inline-block;width:11px;height:11px;border-radius:2px;vertical-align:-1px;margin-right:5px;}}
.scroll{{overflow-x:auto;}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;min-width:460px;}}
th,td{{text-align:right;padding:6px 10px;border-bottom:1px solid var(--line);}}
th:first-child,td:first-child{{text-align:left;}}
th{{color:var(--muted);font-weight:500;font-size:12px;}}
.bad{{color:var(--bad);}} .ok{{color:var(--ok);}}
p.note{{color:var(--muted);font-size:13px;}}
</style></head><body><div class="wrap">
<h1>{label} - production vs fleet weather</h1>
<p class="sub">{rows[0]['date']} to {rows[-1]['date']}. The fleet index is the mean output
of {n_plants} independent plants as a share of each one's own best day - an irradiance
proxy taken from real meters, not a forecast.</p>
<div class="tiles">{tile_html}</div>
<div class="card">{_svg(rows)}
<div class="legend">
<span><i class="sw" style="background:var(--actual)"></i>Actual kWh</span>
<span><i class="sw" style="background:var(--expected)"></i>Expected for the weather</span>
<span><i class="sw" style="background:var(--fleet)"></i>Fleet index (100% = best day)</span>
</div></div>
<div class="card scroll"><table>
<thead><tr><th>Date</th><th>Fleet</th><th>Actual kWh</th><th>Expected kWh</th><th>Shortfall</th></tr></thead>
<tbody>{body}</tbody></table></div>
<p class="note">A high fleet index alongside a large shortfall means the sky was fine and the
loss belongs to the plant. Expected kWh is {capability:.1f} kWh on a 100% fleet day
({basis}), scaled by the fleet index for that day.</p>
</div></body></html>"""


if __name__ == "__main__":
    sys.exit(main())
