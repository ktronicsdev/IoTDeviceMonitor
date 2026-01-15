#!/usr/bin/env python3
"""
Generate device alarm notifications from ShineMonitor API data.

Each UNHANDLED alarm is sent 3 times (every 4 hours), then auto-ignored.
Tracks sent alarms in state file to prevent duplicate notifications.

Usage:
    python3 generate_device_alarms.py --alarms-dir DIR --state-file FILE --output-file FILE
"""

import os
import sys
import json
import re
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from config import CREDENTIALS_PATH


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

            # Extract alarms from "dat" field
            # API response structure: {"err":"0","dat":{"total":37,"warning":[...]}}
            # OR when no alarms: {"err":"0","dat":[]}
            if 'dat' in data:
                dat = data['dat']

                # Handle both response formats:
                # 1. Array format (no alarms): {"dat": []}
                # 2. Object format (with alarms): {"dat": {"total": N, "warning": [...]}}
                if isinstance(dat, list):
                    alarm_list = dat
                elif isinstance(dat, dict) and 'warning' in dat:
                    alarm_list = dat['warning']
                else:
                    alarm_list = []

                for alarm in alarm_list:
                    alarm['customer_label'] = customer_label
                    all_alarms.append(alarm)

        except Exception as e:
            print(f"Warning: Failed to parse {alarm_file}: {e}", file=sys.stderr)

    return all_alarms


def create_alarm_key(alarm):
    """Create unique key for alarm to track in state."""
    # Use combination of plant ID, device serial number, and warning ID
    # API returns: pid (plant ID), pn or sn (device serial), id (warning ID)
    plant_id = alarm.get('pid', alarm.get('pId', 'unknown'))
    device_sn = alarm.get('pn', alarm.get('sn', alarm.get('devId', 'unknown')))
    warning_id = alarm.get('id', alarm.get('warnId', 'unknown'))

    return f"{plant_id}:{device_sn}:{warning_id}"


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

            # Handle both old and new API field names
            # New API: plant, alias, desc, gts
            # Old API: pName, devName, warnMsg, warnTime
            plant_name = alarm.get('plant', alarm.get('pName', 'Unknown Plant'))
            device_name = alarm.get('alias', alarm.get('devName', 'Unknown Device'))
            warning_msg = alarm.get('desc', alarm.get('warnMsg', 'No message'))
            warning_time = alarm.get('gts', alarm.get('warnTime', 'Unknown time'))

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

        # UC7: Add human-readable customer, plant, and message
        state[alarm_key]['customer'] = alarm.get('customer_label', 'Unknown')
        state[alarm_key]['plant'] = alarm.get('plant', 'Unknown')
        state[alarm_key]['message'] = alarm.get('desc', alarm.get('warnMsg', 'Unknown'))

        # Auto-ignore after 3 sends
        if state[alarm_key]['send_count'] >= 3:
            state[alarm_key]['ignored'] = True

    return state


def load_customer_mapping(credentials_file):
    """Load plant-to-customer mapping from credentials.json.

    Returns dict: {normalized_customer: {'label': str, 'email': str}}
    """
    with open(credentials_file, 'r', encoding='utf-8') as f:
        creds = json.load(f)

    customer_map = {}
    for account in creds.get('accounts', []):
        label = account.get('label', '')
        email = account.get('email', '')

        if not label:
            continue

        # Normalize: "Gayan-Home-3KW" → "gayanhome3kw"
        normalized = re.sub(r'[^a-z0-9]', '', label.lower())
        customer_map[normalized] = {
            'label': label,
            'email': email
        }

    return customer_map


def create_customer_device_alarms(alarms_to_send, state, credentials_file):
    """Map alarms to customers and create customer_device_alarms.json structure.

    Args:
        alarms_to_send: List of alarm dicts that should be sent
        state: Current alarm state dict
        credentials_file: Path to credentials.json

    Returns:
        dict: {customer_label: {'email': str, 'alarms': [...]}}
    """
    customer_map = load_customer_mapping(credentials_file)
    customer_alarms = {}

    for item in alarms_to_send:
        alarm = item['alarm']
        alarm_key = item['alarm_key']

        # Use customer_label from alarm (set by parse_alarm_files from filename)
        # e.g., "Gayan-IMH-alarms.json" -> customer_label = "Gayan-IMH"
        customer_label = alarm.get('customer_label', '')
        if not customer_label:
            continue

        # Normalize to find in customer_map
        customer_norm = re.sub(r'[^a-z0-9]', '', customer_label.lower())

        # Look up customer email from credentials
        if customer_norm not in customer_map:
            continue

        customer_info = customer_map[customer_norm]
        customer_email = customer_info.get('email', '')

        if not customer_email:
            continue

        # Use original label from credentials for consistency
        customer_label = customer_info['label']

        # Initialize customer entry if not exists
        if customer_label not in customer_alarms:
            customer_alarms[customer_label] = {
                'email': customer_email,
                'alarms': []
            }

        # Get send count from state
        send_count = item['send_count'] + 1  # +1 because we're about to send

        # Add alarm to customer's list
        # API field mappings: plant=plant name, alias=device name, desc=warning message, gts=warning time
        customer_alarms[customer_label]['alarms'].append({
            'plant': alarm.get('plant', alarm.get('pName', 'Unknown Plant')),
            'device': alarm.get('alias', alarm.get('devName', 'Unknown Device')),
            'message': alarm.get('desc', alarm.get('warnMsg', 'No message')),
            'time': alarm.get('gts', alarm.get('warnTime', 'Unknown time')),
            'send_count': send_count
        })

    return customer_alarms


def get_most_recent_alarm(alarms):
    """Get the most recent alarm based on timestamp (gts field)."""
    if not alarms:
        return None

    # Sort by timestamp (gts = warning time), most recent first
    sorted_alarms = sorted(
        alarms,
        key=lambda a: a.get('gts', a.get('warnTime', '1970-01-01 00:00:00')),
        reverse=True
    )
    return sorted_alarms[0] if sorted_alarms else None


def main():
    parser = argparse.ArgumentParser(description='Process device alarms and send notifications')
    parser.add_argument('--alarms-dir', required=True, help='Directory containing alarm JSON files')
    parser.add_argument('--state-file', required=True, help='Path to alarm state JSON file')
    parser.add_argument('--output-file', required=True, help='Path to output email text file')
    parser.add_argument('--test-mode', action='store_true',
                        help='Test mode: include most recent alarm even if max sends reached (for push/manual runs)')

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

    # UC5: Test mode - if no alarms to send but alarms exist, include most recent from test customer
    if args.test_mode and len(alarms_to_send) == 0 and len(all_alarms) > 0:
        print("  [TEST MODE] No alarms passed filter, adding most recent alarm for testing...")
        # UC5 FIX: Filter to test customer (Gayan-IMH) alarms only
        test_customer_alarms = [a for a in all_alarms if a.get('customer_label', '').lower() == 'gayan-imh']
        if test_customer_alarms:
            most_recent = get_most_recent_alarm(test_customer_alarms)
            print(f"  [TEST MODE] Found {len(test_customer_alarms)} alarms for test customer (Gayan-IMH)")
        else:
            # Fallback: If test customer has no alarms, log and skip
            print("  [TEST MODE] No alarms found for test customer (Gayan-IMH)")
            most_recent = None
        if most_recent:
            alarm_key = create_alarm_key(most_recent)
            alarm_state = state.get(alarm_key, {'send_count': 0, 'ignored': False})
            # UC4: Test mode bypasses 3-send limit for Gayan-IMH testing
            # Always include the most recent alarm regardless of ignored/send_count
            alarms_to_send.append({
                'alarm': most_recent,
                'alarm_key': alarm_key,
                'send_count': alarm_state.get('send_count', 0)
            })
            print(f"  [TEST MODE] Added alarm (bypassing limits): {most_recent.get('plant', 'Unknown')} - {most_recent.get('desc', 'No message')}")

    # Format email
    print("[4/5] Formatting email...")
    email_body = format_device_alarms_email(alarms_to_send)

    # Write output
    os.makedirs(os.path.dirname(args.output_file), exist_ok=True)
    with open(args.output_file, 'w', encoding='utf-8') as f:
        f.write(email_body)
    print(f"✓ Email written to {args.output_file}")

    # Create customer-specific device alarms JSON
    if alarms_to_send:
        credentials_file = CREDENTIALS_PATH
        customer_device_alarms = create_customer_device_alarms(
            alarms_to_send,
            state,
            credentials_file
        )

        # Write customer device alarms JSON
        customer_alarms_file = Path(args.output_file).parent / 'customer_device_alarms.json'
        output_data = {'customer_device_alarms': customer_device_alarms}

        with open(customer_alarms_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2)

        print(f"✓ Customer device alarms written to {customer_alarms_file}")
        print(f"  Customers with device alarms: {len(customer_device_alarms)}")
    else:
        # No alarms - create empty customer alarms file
        customer_alarms_file = Path(args.output_file).parent / 'customer_device_alarms.json'
        with open(customer_alarms_file, 'w', encoding='utf-8') as f:
            json.dump({'customer_device_alarms': {}}, f, indent=2)
        print(f"✓ Empty customer device alarms file created")

    # Update state
    print("[5/5] Updating alarm state...")
    state = update_alarm_state(state, alarms_to_send)
    save_alarm_state(args.state_file, state)
    print(f"✓ State saved to {args.state_file}")

    # UC6: Print summary for admin visibility in logs
    if alarms_to_send:
        unique_plants = set(item['alarm'].get('plant', 'Unknown') for item in alarms_to_send)
        unique_customers = set(item['alarm'].get('customer_label', 'Unknown') for item in alarms_to_send)
        print(f"📊 Summary: {len(alarms_to_send)} alarms from {len(unique_customers)} customers affecting {len(unique_plants)} plants")

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Exit code: 0 if no alarms, 2 if alarms to send
    if len(alarms_to_send) > 0:
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
