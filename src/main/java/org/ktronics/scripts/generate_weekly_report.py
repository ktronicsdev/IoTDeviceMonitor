#!/usr/bin/env python3
"""
Generate weekly progress reports for individual customers.
Sends personalized emails with daily, monthly, and yearly summaries.
"""

import os
import sys
import json
import csv
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from config import CREDENTIALS_PATH

def load_credentials(creds_file, platform=None):
    """Load customer credentials and email addresses.

    Args:
        creds_file: Path to credentials JSON file
        platform: Optional platform ('shinemonitor' or 'dessmonitor')
                  For 'dessmonitor', looks for 'dessmonitor_accounts' key
                  Otherwise, uses 'accounts' key (ShineMonitor default)

    Returns:
        List of account dictionaries with label, email, etc.
    """
    with open(creds_file, 'r') as f:
        data = json.load(f)

    if platform == 'dessmonitor':
        # DessMonitor credentials format: {"dessmonitor_accounts": [...]}
        return data.get('dessmonitor_accounts', [])
    else:
        # ShineMonitor credentials format: {"accounts": [...]}
        return data.get('accounts', [])

def get_customer_plants(data_dir, customer_label, platform=None):
    """Find unique plant base names for a customer (without year/month suffixes).

    Args:
        data_dir: Directory containing CSV files
        customer_label: Customer label to match
        platform: Optional platform filter ('shinemonitor' or 'dessmonitor')
                  If 'dessmonitor', only matches files starting with 'dessmonitor-'
                  If 'shinemonitor' or None, excludes files starting with 'dessmonitor-'
    """
    import re
    data_path = Path(data_dir)
    plant_names = set()

    # Normalize customer label for matching
    normalized_label = customer_label.lower().replace(' ', '-').replace('_', '-')

    # Pattern to match: plantname-YYYY-MM.csv or plantname-YYYY.csv
    # We want to extract just the plantname part
    for csv_file in data_path.glob('*.csv'):
        filename = csv_file.stem.lower()

        # Platform filtering
        if platform == 'dessmonitor':
            # Only match DessMonitor files (prefixed with 'dessmonitor-')
            if not filename.startswith('dessmonitor-'):
                continue
        else:
            # ShineMonitor (default): exclude DessMonitor files
            if filename.startswith('dessmonitor-'):
                continue

        if normalized_label in filename:
            # Remove year/month suffixes: -YYYY-MM or -YYYY
            base_name = re.sub(r'-\d{4}(-\d{2})?$', '', csv_file.stem)
            plant_names.add(base_name)

    return sorted(list(plant_names))

def parse_csv_data(csv_file):
    """Parse CSV file and extract production data."""
    data = {'daily': [], 'monthly': [], 'yearly': []}

    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if 'date' in row and 'energy_kwh' in row:
                data['daily'].append({
                    'date': row['date'],
                    'energy': float(row['energy_kwh'])
                })
            elif 'month' in row and 'total_kwh' in row:
                data['monthly'].append({
                    'month': row['month'],
                    'total': float(row['total_kwh'])
                })
            elif 'year' in row and 'total_kwh' in row:
                data['yearly'].append({
                    'year': row['year'],
                    'total': float(row['total_kwh'])
                })

    return data

def get_weekly_summary(plant_base_names, data_dir):
    """Generate weekly summary for customer's plants."""
    today = datetime.now()
    week_ago = today - timedelta(days=7)

    # Calculate previous month/year for comparisons
    first_of_month = today.replace(day=1)
    prev_month = first_of_month - timedelta(days=1)
    prev_year = today.year - 1

    summary = {
        'plants': [],
        'total_weekly': 0,
        'total_monthly': 0,
        'total_yearly': 0,
        'total_prev_monthly': 0,
        'total_prev_yearly': 0,
        'week_start': week_ago.strftime('%Y-%m-%d'),
        'week_end': today.strftime('%Y-%m-%d'),
        'current_month': today.strftime('%Y-%m'),
        'prev_month': prev_month.strftime('%Y-%m'),
        'current_year': str(today.year),
        'prev_year': str(prev_year)
    }

    for plant_name in plant_base_names:
        # Read monthly CSV for recent data (current month)
        monthly_file = data_dir / f"{plant_name}-{today.strftime('%Y-%m')}.csv"
        yearly_file = data_dir / f"{plant_name}-{today.year}.csv"
        prev_monthly_file = data_dir / f"{plant_name}-{prev_month.strftime('%Y-%m')}.csv"
        prev_yearly_file = data_dir / f"{plant_name}-{prev_year}.csv"

        plant_data = {
            'name': plant_name,
            'weekly_kwh': 0,
            'monthly_kwh': 0,
            'yearly_kwh': 0,
            'prev_monthly_kwh': 0,
            'prev_yearly_kwh': 0,
            'daily_average': 0
        }

        # Get weekly data from monthly CSV
        if monthly_file.exists():
            try:
                with open(monthly_file, 'r') as f:
                    reader = csv.DictReader(f)
                    daily_values = []
                    for row in reader:
                        row_date = datetime.strptime(row['date'], '%Y-%m-%d').date()
                        if week_ago.date() <= row_date <= today.date():
                            energy = float(row['kwh'])
                            plant_data['weekly_kwh'] += energy
                            daily_values.append(energy)

                    if daily_values:
                        plant_data['daily_average'] = sum(daily_values) / len(daily_values)

                    # Get monthly total - need to re-read file from beginning
                    f.seek(0)
                    reader2 = csv.DictReader(f)
                    plant_data['monthly_kwh'] = sum(float(row['kwh']) for row in reader2)
            except Exception as e:
                print(f"Error reading {monthly_file}: {e}", file=sys.stderr)

        # Get yearly data
        if yearly_file.exists():
            try:
                with open(yearly_file, 'r') as f:
                    reader = csv.DictReader(f)
                    plant_data['yearly_kwh'] = sum(float(row['kwh']) for row in reader)
            except Exception as e:
                print(f"Error reading {yearly_file}: {e}", file=sys.stderr)

        # Get previous month total
        if prev_monthly_file.exists():
            try:
                with open(prev_monthly_file, 'r') as f:
                    reader = csv.DictReader(f)
                    plant_data['prev_monthly_kwh'] = sum(float(row['kwh']) for row in reader)
            except Exception as e:
                print(f"Error reading {prev_monthly_file}: {e}", file=sys.stderr)

        # Get previous year total
        if prev_yearly_file.exists():
            try:
                with open(prev_yearly_file, 'r') as f:
                    reader = csv.DictReader(f)
                    plant_data['prev_yearly_kwh'] = sum(float(row['kwh']) for row in reader)
            except Exception as e:
                print(f"Error reading {prev_yearly_file}: {e}", file=sys.stderr)

        summary['plants'].append(plant_data)
        summary['total_weekly'] += plant_data['weekly_kwh']
        summary['total_monthly'] += plant_data['monthly_kwh']
        summary['total_yearly'] += plant_data['yearly_kwh']
        summary['total_prev_monthly'] += plant_data['prev_monthly_kwh']
        summary['total_prev_yearly'] += plant_data['prev_yearly_kwh']

    return summary

def format_weekly_email(customer_label, summary, alerts_for_customer):
    """Format weekly report email for customer."""

    email_body = f"""
================================================================================
║                                                                              ║
║                     WEEKLY SOLAR PRODUCTION REPORT                           ║
║                                                                              ║
║                          Customer: {customer_label:<35} ║
║                                                                              ║
================================================================================

Report Period: {summary['week_start']} to {summary['week_end']}

┌─ PRODUCTION SUMMARY ────────────────────────────────────────────────────────┐
│
│ Weekly Total:   {summary['total_weekly']:>10.2f} kWh
│
│ This Month ({summary['current_month']}):  {summary['total_monthly']:>10.2f} kWh
│ Last Month ({summary['prev_month']}):  {summary['total_prev_monthly']:>10.2f} kWh
│ Month Change:     {summary['total_monthly'] - summary['total_prev_monthly']:>+10.2f} kWh
│
│ This Year ({summary['current_year']}):      {summary['total_yearly']:>10.2f} kWh
│ Last Year ({summary['prev_year']}):      {summary['total_prev_yearly']:>10.2f} kWh
│ Year Change:      {summary['total_yearly'] - summary['total_prev_yearly']:>+10.2f} kWh
│
│ Number of Plants: {len(summary['plants'])}
│
└──────────────────────────────────────────────────────────────────────────────┘

"""

    # Add alerts section if any
    if alerts_for_customer:
        email_body += """
┌─ ⚠️  ALERTS & NOTIFICATIONS ─────────────────────────────────────────────────┐
│
"""
        for alert in alerts_for_customer:
            email_body += f"""│ 🔴 {alert['plant']}
│   • Status: {alert['status']}
│   • Duration: {alert['days']} days
│   • Action Required: {alert['action']}
│
"""
        email_body += "└──────────────────────────────────────────────────────────────────────────────┘\n\n"
    else:
        email_body += """
┌─ ✓ ALL SYSTEMS OPERATIONAL ─────────────────────────────────────────────────┐
│
│ All your solar plants are performing normally with no alerts detected.
│
└──────────────────────────────────────────────────────────────────────────────┘

"""

    email_body += """
Thank you for choosing our solar monitoring service!

For questions or support, please contact: ktronicssolar@gmail.com

"""

    return email_body

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Generate weekly customer reports')
    parser.add_argument('--credentials', default=str(CREDENTIALS_PATH), help='Path to credentials.json')
    parser.add_argument('--data-dir', default='data', help='Directory containing CSV files')
    parser.add_argument('--alerts-state', default='state/customer_alerts_state.json', help='Customer alerts state file')
    parser.add_argument('--output-dir', default='reports', help='Output directory for reports')
    parser.add_argument('--platform', default=None, choices=['shinemonitor', 'dessmonitor'],
                        help='Filter by platform (shinemonitor or dessmonitor). If not specified, processes ShineMonitor data.')

    args = parser.parse_args()

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    # Determine platform for logging
    platform_name = args.platform.upper() if args.platform else 'ShineMonitor'
    print(f"Generating {platform_name} weekly reports")

    # Load customer data
    customers = load_credentials(args.credentials, args.platform)
    data_dir = Path(args.data_dir)

    # Load alerts state if exists
    alerts_by_customer = defaultdict(list)
    if os.path.exists(args.alerts_state):
        with open(args.alerts_state, 'r') as f:
            alerts_by_customer = json.load(f)

    # Generate reports for customers with email addresses
    reports_generated = 0

    for customer in customers:
        customer_label = customer['label']
        customer_email = customer.get('email')

        if not customer_email:
            print(f"Skipping {customer_label} - no email address", file=sys.stderr)
            continue

        print(f"Generating report for {customer_label} ({customer_email})")

        # Find customer's plants (filtered by platform)
        plants = get_customer_plants(data_dir, customer_label, args.platform)

        if not plants:
            print(f"  No plants found for {customer_label}", file=sys.stderr)
            continue

        print(f"  Found {len(plants)} plant(s)")

        # Generate weekly summary
        summary = get_weekly_summary(plants, data_dir)

        # Get alerts for this customer
        customer_alerts = alerts_by_customer.get(customer_label, [])

        # Format email
        email_content = format_weekly_email(customer_label, summary, customer_alerts)

        # Determine report filename prefix based on platform
        prefix = 'dessmonitor_weekly_report' if args.platform == 'dessmonitor' else 'weekly_report'

        # Save report to file
        report_file = Path(args.output_dir) / f"{prefix}_{customer_label.replace(' ', '_').lower()}.txt"
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(email_content)

        # Also save email metadata
        metadata_file = Path(args.output_dir) / f"{prefix}_{customer_label.replace(' ', '_').lower()}.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump({
                'customer': customer_label,
                'email': customer_email,
                'report_file': str(report_file),
                'generated_at': datetime.now().isoformat(),
                'summary': summary,
                'platform': args.platform or 'shinemonitor'
            }, f, indent=2)

        reports_generated += 1
        print(f"  Report saved to {report_file}")

    print(f"\nGenerated {reports_generated} {platform_name} weekly reports")
    return 0

if __name__ == '__main__':
    sys.exit(main())
