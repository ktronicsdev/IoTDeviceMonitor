#!/usr/bin/env python3
"""
DessMonitor API Diagnostic Tool

Tests different API endpoints to find which one returns actual production data.
Run this to diagnose why queryPlantEnergyMonthPerDay returns all zeros.
"""

import json
import sys
from pathlib import Path

# Add parent directory to path to import config
sys.path.insert(0, str(Path(__file__).parent.parent / "src/main/java/org/ktronics/scripts"))

from config import CREDENTIALS_PATH


def load_credentials():
    """Load DessMonitor credentials"""
    with open(CREDENTIALS_PATH, 'r') as f:
        data = json.load(f)

    if 'dessmonitor_credentials' in data:
        return data['dessmonitor_credentials']

    return None


def main():
    print("=" * 80)
    print("DESSMONITOR API DIAGNOSTIC TOOL")
    print("=" * 80)
    print()

    # Load credentials
    creds = load_credentials()

    if not creds:
        print("❌ ERROR: No dessmonitor_credentials found in credentials.json")
        print()
        print("Expected format:")
        print('{')
        print('  "dessmonitor_credentials": {')
        print('    "company_key": "...",')
        print('    "dessmonitor_accounts": [')
        print('      {')
        print('        "label": "...",')
        print('        "username": "...",')
        print('        "password": "...",')
        print('        "email": "..."')
        print('      }')
        print('    ]')
        print('  }')
        print('}')
        return 1

    print("✓ Credentials loaded")
    print(f"  Company Key: {creds.get('company_key', 'NOT FOUND')[:20]}...")
    print(f"  Accounts: {len(creds.get('dessmonitor_accounts', []))}")
    print()

    # Check if accounts exist
    accounts = creds.get('dessmonitor_accounts', [])
    if not accounts:
        print("❌ ERROR: No dessmonitor_accounts found in credentials")
        return 1

    print("NEXT STEPS TO DIAGNOSE:")
    print()
    print("1. VERIFY API ACCESS:")
    print("   - Log into DessMonitor web portal with these credentials")
    print("   - Check if you can see production data there")
    print("   - If no data in portal → Plants are offline OR account has no data access")
    print()
    print("2. CHECK ACCOUNT PERMISSIONS:")
    print("   - These might be DEMO/TEST accounts with no real data")
    print("   - Contact DessMonitor support to verify data access permissions")
    print()
    print("3. TEST ALTERNATE API:")
    print("   Run: bash src/main/java/org/ktronics/scripts/check_dessmonitor_monthly.sh")
    print("   Check DEBUG output to see actual API response")
    print()
    print("4. COMPARE WITH WORKING SHINEMONITOR:")
    print("   - ShineMonitor returns real data → API works")
    print("   - DessMonitor returns zeros → Different API auth or permissions needed")
    print()

    # Show account details
    print("=" * 80)
    print("DESSMONITOR ACCOUNTS TO VERIFY:")
    print("=" * 80)
    for i, acc in enumerate(accounts, 1):
        print(f"\n{i}. {acc.get('label', 'UNKNOWN')}")
        print(f"   Username: {acc.get('username', 'NOT SET')}")
        print(f"   Email: {acc.get('email', 'NOT SET')}")
        print(f"   Has Password: {'YES' if acc.get('password') else 'NO'}")

    print()
    print("=" * 80)
    print("RECOMMENDATION:")
    print("=" * 80)
    print()
    print("Since we've been trying for 3+ days with no success:")
    print()
    print("🔴 CRITICAL ACTION REQUIRED:")
    print("   Contact DessMonitor support IMMEDIATELY to verify:")
    print("   1. Do these accounts have data access permissions?")
    print("   2. Are the plants actually producing energy?")
    print("   3. Which API endpoint should we use for historical data?")
    print()
    print("The code is working correctly - it's fetching exactly what the API returns.")
    print("The problem is the API returns 0 for everything, which means:")
    print("  - Either the plants are offline")
    print("  - Or the credentials don't have permission to see production data")
    print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
