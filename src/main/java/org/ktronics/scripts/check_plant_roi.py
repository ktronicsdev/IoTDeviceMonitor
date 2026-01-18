#!/usr/bin/env python3
"""
UC11: Customer Plant ROI Verification - OffGrid Backup Analysis

Fetches detailed device data from ShineMonitor API for analyzing OffGrid backup usage.
Tracks when PLoad > 0 and work_state = OffGrid (battery backup mode).

Usage:
    python check_plant_roi.py --customer abeetha --days 365
    python check_plant_roi.py --customer abeetha --days 30  # Last 30 days
    python check_plant_roi.py --customer abeetha --discover-api  # API discovery mode
"""

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# API Configuration
API_URL = "https://web.shinemonitor.com/public/"

# Column indices for queryDeviceDataOneDayPaging response
COL_ID = 0
COL_TIMESTAMP = 1
COL_BATTERY_VOLTAGE = 2
COL_BATT_CURRENT = 3
COL_CHARGER_CURRENT = 4
COL_PLOAD = 5
COL_PGRID = 6
COL_WORK_STATE = 7
COL_GRID_VOLTAGE = 8
COL_PINVERTER = 9


def sha1hex(text: str) -> str:
    """Calculate SHA1 hash of text."""
    return hashlib.sha1(text.encode()).hexdigest()


def salt_ms() -> str:
    """Generate timestamp in milliseconds."""
    return str(int(time.time() * 1000))


def api_request(url: str, method: str = "GET", data: dict = None) -> dict:
    """Make HTTP request and return JSON response."""
    try:
        if method == "POST" and data:
            encoded_data = urllib.parse.urlencode(data).encode()
            req = urllib.request.Request(url, data=encoded_data)
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        else:
            req = urllib.request.Request(url)

        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        return {"err": -1, "msg": str(e)}


def authenticate(username: str, password: str, company_key: str) -> tuple:
    """Authenticate with ShineMonitor API."""
    salt = salt_ms()
    pw_sha1 = sha1hex(password)

    # Try authEmail (GET method) first
    tail = f"&action=authEmail&usr={urllib.parse.quote(username)}&company-key={company_key}"
    sign = sha1hex(f"{salt}{pw_sha1}{tail}")
    auth_url = f"{API_URL}?sign={sign}&salt={salt}&action=authEmail&usr={urllib.parse.quote(username)}&company-key={company_key}"

    response = api_request(auth_url)

    if response.get("err") == 0:
        token = response.get("dat", {}).get("token")
        secret = response.get("dat", {}).get("secret")
        if token and secret:
            print(f"[OK] Authenticated for {username}")
            return token, secret

    # Fallback to authSource (POST method)
    salt = salt_ms()
    sign = sha1hex(f"{username}{pw_sha1}{salt}")

    post_data = {
        "usr": username,
        "company-key": company_key,
        "pwd": pw_sha1,
        "sign": sign,
        "salt": salt
    }

    response = api_request(f"{API_URL}?action=authSource", method="POST", data=post_data)

    if response.get("err") == 0:
        token = response.get("dat", {}).get("token")
        secret = response.get("dat", {}).get("secret")
        if token and secret:
            print(f"[OK] Authenticated for {username}")
            return token, secret

    print(f"[ERROR] Authentication failed for {username}")
    return None, None


def api_call(token: str, secret: str, action: str, extra_params: str = "") -> dict:
    """Make authenticated API call."""
    salt = salt_ms()

    if extra_params:
        action_string = f"&action={action}&{extra_params}"
    else:
        action_string = f"&action={action}"

    sign = sha1hex(f"{salt}{secret}{token}{action_string}")
    url = f"{API_URL}?sign={sign}&salt={salt}&token={token}{action_string}"

    return api_request(url)


def get_plants(token: str, secret: str) -> list:
    """Get list of plants for authenticated user."""
    response = api_call(token, secret, "queryPlants")

    if response.get("err") == 0:
        dat = response.get("dat", {})
        if isinstance(dat, dict):
            return dat.get("plant", [])
        return dat

    return []


def get_collectors(token: str, secret: str, plant_id: str) -> list:
    """Get collectors (devices) for a plant."""
    response = api_call(token, secret, "webQueryCollectorsEs", f"plantid={plant_id}")

    if response.get("err") == 0:
        dat = response.get("dat", {})
        if isinstance(dat, dict):
            return dat.get("collector", [])
        return dat

    return []


def get_devices(token: str, secret: str, pn: str) -> list:
    """Get actual devices from a collector using webQueryDeviceEs."""
    response = api_call(token, secret, "webQueryDeviceEs", f"pn={pn}&devcode=2429&devaddr=1&sn={pn}")

    if response.get("err") == 0:
        return response.get("dat", {}).get("device", [])

    return []


def get_device_data_for_day(token: str, secret: str, pn: str, devcode: str, devaddr: str, sn: str, date_str: str) -> list:
    """Fetch all device data for a single day using pagination.

    Returns list of data rows, each containing:
    [id, timestamp, battery_v, batt_current, charger_current, pload, pgrid, work_state, grid_v, pinverter, ...]
    """
    all_rows = []
    page = 0
    pagesize = 200

    while True:
        params = f"pn={pn}&devcode={devcode}&devaddr={devaddr}&sn={sn}&date={date_str}&page={page}&pagesize={pagesize}&i18n=en_US"
        response = api_call(token, secret, "queryDeviceDataOneDayPaging", params)

        if response.get("err") != 0:
            break

        dat = response.get("dat", {})
        rows = dat.get("row", [])
        total = dat.get("total", 0)

        for row in rows:
            fields = row.get("field", [])
            if fields:
                all_rows.append(fields)

        # Check if we have more pages
        if len(all_rows) >= total or len(rows) == 0:
            break

        page += 1
        time.sleep(0.1)  # Rate limiting

    return all_rows


def fetch_historical_data(token: str, secret: str, pn: str, devcode: str, devaddr: str, sn: str,
                          days: int, output_path: Path, start_date_str: str = None, end_date_str: str = None) -> int:
    """Fetch historical device data for the specified number of days or date range.

    Returns: Number of records fetched
    """
    if start_date_str and end_date_str:
        # Use explicit date range
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        days = (end_date - start_date).days + 1
    else:
        # Use days from today
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

    print(f"\nFetching data from {start_date} to {end_date} ({days} days)")
    print(f"Output: {output_path}")

    # Create output directory
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_records = 0
    offgrid_records = 0

    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['date', 'timestamp', 'work_state', 'pload_w', 'pgrid_w',
                         'battery_v', 'batt_current_a', 'grid_v', 'pinverter_w'])

        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime("%Y-%m-%d")

            # Progress indicator
            progress = (current_date - start_date).days / days * 100
            print(f"\r  Fetching {date_str}... ({progress:.0f}%)", end="", flush=True)

            rows = get_device_data_for_day(token, secret, pn, devcode, devaddr, sn, date_str)

            for fields in rows:
                if len(fields) >= 10:
                    timestamp = fields[COL_TIMESTAMP]
                    work_state = fields[COL_WORK_STATE]
                    pload = fields[COL_PLOAD]
                    pgrid = fields[COL_PGRID]
                    battery_v = fields[COL_BATTERY_VOLTAGE]
                    batt_current = fields[COL_BATT_CURRENT]
                    grid_v = fields[COL_GRID_VOLTAGE]
                    pinverter = fields[COL_PINVERTER]

                    writer.writerow([date_str, timestamp, work_state, pload, pgrid,
                                     battery_v, batt_current, grid_v, pinverter])

                    total_records += 1
                    if work_state == "OffGrid":
                        offgrid_records += 1

            current_date += timedelta(days=1)
            time.sleep(0.2)  # Rate limiting between days

    print(f"\n\n[OK] Data fetch complete!")
    print(f"  Total records: {total_records:,}")
    print(f"  OffGrid records: {offgrid_records:,}")
    print(f"  Output file: {output_path}")

    return total_records


def load_credentials(customer_label: str) -> tuple:
    """Load credentials for a customer from credentials.json."""
    script_dir = Path(__file__).parent
    creds_path = script_dir.parent / "config" / "credentials.json"

    if not creds_path.exists():
        print(f"Credentials file not found: {creds_path}")
        return None, None, None

    with open(creds_path, 'r', encoding='utf-8') as f:
        creds = json.load(f)

    company_key = creds.get("company_key")

    for account in creds.get("accounts", []):
        if account.get("label", "").lower() == customer_label.lower():
            return account.get("username"), account.get("password"), company_key

    print(f"Customer not found: {customer_label}")
    return None, None, None


def get_device_info(token: str, secret: str, plant_id: str) -> dict:
    """Get device information (pn, devcode, devaddr, sn) for a plant."""
    collectors = get_collectors(token, secret, plant_id)

    if not collectors:
        return None

    # Get first collector
    collector = collectors[0] if isinstance(collectors[0], dict) else {}
    pn = collector.get("pn", "")

    # Get actual device info
    devices = get_devices(token, secret, pn)

    if devices:
        device = devices[0]
        return {
            "pn": pn,
            "devcode": str(device.get("devcode", "2429")),
            "devaddr": str(device.get("devaddr", "1")),
            "sn": device.get("sn", pn),
            "name": device.get("devalias", "Unknown Device")
        }

    return {
        "pn": pn,
        "devcode": "2429",
        "devaddr": "1",
        "sn": pn,
        "name": "Unknown Device"
    }


def main():
    parser = argparse.ArgumentParser(description="UC11: Plant ROI Analysis - OffGrid Backup")
    parser.add_argument("--customer", required=True, help="Customer label (e.g., 'Abeetha')")
    parser.add_argument("--days", type=int, default=365, help="Number of days to analyze (default: 365)")
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD) for custom date range")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD) for custom date range")
    parser.add_argument("--discover-api", action="store_true", help="Run API discovery mode")
    parser.add_argument("--output", help="Output CSV file path")

    args = parser.parse_args()

    # Load credentials
    username, password, company_key = load_credentials(args.customer)
    if not username:
        print(f"Failed to load credentials for customer: {args.customer}")
        sys.exit(1)

    print(f"Customer: {args.customer}")
    print(f"Username: {username}")

    # Authenticate
    token, secret = authenticate(username, password, company_key)
    if not token:
        print("Authentication failed")
        sys.exit(1)

    # Get plants
    plants = get_plants(token, secret)
    if not plants:
        print("No plants found")
        sys.exit(1)

    plant = plants[0]
    plant_id = plant.get("pid")
    plant_name = plant.get("name", "Unknown")

    print(f"Plant: {plant_name} (ID: {plant_id})")

    # Get device info
    device_info = get_device_info(token, secret, plant_id)
    if not device_info:
        print("Failed to get device info")
        sys.exit(1)

    print(f"Device: {device_info['name']}")
    print(f"  pn={device_info['pn']}, devcode={device_info['devcode']}, devaddr={device_info['devaddr']}")

    if args.discover_api:
        # API Discovery mode - just show one day sample
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"\nFetching sample data for {yesterday}...")

        rows = get_device_data_for_day(token, secret, device_info['pn'], device_info['devcode'],
                                       device_info['devaddr'], device_info['sn'], yesterday)

        print(f"Found {len(rows)} records")
        if rows:
            print("\nSample data (first 5 rows):")
            print("Timestamp | Work State | PLoad | PGrid | Battery V | Batt Current")
            print("-" * 70)
            for row in rows[:5]:
                if len(row) >= 10:
                    print(f"{row[1]} | {row[7]} | {row[5]}W | {row[6]}W | {row[2]}V | {row[3]}A")

            # Count OffGrid records
            offgrid_count = sum(1 for r in rows if len(r) > 7 and r[7] == "OffGrid")
            print(f"\nOffGrid records: {offgrid_count} / {len(rows)}")
    else:
        # Normal mode - fetch historical data
        output_path = args.output
        if not output_path:
            customer_slug = args.customer.lower().replace(" ", "-")
            if args.start_date and args.end_date:
                # Use year from start date for filename
                year = args.start_date[:4]
                output_path = Path(f"roi/{customer_slug}-device-data-{year}.csv")
            else:
                output_path = Path(f"roi/{customer_slug}-device-data-{args.days}days.csv")

        output_path = Path(output_path)

        fetch_historical_data(
            token, secret,
            device_info['pn'], device_info['devcode'], device_info['devaddr'], device_info['sn'],
            args.days, output_path,
            start_date_str=args.start_date,
            end_date_str=args.end_date
        )

        print(f"\nNext step: Run analyze_plant_roi.py to generate ROI report")
        print(f"  python analyze_plant_roi.py --input {output_path}")


if __name__ == "__main__":
    main()
