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

import connectivity
import exclusions
from config import CREDENTIALS_PATH

# Reuse functions from generate_weekly_report
sys.path.insert(0, str(Path(__file__).parent))
from check_anomaly import load_daily_series, utc_today
from generate_weekly_report import load_credentials, get_customer_plants, get_weekly_summary


def generate_admin_summary(credentials_file, data_dir, output_file, platform=None,
                           stale_days=connectivity.DEFAULT_STALE_DAYS):
    """Generate comprehensive admin summary of all customers.

    Args:
        credentials_file: Path to credentials JSON file
        data_dir: Directory containing CSV files
        output_file: Output file path
        platform: Optional platform filter ('shinemonitor', 'dessmonitor', or None for default)
        stale_days: Days with no new reading before a plant counts as link-down
    """

    data_path = Path(data_dir)
    accounts = load_credentials(credentials_file, platform=platform)

    # The three numbers the owner is actually asked for — how many systems are
    # monitored, how many have lost their data link, how many we have dropped.
    # Taken from the CSV fleet, the same source the production checks read, so
    # the figure quoted on the website is the figure the platform can defend.
    fleet = connectivity.classify_fleet(
        connectivity.select_platform(load_daily_series(data_path), platform),
        utc_today(), stale_days=stale_days,
    )
    for line in exclusions.log_lines():
        print(line)
    for line in fleet.counts_lines():
        print(line)

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

        # Excluded customers are off the platform: no report, no totals, no count.
        if exclusions.is_customer_excluded(customer_label):
            print(f"EXCLUDED {customer_label}: {exclusions.reason_for_customer(customer_label)}")
            continue

        # Get customer's plants (filter by platform)
        plant_names = get_customer_plants(data_path, customer_label, platform=platform)

        # ...and an individually excluded plant drops out of their totals too.
        plant_names, dropped = exclusions.split_plants(plant_names)
        for item in dropped:
            print(f"EXCLUDED {item['plant_key']}: {item['reason']}")

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

┌─ PLATFORM COVERAGE ─────────────────────────────────────────────────────────┐
│
│ Plants reporting:        {fleet.counts['reporting']:>3}   sending data normally
│ Plants with a dead link: {fleet.counts['link_down']:>3}   no data for {stale_days}+ days — CONNECTIVITY, not a fault
│ Plants excluded:         {fleet.counts['excluded']:>3}   deliberately off the platform
│ ─────────────────────────────
│ Plants monitored:        {fleet.counts['monitored']:>3}   reporting + dead link
│
│ A dead link means the customer's WiFi or monitoring dongle is offline. Those
│ solar systems are very probably fine — they are NOT failed systems, and their
│ production cannot be judged until the link is restored.
│
└──────────────────────────────────────────────────────────────────────────────┘

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

    email_body += "└──────────────────────────────────────────────────────────────────────────────┘\n\n"

    if fleet.link_down:
        email_body += """┌─ 📡 PLANTS WITH A DEAD LINK ────────────────────────────────────────────────┐
│
│ No data is reaching us from these plants. That is a monitoring problem, not
│ a production fault — check the customer's WiFi and dongle before anything.
│
"""
        for status in fleet.link_down:
            last_seen = status.last_nonzero_date or 'never'
            email_body += f"│ • {status.plant_key}\n"
            email_body += f"│   Last reading: {last_seen} ({status.days_stale} days ago)\n"
            email_body += f"│   Symptom: {status.meaning}\n"
        email_body += "│\n└──────────────────────────────────────────────────────────────────────────────┘\n\n"

    if fleet.excluded:
        email_body += """┌─ EXCLUDED PLANTS (OFF THE PLATFORM BY DECISION) ────────────────────────────┐
│
│ Not monitored, not counted, no reports. Listed so nobody wonders where they
│ went. Remove an entry from excluded_plants.json to bring a plant back.
│
"""
        for item in fleet.excluded:
            since = f" (since {item['since']})" if item.get('since') else ""
            email_body += f"│ • {item['plant_key']}{since}\n"
            email_body += f"│   Reason: {item['reason']}\n"
        email_body += "│\n└──────────────────────────────────────────────────────────────────────────────┘\n\n"

    email_body += """┌─ STATISTICS ────────────────────────────────────────────────────────────────┐
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
    print(f"Coverage: {fleet.counts['reporting']} reporting, "
          f"{fleet.counts['link_down']} dead link, "
          f"{fleet.counts['excluded']} excluded")

    return {
        'customers_count': len(customer_summaries),
        'total_plants': total_plants,
        'total_weekly': total_weekly,
        'total_monthly': total_monthly,
        'total_yearly': total_yearly,
        'plants_reporting': fleet.counts['reporting'],
        'plants_link_down': fleet.counts['link_down'],
        'plants_excluded': fleet.counts['excluded'],
        'plants_monitored': fleet.counts['monitored'],
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Generate admin summary of all customers')
    parser.add_argument('--credentials', default=str(CREDENTIALS_PATH), help='Path to credentials.json')
    parser.add_argument('--data-dir', default='data', help='Directory containing CSV files')
    parser.add_argument('--output', default='reports/admin_summary.txt', help='Output file path')
    parser.add_argument('--platform', default=None, choices=['shinemonitor', 'dessmonitor'],
                        help='Filter by platform (shinemonitor or dessmonitor). If not specified, defaults to ShineMonitor.')
    parser.add_argument('--stale-days', type=int, default=connectivity.DEFAULT_STALE_DAYS,
                        help='Days with no new reading before a plant counts as link-down (default: 3)')

    args = parser.parse_args()

    result = generate_admin_summary(args.credentials, args.data_dir, args.output,
                                    platform=args.platform, stale_days=args.stale_days)

    return 0


if __name__ == '__main__':
    sys.exit(main())
