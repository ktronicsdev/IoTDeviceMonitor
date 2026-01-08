#!/usr/bin/env python3
"""
Generate admin summary email with all customers' weekly data.
Aggregates all customer reports into a single summary for admin review.
"""

import os
import sys
import json
import csv
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
import re

from config import CREDENTIALS_PATH

# Reuse functions from generate_weekly_report
sys.path.insert(0, str(Path(__file__).parent))
from generate_weekly_report import load_credentials, get_customer_plants, get_weekly_summary


def generate_admin_summary(credentials_file, data_dir, output_file):
    """Generate comprehensive admin summary of all customers."""

    data_path = Path(data_dir)
    accounts = load_credentials(credentials_file)

    today = datetime.now()
    week_ago = today - timedelta(days=7)

    # Aggregate data
    customer_summaries = []
    total_weekly = 0
    total_monthly = 0
    total_yearly = 0
    total_prev_monthly = 0
    total_prev_yearly = 0
    total_plants = 0

    for customer in accounts:
        customer_label = customer['label']

        # Get customer's plants
        plant_names = get_customer_plants(data_path, customer_label)

        if not plant_names:
            continue

        # Get summary for this customer
        summary = get_weekly_summary(plant_names, data_path)

        customer_summaries.append({
            'customer': customer_label,
            'email': customer.get('email', 'N/A'),
            'plants_count': len(summary['plants']),
            'weekly': summary['total_weekly'],
            'monthly': summary['total_monthly'],
            'yearly': summary['total_yearly'],
            'prev_monthly': summary['total_prev_monthly'],
            'prev_yearly': summary['total_prev_yearly']
        })

        total_weekly += summary['total_weekly']
        total_monthly += summary['total_monthly']
        total_yearly += summary['total_yearly']
        total_prev_monthly += summary['total_prev_monthly']
        total_prev_yearly += summary['total_prev_yearly']
        total_plants += len(summary['plants'])

    # Format admin email
    email_body = f"""
================================================================================
║                                                                              ║
║                   ADMIN WEEKLY SUMMARY - ALL CUSTOMERS                       ║
║                                                                              ║
================================================================================

Report Period: {week_ago.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')}
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

┌─ OVERALL SUMMARY ───────────────────────────────────────────────────────────┐
│
│ Total Customers: {len(customer_summaries)}
│ Total Plants:    {total_plants}
│
│ PRODUCTION TOTALS (ALL CUSTOMERS):
│   • Weekly Total:          {total_weekly:>10.2f} kWh
│
│   • This Month Total:      {total_monthly:>10.2f} kWh
│   • Previous Month Total:  {total_prev_monthly:>10.2f} kWh
│   • Month-over-Month:      {total_monthly - total_prev_monthly:>+10.2f} kWh
│
│   • This Year Total:       {total_yearly:>10.2f} kWh
│   • Previous Year Total:   {total_prev_yearly:>10.2f} kWh
│   • Year-over-Year:        {total_yearly - total_prev_yearly:>+10.2f} kWh
│
└──────────────────────────────────────────────────────────────────────────────┘

┌─ CUSTOMER BREAKDOWN ────────────────────────────────────────────────────────┐
│
"""

    # Sort customers by weekly production (descending)
    customer_summaries.sort(key=lambda x: x['weekly'], reverse=True)

    for i, customer_data in enumerate(customer_summaries, 1):
        month_change = customer_data['monthly'] - customer_data['prev_monthly']
        year_change = customer_data['yearly'] - customer_data['prev_yearly']

        email_body += f"""│ {i}. {customer_data['customer']:<40}
│    Email: {customer_data['email']:<45}
│    Plants: {customer_data['plants_count']:<3}
│    Weekly:      {customer_data['weekly']:>8.2f} kWh
│    This Month:  {customer_data['monthly']:>8.2f} kWh  |  Last Month: {customer_data['prev_monthly']:>8.2f} kWh  |  Change: {month_change:>+8.2f} kWh
│    This Year:   {customer_data['yearly']:>8.2f} kWh  |  Last Year:  {customer_data['prev_yearly']:>8.2f} kWh  |  Change: {year_change:>+8.2f} kWh
│
"""

    email_body += """└──────────────────────────────────────────────────────────────────────────────┘

┌─ STATISTICS ────────────────────────────────────────────────────────────────┐
│
"""

    # Calculate averages
    if customer_summaries:
        avg_weekly = total_weekly / len(customer_summaries)
        avg_monthly = total_monthly / len(customer_summaries)
        avg_yearly = total_yearly / len(customer_summaries)
        avg_plants = total_plants / len(customer_summaries)

        email_body += f"""│ Average per Customer:
│   • Weekly Production:  {avg_weekly:>8.2f} kWh
│   • Monthly Production: {avg_monthly:>8.2f} kWh
│   • Yearly Production:  {avg_yearly:>8.2f} kWh
│   • Plants per Customer: {avg_plants:>5.1f}
│
"""

    email_body += """└──────────────────────────────────────────────────────────────────────────────┘

This is an automated weekly summary for administrative monitoring.

For support: ktronicssolar@gmail.com

"""

    # Write to file
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(email_body)

    print(f"Admin summary generated: {output_file}")
    print(f"Customers: {len(customer_summaries)}, Total Plants: {total_plants}")
    print(f"Total Weekly: {total_weekly:.2f} kWh")

    return {
        'customers_count': len(customer_summaries),
        'total_plants': total_plants,
        'total_weekly': total_weekly,
        'total_monthly': total_monthly,
        'total_yearly': total_yearly
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Generate admin summary of all customers')
    parser.add_argument('--credentials', default=str(CREDENTIALS_PATH), help='Path to credentials.json')
    parser.add_argument('--data-dir', default='data', help='Directory containing CSV files')
    parser.add_argument('--output', default='reports/admin_summary.txt', help='Output file path')

    args = parser.parse_args()

    result = generate_admin_summary(args.credentials, args.data_dir, args.output)

    return 0


if __name__ == '__main__':
    sys.exit(main())
