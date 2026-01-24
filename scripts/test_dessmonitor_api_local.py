#!/usr/bin/env python3
"""
Local DessMonitor API Test Script

Tests various API endpoints to find the correct collector IDs (pn values)
needed for device-level energy queries.

Usage:
    python scripts/test_dessmonitor_api_local.py
"""

import json
import hashlib
import time
import urllib.parse
import sys
from pathlib import Path

# Try to use requests, fall back to urllib
try:
    import requests
    USE_REQUESTS = True
except ImportError:
    import urllib.request
    USE_REQUESTS = False
    print("Note: 'requests' not installed, using urllib")

# Configuration
API_URL = "https://api.dessmonitor.com/public/"

# Load credentials - DessMonitor has its own credentials file
CREDENTIALS_PATH = Path(__file__).parent.parent / "src/main/java/org/ktronics/config/dessmonitor_credentials.json"


def sha1hex(text: str) -> str:
    """Calculate SHA1 hash of text"""
    return hashlib.sha1(text.encode()).hexdigest()


def salt_ms() -> str:
    """Generate timestamp in milliseconds"""
    return str(int(time.time() * 1000))


def api_call(url: str, method: str = "GET", data: dict = None) -> dict:
    """Make API call and return JSON response"""
    if USE_REQUESTS:
        if method == "POST":
            resp = requests.post(url, data=data, timeout=30)
        else:
            resp = requests.get(url, timeout=30)
        return resp.json()
    else:
        if method == "POST" and data:
            data_bytes = urllib.parse.urlencode(data).encode()
            req = urllib.request.Request(url, data=data_bytes)
        else:
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())


def authenticate(username: str, password: str, company_key: str) -> tuple:
    """Authenticate with DessMonitor API"""
    salt = salt_ms()
    pw_sha1 = sha1hex(password)

    # Try authEmail first (GET method)
    sign_input = f"{salt}{pw_sha1}&action=authEmail&usr={username}&company-key={company_key}&source=1"
    sign = sha1hex(sign_input)

    url = f"{API_URL}?sign={sign}&salt={salt}&action=authEmail&usr={urllib.parse.quote(username)}&company-key={company_key}&source=1"

    try:
        resp = api_call(url)
        if resp.get("err") == 0:
            return resp.get("dat", {}).get("token"), resp.get("dat", {}).get("secret")
    except Exception as e:
        print(f"  authEmail failed: {e}")

    # Fallback to authSource (POST method)
    sign_input = f"{salt}{pw_sha1}&action=authSource&usr={username}&company-key={company_key}&source=1"
    sign = sha1hex(sign_input)

    url = f"{API_URL}?action=authSource"
    data = {
        "usr": username,
        "company-key": company_key,
        "pwd": pw_sha1,
        "sign": sign,
        "salt": salt,
        "source": "1"
    }

    try:
        resp = api_call(url, method="POST", data=data)
        if resp.get("err") == 0:
            return resp.get("dat", {}).get("token"), resp.get("dat", {}).get("secret")
    except Exception as e:
        print(f"  authSource failed: {e}")

    return None, None


def signed_api_call(action: str, params: str, token: str, secret: str) -> dict:
    """Make authenticated API call"""
    salt = salt_ms()

    if params:
        action_string = f"&action={action}&source=1&{params}"
    else:
        action_string = f"&action={action}&source=1"

    sign = sha1hex(f"{salt}{secret}{token}{action_string}")
    url = f"{API_URL}?sign={sign}&salt={salt}&token={token}{action_string}"

    return api_call(url)


def test_account(account: dict, company_key: str):
    """Test API endpoints for a single account"""
    label = account.get("label", account.get("username", "unknown"))
    username = account.get("username", "")
    password = account.get("password", "")

    print(f"\n{'='*60}")
    print(f"ACCOUNT: {label}")
    print(f"{'='*60}")

    # Authenticate
    print("\n1. AUTHENTICATION")
    token, secret = authenticate(username, password, company_key)
    if not token:
        print("   FAILED - Could not authenticate")
        return
    print(f"   SUCCESS - Token: {token[:20]}...")

    # Test queryPlants
    print("\n2. queryPlants (get plant IDs)")
    resp = signed_api_call("queryPlants", "page=0&pagesize=50", token, secret)
    print(f"   Response: err={resp.get('err')}, desc={resp.get('desc', 'N/A')}")

    plants = []
    dat = resp.get("dat", {})
    if isinstance(dat, list):
        plants = dat
    elif isinstance(dat, dict):
        plants = dat.get("plant", dat.get("plants", []))

    print(f"   Found {len(plants)} plants:")
    for p in plants:
        pid = p.get("pid", p.get("id", "?"))
        pname = p.get("name", p.get("pname", "?"))
        print(f"   - pid={pid}, name={pname}")

    # Test webQueryCollectorsEs (this might have collector IDs)
    print("\n3. webQueryCollectorsEs (get collector IDs)")
    resp = signed_api_call("webQueryCollectorsEs", "page=0&pagesize=50", token, secret)
    print(f"   Response: err={resp.get('err')}, desc={resp.get('desc', 'N/A')}")

    collectors = []
    dat = resp.get("dat", {})
    if isinstance(dat, list):
        collectors = dat
    elif isinstance(dat, dict):
        collectors = dat.get("collector", dat.get("collectors", dat.get("pn", [])))
        if not collectors and "pn" not in dat:
            # Try to get any list in dat
            for key, val in dat.items():
                if isinstance(val, list):
                    collectors = val
                    print(f"   Found list in dat.{key}")
                    break

    if collectors:
        print(f"   Found {len(collectors)} collectors:")
        for c in collectors[:5]:  # Show first 5
            print(f"   - {json.dumps(c)[:200]}")
    else:
        print(f"   Raw dat: {json.dumps(dat)[:500]}")

    # Test webQueryDeviceEs with different parameters
    print("\n4. webQueryDeviceEs (get devices)")
    for plant in plants[:2]:  # Test first 2 plants
        pid = plant.get("pid", plant.get("id", ""))
        if not pid:
            continue

        print(f"\n   Testing plant pid={pid}:")

        # Try various parameter combinations
        param_sets = [
            f"pn={pid}",
            f"pn={pid}&page=0&pagesize=50",
            f"sn={pid}",
            f"plantid={pid}",
        ]

        for params in param_sets:
            resp = signed_api_call("webQueryDeviceEs", params, token, secret)
            err = resp.get("err")
            desc = resp.get("desc", "N/A")

            if err == 0:
                dat = resp.get("dat", {})
                devices = []
                if isinstance(dat, list):
                    devices = dat
                elif isinstance(dat, dict):
                    devices = dat.get("device", dat.get("devices", []))
                print(f"   - {params}: SUCCESS! Found {len(devices)} devices")
                for d in devices[:3]:
                    print(f"     {json.dumps(d)[:200]}")
                break
            else:
                print(f"   - {params}: err={err} ({desc})")

    # Test queryPlantDeviceList (might return devices with pn)
    print("\n5. queryPlantDeviceList (alternative device query)")
    for plant in plants[:2]:
        pid = plant.get("pid", plant.get("id", ""))
        if not pid:
            continue

        resp = signed_api_call("queryPlantDeviceList", f"plantid={pid}&page=0&pagesize=50", token, secret)
        err = resp.get("err")
        print(f"   plantid={pid}: err={err}, desc={resp.get('desc', 'N/A')}")
        if err == 0:
            print(f"   dat: {json.dumps(resp.get('dat', {}))[:500]}")

    # Test queryDeviceListPaged
    print("\n6. queryDeviceListPaged")
    resp = signed_api_call("queryDeviceListPaged", "page=0&pagesize=50", token, secret)
    err = resp.get("err")
    print(f"   err={err}, desc={resp.get('desc', 'N/A')}")
    if err == 0:
        dat = resp.get("dat", {})
        print(f"   dat keys: {list(dat.keys()) if isinstance(dat, dict) else 'list'}")
        print(f"   dat: {json.dumps(dat)[:500]}")

    # Test querySPDeviceKeyParameterMonthPerDay with correct device info
    print("\n7. querySPDeviceKeyParameterMonthPerDay (ENERGY DATA)")

    # Get device info via sn={pid}
    for plant in plants[:2]:
        pid = plant.get("pid", plant.get("id", ""))
        if not pid:
            continue

        # Query devices using sn parameter (this works!)
        resp = signed_api_call("webQueryDeviceEs", f"sn={pid}", token, secret)
        if resp.get("err") != 0:
            continue

        dat = resp.get("dat", {})
        devices = dat if isinstance(dat, list) else dat.get("device", [])

        for device in devices[:2]:
            pn = device.get("pn", "")
            sn = device.get("sn", "")
            devcode = device.get("devcode", "")
            devaddr = device.get("devaddr", "")

            if not all([pn, sn, devcode, devaddr]):
                continue

            print(f"\n   Device: pn={pn}, sn={sn}, devcode={devcode}, devaddr={devaddr}")

            # Test energy query with ENERGY_TODAY_FROM_GRID
            params = f"pn={pn}&sn={sn}&devcode={devcode}&devaddr={devaddr}&parameter=ENERGY_TODAY_FROM_GRID&date=2026-01&i18n=en_US&chartStatus=false"
            resp = signed_api_call("querySPDeviceKeyParameterMonthPerDay", params, token, secret)
            err = resp.get("err")
            print(f"   ENERGY_TODAY_FROM_GRID: err={err}, desc={resp.get('desc', 'N/A')}")
            print(f"   Full response: {json.dumps(resp)[:800]}")

            if err == 0:
                dat = resp.get("dat", {})
                perday = dat.get("perday", [])
                print(f"   Found {len(perday)} days of data")
                # Show first 3 days with non-zero values
                for day in perday[:5]:
                    val = day.get("val", "0")
                    ts = day.get("ts", "?")
                    print(f"     {ts}: {val} kWh")

            # Also try ENERGY_TODAY parameter
            params = f"pn={pn}&sn={sn}&devcode={devcode}&devaddr={devaddr}&parameter=ENERGY_TODAY&date=2026-01&i18n=en_US&chartStatus=false"
            resp = signed_api_call("querySPDeviceKeyParameterMonthPerDay", params, token, secret)
            err = resp.get("err")
            print(f"   ENERGY_TODAY: err={err}, desc={resp.get('desc', 'N/A')}")

            if err == 0:
                dat = resp.get("dat", {})
                perday = dat.get("perday", [])
                print(f"   Found {len(perday)} days of data")
                for day in perday[:5]:
                    val = day.get("val", "0")
                    ts = day.get("ts", "?")
                    print(f"     {ts}: {val} kWh")

            # Try different parameter names to find actual production data
            test_params = [
                "ENERGY_TODAY_FROM_GRID",
                "ENERGY_TODAY",
                "PV_ENERGY_TODAY",
                "GRID_ENERGY_OUT",
                "ENERGY_TOTAL",
                "PV_POWER",
                "TOTAL_ENERGY",
                "E_TODAY",
                "TODAY_ENERGY",
            ]

            print(f"\n   Testing different parameters for 2026-01:")
            for param_name in test_params:
                params = f"pn={pn}&sn={sn}&devcode={devcode}&devaddr={devaddr}&parameter={param_name}&date=2026-01&i18n=en_US&chartStatus=false"
                resp = signed_api_call("querySPDeviceKeyParameterMonthPerDay", params, token, secret)
                err = resp.get("err")
                dat = resp.get("dat", {})

                # Data could be in 'option' or 'perday'
                data_list = dat.get("option", dat.get("perday", []))
                non_zero = [d for d in data_list if float(d.get("val", "0")) > 0]

                if err == 0 and data_list:
                    print(f"   - {param_name}: {len(data_list)} days, {len(non_zero)} non-zero")
                    if non_zero:
                        for day in non_zero[:2]:
                            print(f"       {day.get('gts', day.get('ts', '?'))}: {day.get('val', '0')} kWh")
                else:
                    print(f"   - {param_name}: err={err}")


def main():
    print("="*60)
    print("DESSMONITOR API LOCAL TEST")
    print("="*60)

    # Load credentials
    if not CREDENTIALS_PATH.exists():
        print(f"ERROR: Credentials file not found: {CREDENTIALS_PATH}")
        return 1

    with open(CREDENTIALS_PATH) as f:
        creds = json.load(f)

    # Find DessMonitor config - handle multiple formats
    dm_config = None
    if "platforms" in creds and "dessmonitor" in creds["platforms"]:
        dm_config = creds["platforms"]["dessmonitor"]
    elif "dessmonitor_accounts" in creds:
        dm_config = creds
    elif "accounts" in creds:
        # Direct format: {company_key, accounts}
        dm_config = creds

    if not dm_config:
        print("ERROR: No DessMonitor configuration found")
        print(f"Keys in file: {list(creds.keys())}")
        return 1

    company_key = dm_config.get("company_key", "bnrl_frRFjEz8Mkn")
    accounts = dm_config.get("accounts", dm_config.get("dessmonitor_accounts", []))

    print(f"Company Key: {company_key}")
    print(f"Accounts: {len(accounts)}")

    # Test each account
    for account in accounts:
        test_account(account, company_key)

    print("\n" + "="*60)
    print("DONE")
    print("="*60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
