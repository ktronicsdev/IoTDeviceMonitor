#!/usr/bin/env python3
"""
Send emails to individual customers.
Supports both weekly reports and customer-specific alerts.
Uses shared email_utils for HTML formatting and SMTP sending.
"""

import os
import sys
import json
from pathlib import Path
from email_utils import send_email_smtp, convert_plain_text_to_simple_html

def convert_text_to_html_legacy(text_body):
    """Convert plain text email body to formatted HTML (reuse from send_email.py)."""
    # Check if this is a weekly report
    is_weekly = "WEEKLY SOLAR PRODUCTION REPORT" in text_body

    # Start HTML with inline CSS
    html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {
            font-family: 'Courier New', monospace;
            background-color: #f5f5f5;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            background-color: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        .header.success {
            background: linear-gradient(135deg, #56ab2f 0%, #a8e063 100%);
        }
        .header.weekly {
            background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
        }
        .header.alert {
            background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%);
        }
        .header h1 {
            margin: 0;
            font-size: 24px;
            font-weight: bold;
        }
        .status {
            margin-top: 10px;
            font-size: 16px;
            opacity: 0.95;
        }
        .content {
            padding: 30px;
            line-height: 1.6;
        }
        .section {
            margin: 20px 0;
            padding: 20px;
            background-color: #f8f9fa;
            border-left: 4px solid #3498db;
            border-radius: 4px;
        }
        .section.red {
            border-left-color: #eb3349;
            background-color: #fff5f5;
        }
        .section.orange {
            border-left-color: #f39c12;
            background-color: #fffbf0;
        }
        .section.info {
            border-left-color: #3498db;
            background-color: #f0f8ff;
        }
        .footer {
            padding: 20px 30px;
            background-color: #f8f9fa;
            text-align: center;
            color: #666;
            font-size: 12px;
            border-top: 1px solid #dee2e6;
        }
        .metric {
            display: inline-block;
            padding: 8px 16px;
            background-color: #e9ecef;
            border-radius: 4px;
            margin: 4px;
            font-size: 14px;
        }
    </style>
</head>
<body>
"""

    # Parse the text
    lines = text_body.split('\n')

    if is_weekly:
        html += '    <div class="container">\n'
        html += '        <div class="header weekly">\n'
        html += '            <h1>📊 Weekly Solar Production Report</h1>\n'
        html += '        </div>\n'
    else:
        html += '    <div class="container">\n'
        html += '        <div class="header success">\n'
        html += '            <h1>☀️ Solar System Status</h1>\n'
        html += '        </div>\n'

    html += '        <div class="content">\n'
    html += '            <div class="section info">\n'
    html += '                <pre style="font-family: \'Courier New\', monospace; white-space: pre-wrap; margin: 0;">\n'

    for line in lines:
        html += f'{line}\n'

    html += '                </pre>\n'
    html += '            </div>\n'
    html += '        </div>\n'
    html += '        <div class="footer">\n'
    html += '            ShineMonitor IoT Device Monitoring System<br>\n'
    html += '            Automated solar panel monitoring with intelligent anomaly detection<br>\n'
    html += '            <br>\n'
    html += '            For support: ktronicssolar@gmail.com\n'
    html += '        </div>\n'
    html += '    </div>\n'
    html += '</body>\n'
    html += '</html>\n'

    return html

def send_email(to_addr, subject, body):
    """Send email using shared SMTP utility."""
    # Use shared utility (HTML will be auto-generated)
    return send_email_smtp(to_addr, subject, body)

def format_customer_alert_email(customer, alerts):
    """Format alert email for a specific customer."""
    lines = []
    lines.append("=" * 80)
    lines.append("SOLAR SYSTEM ALERT NOTIFICATION")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"Dear {customer},")
    lines.append("")
    lines.append("We detected issues with your solar installation(s):")
    lines.append("")

    # Group by severity
    red_alerts = [a for a in alerts if a.get('level') == 'RED']
    orange_alerts = [a for a in alerts if a.get('level') == 'ORANGE']

    if red_alerts:
        lines.append("🔴 CRITICAL ISSUES (Immediate attention required):")
        lines.append("")
        for alert in red_alerts:
            lines.append(f"  Plant: {alert['plant']}")
            lines.append(f"  Issue: {alert['issue']}")
            if 'normal' in alert and 'current' in alert:
                lines.append(f"  Normal production: {alert['normal']}")
                lines.append(f"  Current production: {alert['current']}")
            lines.append("")

    if orange_alerts:
        lines.append("🟠 WARNINGS (Please investigate):")
        lines.append("")
        for alert in orange_alerts:
            lines.append(f"  Plant: {alert['plant']}")
            lines.append(f"  Issue: {alert['issue']}")
            if 'normal' in alert and 'current' in alert:
                lines.append(f"  Normal production: {alert['normal']}")
                lines.append(f"  Current production: {alert['current']}")
            lines.append("")

    lines.append("-" * 80)
    lines.append("RECOMMENDED ACTIONS:")
    lines.append("-" * 80)
    lines.append("")
    lines.append("1. Check inverter status and error codes")
    lines.append("2. Verify grid connection and breaker status")
    lines.append("3. Inspect panels for shading or physical damage")
    lines.append("4. Contact maintenance team if issue persists")
    lines.append("")
    lines.append("=" * 80)
    lines.append("If this issue continues for 3 consecutive days, you will stop")
    lines.append("receiving alerts for these plants until the issue is resolved.")
    lines.append("=" * 80)
    lines.append("")
    lines.append("For support, please contact: ktronicssolar@gmail.com")

    return "\n".join(lines)


def send_customer_alerts(customer_alerts_file):
    """Send alert emails to customers based on customer_alerts.json."""
    customer_alerts_path = Path(customer_alerts_file)

    if not customer_alerts_path.exists():
        print(f"Customer alerts file not found: {customer_alerts_file}")
        return 0, 0

    try:
        with open(customer_alerts_path, 'r') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error parsing customer alerts JSON: {e}", file=sys.stderr)
        return 0, 1

    customer_alerts = data.get('customer_alerts', {})

    if not customer_alerts:
        print("No customer alerts to send")
        return 0, 0

    sent_count = 0
    failed_count = 0

    for customer, customer_data in customer_alerts.items():
        email = customer_data.get('email')
        alerts = customer_data.get('alerts', [])

        if not email:
            print(f"Skipping {customer}: No email address")
            continue

        if not alerts:
            print(f"Skipping {customer}: No alerts")
            continue

        # Format email
        subject = f"⚠️ Solar System Alert - {customer}"
        body = format_customer_alert_email(customer, alerts)

        # Send email
        if send_email(email, subject, body):
            sent_count += 1
            print(f"✓ Sent alert email to {customer} ({email})")
        else:
            failed_count += 1
            print(f"✗ Failed to send alert email to {customer} ({email})", file=sys.stderr)

    return sent_count, failed_count


def send_weekly_reports(reports_dir, report_prefix='weekly_report'):
    """Send all weekly reports to customers.

    Args:
        reports_dir: Directory containing report files
        report_prefix: Prefix for report files (default: 'weekly_report')
                       Use 'dessmonitor_weekly_report' for DessMonitor reports
    """
    reports_path = Path(reports_dir)

    if not reports_path.exists():
        print(f"Reports directory not found: {reports_dir}")
        return 0, 0

    sent_count = 0
    failed_count = 0

    # Find all report metadata files matching the prefix
    for metadata_file in reports_path.glob(f"{report_prefix}_*.json"):
        try:
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)

            customer = metadata['customer']
            email = metadata['email']
            report_file = metadata['report_file']
            platform = metadata.get('platform', 'shinemonitor')

            # Read report content
            with open(report_file, 'r') as f:
                report_content = f.read()

            # Send email with platform-specific subject
            if platform == 'dessmonitor':
                subject = f"[DessMonitor] Weekly Solar Production Report - {customer}"
            else:
                subject = f"Weekly Solar Production Report - {customer}"
            if send_email(email, subject, report_content):
                sent_count += 1
                print(f"✓ Sent weekly report to {customer} ({email})")
            else:
                failed_count += 1
                print(f"✗ Failed to send weekly report to {customer} ({email})", file=sys.stderr)

        except Exception as e:
            print(f"Error processing {metadata_file}: {e}", file=sys.stderr)
            failed_count += 1

    return sent_count, failed_count


def format_customer_device_alarm_email(customer, alarms):
    """Format device alarm email for a specific customer.

    Args:
        customer: Customer name/label
        alarms: List of alarm dicts for this customer

    Returns:
        str: Formatted email body
    """
    lines = []
    lines.append("=" * 80)
    lines.append(f"DEVICE ALARMS FOR: {customer}")
    lines.append("=" * 80)
    lines.append("")

    if not alarms:
        lines.append("All devices are operating normally.")
        lines.append("")
        lines.append("=" * 80)
        return "\n".join(lines)

    lines.append(f"⚠️  {len(alarms)} DEVICE ALARM(S) DETECTED")
    lines.append("")
    lines.append("The following devices require attention:")
    lines.append("")

    for i, alarm in enumerate(alarms, 1):
        lines.append(f"ALARM #{i}")
        lines.append(f"  Plant:    {alarm['plant']}")
        lines.append(f"  Device:   {alarm['device']}")
        lines.append(f"  Issue:    {alarm['message']}")
        lines.append(f"  Time:     {alarm['time']}")
        lines.append(f"  Status:   Send #{alarm['send_count']}/3")
        lines.append("")

    lines.append("RECOMMENDED ACTIONS:")
    lines.append("  1. Check device status in ShineMonitor portal")
    lines.append("  2. Verify physical device operation")
    lines.append("  3. Contact support if issue persists")
    lines.append("")

    if any(alarm['send_count'] >= 3 for alarm in alarms):
        lines.append("⚠️  Some alarms have been sent 3 times and will be auto-ignored.")
        lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


def send_customer_device_alarms(customer_device_alarms_file, test_customer_only=False):
    """Send device alarm emails to customers based on customer_device_alarms.json.

    Args:
        customer_device_alarms_file: Path to customer_device_alarms.json
        test_customer_only: If True, only send to Gayan-IMH (test customer)

    Returns:
        tuple: (sent_count, failed_count)
    """
    if not Path(customer_device_alarms_file).exists():
        print(f"Customer device alarms file not found: {customer_device_alarms_file}")
        return 0, 0

    with open(customer_device_alarms_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    customer_device_alarms = data.get('customer_device_alarms', {})

    if not customer_device_alarms:
        print("No customer device alarms to send")
        return 0, 0

    sent = 0
    failed = 0

    for customer, customer_data in customer_device_alarms.items():
        # Filter for test customer only if flag is set
        if test_customer_only:
            if customer.lower() != 'gayan-imh':
                print(f"Skipping {customer}: Not test customer (test-customer-only mode)")
                continue

        email = customer_data.get('email')
        alarms = customer_data.get('alarms', [])

        if not email:
            print(f"Skipping {customer}: No email address")
            continue

        if not alarms:
            print(f"Skipping {customer}: No device alarms")
            continue

        # Format email
        subject = f"🔔 Device Alarm Alert - {customer}"
        body = format_customer_device_alarm_email(customer, alarms)

        # Send email
        try:
            if send_email(email, subject, body):
                print(f"✓ Sent device alarm email to {customer} ({email})")
                sent += 1
            else:
                print(f"✗ Failed to send device alarm email to {customer} ({email})")
                failed += 1
        except Exception as e:
            print(f"✗ Failed to send device alarm email to {customer} ({email}): {e}")
            failed += 1

    return sent, failed


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Send customer emails (alerts or weekly reports)')
    parser.add_argument('--reports-dir', help='Directory containing weekly reports (for weekly reports)')
    parser.add_argument('--report-prefix', default='weekly_report',
                        help='Prefix for report files (default: weekly_report, use dessmonitor_weekly_report for DessMonitor)')
    parser.add_argument('--customer-alerts', help='Path to customer_alerts.json (for alert emails)')
    parser.add_argument('--customer-device-alarms', help='Path to customer_device_alarms.json file')
    parser.add_argument('--test-customer-only', action='store_true',
                        help='Only send to test customer (Gayan-IMH) - for testing on push/manual runs')

    args = parser.parse_args()

    total_sent = 0
    total_failed = 0

    # Send customer alerts if provided
    if args.customer_alerts:
        print("=" * 80)
        print("SENDING CUSTOMER ALERT EMAILS")
        print("=" * 80)
        sent, failed = send_customer_alerts(args.customer_alerts)
        total_sent += sent
        total_failed += failed
        print(f"\nCustomer alert emails sent: {sent}, failed: {failed}")

    # Send weekly reports if provided
    if args.reports_dir:
        print("=" * 80)
        print(f"SENDING WEEKLY REPORTS (prefix: {args.report_prefix})")
        print("=" * 80)
        sent, failed = send_weekly_reports(args.reports_dir, args.report_prefix)
        total_sent += sent
        total_failed += failed
        print(f"\nWeekly reports sent: {sent}, failed: {failed}")

    # Send customer device alarms if provided
    if args.customer_device_alarms:
        print("\n" + "=" * 80)
        print("SENDING CUSTOMER DEVICE ALARM EMAILS")
        print("=" * 80)
        sent, failed = send_customer_device_alarms(
            args.customer_device_alarms,
            args.test_customer_only
        )
        total_sent += sent
        total_failed += failed
        print(f"Customer device alarms sent: {sent}, failed: {failed}")

    if not args.customer_alerts and not args.reports_dir and not args.customer_device_alarms:
        print("Error: Must specify --customer-alerts, --reports-dir, or --customer-device-alarms", file=sys.stderr)
        return 1

    print("=" * 80)
    print(f"TOTAL: {total_sent} sent, {total_failed} failed")
    print("=" * 80)

    return 0 if total_failed == 0 else 2

if __name__ == '__main__':
    sys.exit(main())
