#!/usr/bin/env python3
"""
UC11: Customer Plant ROI Analysis - OffGrid Backup Report Generator

Analyzes device data CSV and generates a comprehensive ROI report
focusing on OffGrid (battery backup) usage.

Usage:
    python analyze_plant_roi.py --input roi/abeetha-device-data-365days.csv
    python analyze_plant_roi.py --input roi/abeetha-device-data-365days.csv --output roi/report.txt
"""

import argparse
import csv
import io
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def parse_csv(input_path: Path) -> list:
    """Parse CSV file and return list of records."""
    records = []

    with open(input_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                record = {
                    'date': row['date'],
                    'timestamp': datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S'),
                    'work_state': row['work_state'],
                    'pload_w': float(row['pload_w']) if row['pload_w'] else 0,
                    'pgrid_w': float(row['pgrid_w']) if row['pgrid_w'] else 0,
                    'battery_v': float(row['battery_v']) if row['battery_v'] else 0,
                    'batt_current_a': float(row['batt_current_a']) if row['batt_current_a'] else 0,
                    'grid_v': float(row['grid_v']) if row['grid_v'] else 0,
                    'pinverter_w': float(row['pinverter_w']) if row['pinverter_w'] else 0,
                }
                records.append(record)
            except (ValueError, KeyError) as e:
                continue

    return records


def analyze_offgrid_usage(records: list) -> dict:
    """Analyze OffGrid backup usage from records."""

    # Overall stats
    total_records = len(records)
    offgrid_records = [r for r in records if r['work_state'] == 'OffGrid']
    gridtie_records = [r for r in records if r['work_state'] == 'Grid-Tie']

    # Calculate time intervals (approximately 5 minutes per record)
    time_per_record_hours = 5 / 60  # 5 minutes in hours

    # Total time
    total_hours = total_records * time_per_record_hours
    offgrid_hours = len(offgrid_records) * time_per_record_hours
    gridtie_hours = len(gridtie_records) * time_per_record_hours

    # OffGrid energy calculation (PLoad when OffGrid)
    # Energy = Power * Time = W * hours = Wh
    offgrid_energy_wh = sum(r['pload_w'] for r in offgrid_records) * time_per_record_hours
    offgrid_energy_kwh = offgrid_energy_wh / 1000

    # Peak values
    peak_pload = max((r['pload_w'] for r in offgrid_records), default=0)
    avg_pload = sum(r['pload_w'] for r in offgrid_records) / len(offgrid_records) if offgrid_records else 0

    # Monthly breakdown
    monthly_stats = defaultdict(lambda: {
        'total_records': 0,
        'offgrid_records': 0,
        'offgrid_energy_wh': 0,
        'peak_pload': 0,
    })

    for r in records:
        month = r['timestamp'].strftime('%Y-%m')
        monthly_stats[month]['total_records'] += 1

        if r['work_state'] == 'OffGrid':
            monthly_stats[month]['offgrid_records'] += 1
            monthly_stats[month]['offgrid_energy_wh'] += r['pload_w'] * time_per_record_hours
            monthly_stats[month]['peak_pload'] = max(monthly_stats[month]['peak_pload'], r['pload_w'])

    # OffGrid events (consecutive OffGrid periods)
    events = []
    current_event = None

    for r in sorted(records, key=lambda x: x['timestamp']):
        if r['work_state'] == 'OffGrid':
            if current_event is None:
                current_event = {
                    'start': r['timestamp'],
                    'end': r['timestamp'],
                    'records': 1,
                    'total_pload': r['pload_w'],
                    'peak_pload': r['pload_w'],
                }
            else:
                # Check if this is a continuation (within 10 minutes of previous)
                time_diff = (r['timestamp'] - current_event['end']).total_seconds() / 60
                if time_diff <= 10:  # Within 10 minutes
                    current_event['end'] = r['timestamp']
                    current_event['records'] += 1
                    current_event['total_pload'] += r['pload_w']
                    current_event['peak_pload'] = max(current_event['peak_pload'], r['pload_w'])
                else:
                    # New event
                    events.append(current_event)
                    current_event = {
                        'start': r['timestamp'],
                        'end': r['timestamp'],
                        'records': 1,
                        'total_pload': r['pload_w'],
                        'peak_pload': r['pload_w'],
                    }
        else:
            if current_event is not None:
                events.append(current_event)
                current_event = None

    if current_event is not None:
        events.append(current_event)

    # Event statistics
    event_durations = [(e['end'] - e['start']).total_seconds() / 3600 + time_per_record_hours for e in events]
    avg_event_duration = sum(event_durations) / len(event_durations) if event_durations else 0
    max_event_duration = max(event_durations) if event_durations else 0

    # Date range
    if records:
        start_date = min(r['timestamp'] for r in records).date()
        end_date = max(r['timestamp'] for r in records).date()
        days = (end_date - start_date).days + 1
    else:
        start_date = end_date = None
        days = 0

    return {
        'start_date': start_date,
        'end_date': end_date,
        'days': days,
        'total_records': total_records,
        'offgrid_records': len(offgrid_records),
        'gridtie_records': len(gridtie_records),
        'total_hours': total_hours,
        'offgrid_hours': offgrid_hours,
        'gridtie_hours': gridtie_hours,
        'offgrid_percent': (offgrid_hours / total_hours * 100) if total_hours > 0 else 0,
        'offgrid_energy_kwh': offgrid_energy_kwh,
        'peak_pload': peak_pload,
        'avg_pload': avg_pload,
        'event_count': len(events),
        'avg_event_duration': avg_event_duration,
        'max_event_duration': max_event_duration,
        'events': events,
        'monthly_stats': dict(monthly_stats),
    }


def generate_report(stats: dict, customer_name: str = "Unknown") -> str:
    """Generate text report from analysis stats."""

    lines = []

    # Header
    lines.append("=" * 70)
    lines.append(f"PLANT ROI ANALYSIS: {customer_name}")
    lines.append("=" * 70)
    lines.append("")

    if stats['start_date']:
        lines.append(f"Period: {stats['start_date']} to {stats['end_date']} ({stats['days']} days)")
    lines.append(f"Total data points: {stats['total_records']:,}")
    lines.append("")

    # OffGrid Summary
    lines.append("-" * 70)
    lines.append("OFFGRID BACKUP SUMMARY")
    lines.append("-" * 70)
    lines.append("")
    lines.append(f"  Total OffGrid Time:     {stats['offgrid_hours']:.1f} hours")
    lines.append(f"  Total OffGrid Energy:   {stats['offgrid_energy_kwh']:.2f} kWh")
    lines.append(f"  Number of Events:       {stats['event_count']}")
    lines.append(f"  Average Event Duration: {stats['avg_event_duration'] * 60:.1f} minutes")
    lines.append(f"  Longest Event:          {stats['max_event_duration'] * 60:.1f} minutes")
    lines.append(f"  Peak Load:              {stats['peak_pload']:.0f} W")
    lines.append(f"  Average Load:           {stats['avg_pload']:.0f} W")
    lines.append("")

    # Time Distribution
    lines.append("-" * 70)
    lines.append("TIME DISTRIBUTION")
    lines.append("-" * 70)
    lines.append("")
    lines.append(f"  Grid-Tie:  {100 - stats['offgrid_percent']:.1f}% ({stats['gridtie_hours']:.1f} hours)")
    lines.append(f"  OffGrid:   {stats['offgrid_percent']:.1f}% ({stats['offgrid_hours']:.1f} hours)")
    lines.append("")

    # Monthly Breakdown
    if stats['monthly_stats']:
        lines.append("-" * 70)
        lines.append("MONTHLY BREAKDOWN")
        lines.append("-" * 70)
        lines.append("")
        lines.append("  Month      | OffGrid Hrs | Energy (kWh) | Events | Peak Load")
        lines.append("  -----------|-------------|--------------|--------|----------")

        for month in sorted(stats['monthly_stats'].keys()):
            ms = stats['monthly_stats'][month]
            offgrid_hrs = ms['offgrid_records'] * (5/60)
            energy_kwh = ms['offgrid_energy_wh'] / 1000
            # Count events for this month
            month_events = sum(1 for e in stats['events']
                               if e['start'].strftime('%Y-%m') == month)
            peak = ms['peak_pload']

            lines.append(f"  {month}    |   {offgrid_hrs:6.1f}    |    {energy_kwh:6.2f}    |   {month_events:3d}   |  {peak:4.0f} W")

        lines.append("")

    # Value Estimation
    lines.append("-" * 70)
    lines.append("VALUE ESTIMATION")
    lines.append("-" * 70)
    lines.append("")
    # Sri Lanka electricity tariff (approximate)
    rate_per_kwh = 20  # Rs. 20/kWh estimate
    annual_savings = stats['offgrid_energy_kwh'] * rate_per_kwh

    lines.append(f"  Energy provided by battery:  {stats['offgrid_energy_kwh']:.2f} kWh")
    lines.append(f"  Est. electricity rate:       Rs. {rate_per_kwh}/kWh")
    lines.append(f"  Est. cost savings:           Rs. {annual_savings:,.0f}")
    lines.append("")

    # Recent OffGrid Events
    if stats['events']:
        lines.append("-" * 70)
        lines.append("RECENT OFFGRID EVENTS (Last 10)")
        lines.append("-" * 70)
        lines.append("")

        recent_events = sorted(stats['events'], key=lambda x: x['start'], reverse=True)[:10]
        for event in recent_events:
            duration_min = (event['end'] - event['start']).total_seconds() / 60 + 5
            avg_load = event['total_pload'] / event['records'] if event['records'] > 0 else 0
            energy_wh = event['total_pload'] * (5/60)

            lines.append(f"  {event['start'].strftime('%Y-%m-%d %H:%M')} - {event['end'].strftime('%H:%M')}")
            lines.append(f"    Duration: {duration_min:.0f} min | Avg Load: {avg_load:.0f}W | Peak: {event['peak_pload']:.0f}W | Energy: {energy_wh:.0f}Wh")
            lines.append("")

    lines.append("=" * 70)
    lines.append("Generated by UC11: Plant ROI Verification Tool")
    lines.append("=" * 70)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="UC11: Plant ROI Analysis - OffGrid Report")
    parser.add_argument("--input", required=True, help="Input CSV file from check_plant_roi.py")
    parser.add_argument("--output", help="Output report file (default: same dir as input)")
    parser.add_argument("--customer", default="Customer", help="Customer name for report header")

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[ERROR] Input file not found: {input_path}")
        sys.exit(1)

    # Parse CSV
    print(f"[OK] Reading {input_path}...")
    records = parse_csv(input_path)
    print(f"     Loaded {len(records):,} records")

    if not records:
        print("[ERROR] No valid records found in CSV")
        sys.exit(1)

    # Analyze
    print("[OK] Analyzing OffGrid usage...")
    stats = analyze_offgrid_usage(records)

    print(f"     Found {stats['offgrid_records']:,} OffGrid records ({stats['offgrid_percent']:.1f}%)")
    print(f"     Total OffGrid time: {stats['offgrid_hours']:.1f} hours")
    print(f"     Total OffGrid energy: {stats['offgrid_energy_kwh']:.2f} kWh")

    # Generate report
    print("[OK] Generating report...")
    report = generate_report(stats, args.customer)

    # Output
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_suffix('.txt')

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"[OK] Report saved to: {output_path}")
    print("")
    print(report)


if __name__ == "__main__":
    main()
