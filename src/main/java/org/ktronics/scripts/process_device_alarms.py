#!/usr/bin/env python3
"""
Process device alarms from ShineMonitor API and send admin notifications.

Each UNHANDLED alarm is sent 3 times (every 4 hours), then auto-ignored.
Tracks sent alarms in state file to prevent duplicate notifications.

Usage:
    python3 process_device_alarms.py --alarms-dir DIR --state-file FILE --output-file FILE
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict


def load_alarm_state(state_file):
    """Load alarm notification state from JSON file."""
    if not os.path.exists(state_file):
        return {}

    with open(state_file, 'r') as f:
        return json.load(f)


def save_alarm_state(state_file, state):
    """Save alarm notification state to JSON file."""
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)


def parse_alarm_files(alarms_dir):
    """Parse all alarm JSON files and extract UNHANDLED alarms."""
    alarms_dir = Path(alarms_dir)
    all_alarms = []

    for alarm_file in alarms_dir.glob('*-alarms.json'):
        try:
            with open(alarm_file, 'r') as f:
                data = json.load(f)

            # Extract customer label from filename (e.g., "customer-alarms.json" -> "customer")
            customer_label = alarm_file.stem.replace('-alarms', '')

            # Extract alarms from "dat" array
            if 'dat' in data and isinstance(data['dat'], list):
                for alarm in data['dat']:
                    alarm['customer_label'] = customer_label
                    all_alarms.append(alarm)

        except Exception as e:
            print(f"Warning: Failed to parse {alarm_file}: {e}", file=sys.stderr)

    return all_alarms


def create_alarm_key(alarm):
    """Create unique key for alarm to track in state."""
    # Use combination of plant ID, device ID, and warning ID
    plant_id = alarm.get('pId', 'unknown')
    device_id = alarm.get('devId', 'unknown')
    warning_id = alarm.get('warnId', 'unknown')

    return f"{plant_id}:{device_id}:{warning_id}"


def filter_alarms_to_send(alarms, state, max_sends=3):
    """
    Filter alarms that should be sent based on state.

    Each alarm is sent max_sends times (default 3), then auto-ignored.
    Tracks send count and last sent time in state.
    """
    now = datetime.now()
    alarms_to_send = []

    for alarm in alarms:
        alarm_key = create_alarm_key(alarm)

        # Get alarm state (default: never sent)
        alarm_state = state.get(alarm_key, {
            'send_count': 0,
            'last_sent': None,
            'first_seen': now.isoformat(),
            'ignored': False
        })

        # Skip if already ignored
        if alarm_state.get('ignored', False):
            continue

        # Skip if already sent max times
        if alarm_state['send_count'] >= max_sends:
            alarm_state['ignored'] = True
            state[alarm_key] = alarm_state
            continue

        # Check if enough time has passed since last send (4 hours)
        if alarm_state['last_sent']:
            last_sent = datetime.fromisoformat(alarm_state['last_sent'])
            hours_since_last = (now - last_sent).total_seconds() / 3600

            if hours_since_last < 4:
                # Too soon to send again
                continue

        # This alarm should be sent
        alarms_to_send.append({
            'alarm': alarm,
            'alarm_key': alarm_key,
            'send_count': alarm_state['send_count']
        })

    return alarms_to_send


def format_device_alarms_email(alarms_to_send):
    """Format device alarms into admin email text."""

    if not alarms_to_send:
        return """
================================================================================
║                                                                              ║
║                        DEVICE ALARMS - ALL CLEAR                             ║
║                                                                              ║
================================================================================

Report Time: {timestamp}

┌─ STATUS ─────────────────────────────────────────────────────────────────────┐
│
│ ✅ ALL SYSTEMS OPERATIONAL
│
│ No UNHANDLED device alarms detected across all monitored systems.
│
└──────────────────────────────────────────────────────────────────────────────┘

This is an automated device alarm monitoring report.

For support: ktronicssolar@gmail.com

""".format(timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC'))

    # Group alarms by customer
    by_customer = defaultdict(list)
    for item in alarms_to_send:
        alarm = item['alarm']
        customer = alarm.get('customer_label', 'Unknown')
        by_customer[customer].append(item)

    email_body = f"""
================================================================================
║                                                                              ║
║                    🔔 DEVICE ALARMS DETECTED                                 ║
║                                                                              ║
================================================================================

Report Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

┌─ SUMMARY ────────────────────────────────────────────────────────────────────┐
│
│ Total UNHANDLED Alarms: {len(alarms_to_send)}
│ Affected Customers:     {len(by_customer)}
│
└──────────────────────────────────────────────────────────────────────────────┘

┌─ ALARM DETAILS ──────────────────────────────────────────────────────────────┐
│
"""

    for customer, customer_alarms in sorted(by_customer.items()):
        email_body += f"│ 🔴 CUSTOMER: {customer}\n│\n"

        for item in customer_alarms:
            alarm = item['alarm']
            send_count = item['send_count'] + 1  # +1 because we're about to send

            plant_name = alarm.get('pName', 'Unknown Plant')
            device_name = alarm.get('devName', 'Unknown Device')
            warning_msg = alarm.get('warnMsg', 'No message')
            warning_time = alarm.get('warnTime', 'Unknown time')

            email_body += f"│    Plant:   {plant_name}\n"
            email_body += f"│    Device:  {device_name}\n"
            email_body += f"│    Message: {warning_msg}\n"
            email_body += f"│    Time:    {warning_time}\n"
            email_body += f"│    Status:  Send #{send_count}/3\n"
            email_body += "│\n"

    email_body += """└──────────────────────────────────────────────────────────────────────────────┘

┌─ NOTES ──────────────────────────────────────────────────────────────────────┐
│
│ • Each alarm is sent 3 times (every 4 hours), then auto-ignored
│ • UNHANDLED alarms require manual resolution in ShineMonitor portal
│ • Visit https://web.shinemonitor.com to manage alarms
│
└──────────────────────────────────────────────────────────────────────────────┘

This is an automated device alarm monitoring report.

For support: ktronicssolar@gmail.com

"""

    return email_body


def update_alarm_state(state, alarms_to_send):
    """Update state after sending alarms."""
    now = datetime.now()

    for item in alarms_to_send:
        alarm_key = item['alarm_key']
        alarm = item['alarm']

        # Update send count and last sent time
        if alarm_key not in state:
            state[alarm_key] = {
                'send_count': 0,
                'first_seen': now.isoformat(),
                'ignored': False
            }

        state[alarm_key]['send_count'] += 1
        state[alarm_key]['last_sent'] = now.isoformat()

        # Auto-ignore after 3 sends
        if state[alarm_key]['send_count'] >= 3:
            state[alarm_key]['ignored'] = True

    return state


def main():
    parser = argparse.ArgumentParser(description='Process device alarms and send notifications')
    parser.add_argument('--alarms-dir', required=True, help='Directory containing alarm JSON files')
    parser.add_argument('--state-file', required=True, help='Path to alarm state JSON file')
    parser.add_argument('--output-file', required=True, help='Path to output email text file')

    args = parser.parse_args()

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("🔔 PROCESSING DEVICE ALARMS")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Load state
    print(f"[1/5] Loading alarm state from {args.state_file}...")
    state = load_alarm_state(args.state_file)
    print(f"✓ Loaded state ({len(state)} tracked alarms)")

    # Parse alarm files
    print(f"[2/5] Parsing alarm files from {args.alarms_dir}...")
    all_alarms = parse_alarm_files(args.alarms_dir)
    print(f"✓ Found {len(all_alarms)} UNHANDLED alarms")

    # Filter alarms to send
    print("[3/5] Filtering alarms (max 3 sends per alarm, 4-hour interval)...")
    alarms_to_send = filter_alarms_to_send(all_alarms, state)
    print(f"✓ {len(alarms_to_send)} alarms ready to send")

    # Format email
    print("[4/5] Formatting email...")
    email_body = format_device_alarms_email(alarms_to_send)

    # Write output
    os.makedirs(os.path.dirname(args.output_file), exist_ok=True)
    with open(args.output_file, 'w', encoding='utf-8') as f:
        f.write(email_body)
    print(f"✓ Email written to {args.output_file}")

    # Update state
    print("[5/5] Updating alarm state...")
    state = update_alarm_state(state, alarms_to_send)
    save_alarm_state(args.state_file, state)
    print(f"✓ State saved to {args.state_file}")

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Exit code: 0 if no alarms, 2 if alarms to send
    if len(alarms_to_send) > 0:
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
