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

def load_credentials(creds_file):
    """Load customer credentials and email addresses."""
    with open(creds_file, 'r') as f:
        data = json.load(f)
    return data['accounts']

def get_customer_plants(data_dir, customer_label):
    """Find all CSV files belonging to a customer based on label matching."""
    plants = []
    data_path = Path(data_dir)

    # Normalize customer label for matching
    normalized_label = customer_label.lower().replace(' ', '-').replace('_', '-')

    for csv_file in data_path.glob('*.csv'):
        filename = csv_file.stem.lower()
        if normalized_label in filename:
            plants.append(csv_file)

    return plants

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

def get_weekly_summary(plants, data_dir):
    """Generate weekly summary for customer's plants."""
    today = datetime.now()
    week_ago = today - timedelta(days=7)

    summary = {
        'plants': [],
        'total_weekly': 0,
        'total_monthly': 0,
        'total_yearly': 0,
        'week_start': week_ago.strftime('%Y-%m-%d'),
        'week_end': today.strftime('%Y-%m-%d')
    }

    for plant_file in plants:
        plant_name = plant_file.stem

        # Read monthly CSV for recent data
        monthly_file = data_dir / f"{plant_name}-{today.strftime('%Y-%m')}.csv"
        yearly_file = data_dir / f"{plant_name}-{today.year}.csv"

        plant_data = {
            'name': plant_name,
            'weekly_kwh': 0,
            'monthly_kwh': 0,
            'yearly_kwh': 0,
            'daily_average': 0
        }

        # Get weekly data from monthly CSV
        if monthly_file.exists():
            try:
                with open(monthly_file, 'r') as f:
                    reader = csv.DictReader(f)
                    daily_values = []
                    for row in reader:
                        row_date = datetime.strptime(row['date'], '%Y-%m-%d')
                        if week_ago <= row_date <= today:
                            energy = float(row['kwh'])
                            plant_data['weekly_kwh'] += energy
                            daily_values.append(energy)

                    if daily_values:
                        plant_data['daily_average'] = sum(daily_values) / len(daily_values)

                    # Get monthly total
                    f.seek(0)
                    next(reader)  # Skip header again
                    plant_data['monthly_kwh'] = sum(float(row['kwh']) for row in reader)
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

        summary['plants'].append(plant_data)
        summary['total_weekly'] += plant_data['weekly_kwh']
        summary['total_monthly'] += plant_data['monthly_kwh']
        summary['total_yearly'] += plant_data['yearly_kwh']

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
│ Weekly Total:   {summary['total_weekly']:.2f} kWh
│ Monthly Total:  {summary['total_monthly']:.2f} kWh
│ Yearly Total:   {summary['total_yearly']:.2f} kWh
│
│ Number of Plants: {len(summary['plants'])}
│
└──────────────────────────────────────────────────────────────────────────────┘

┌─ PLANT-BY-PLANT BREAKDOWN ──────────────────────────────────────────────────┐
│
"""

    for plant in summary['plants']:
        email_body += f"""│ Plant: {plant['name']}
│   • Weekly Production:  {plant['weekly_kwh']:.2f} kWh
│   • Daily Average:      {plant['daily_average']:.2f} kWh/day
│   • Monthly Total:      {plant['monthly_kwh']:.2f} kWh
│   • Yearly Total:       {plant['yearly_kwh']:.2f} kWh
│
"""

    email_body += "└──────────────────────────────────────────────────────────────────────────────┘\n\n"

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
    parser.add_argument('--credentials', required=True, help='Path to credentials.json')
    parser.add_argument('--data-dir', default='data', help='Directory containing CSV files')
    parser.add_argument('--alerts-state', default='state/customer_alerts_state.json', help='Customer alerts state file')
    parser.add_argument('--output-dir', default='reports', help='Output directory for reports')

    args = parser.parse_args()

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    # Load customer data
    customers = load_credentials(args.credentials)
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

        # Find customer's plants
        plants = get_customer_plants(data_dir, customer_label)

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

        # Save report to file
        report_file = Path(args.output_dir) / f"weekly_report_{customer_label.replace(' ', '_').lower()}.txt"
        with open(report_file, 'w') as f:
            f.write(email_content)

        # Also save email metadata
        metadata_file = Path(args.output_dir) / f"weekly_report_{customer_label.replace(' ', '_').lower()}.json"
        with open(metadata_file, 'w') as f:
            json.dump({
                'customer': customer_label,
                'email': customer_email,
                'report_file': str(report_file),
                'generated_at': datetime.now().isoformat(),
                'summary': summary
            }, f, indent=2)

        reports_generated += 1
        print(f"  Report saved to {report_file}")

    print(f"\nGenerated {reports_generated} weekly reports")
    return 0

if __name__ == '__main__':
    sys.exit(main())
