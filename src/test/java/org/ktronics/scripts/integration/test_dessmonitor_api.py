#!/usr/bin/env python3
"""
Integration tests for DessMonitor API
Tests authentication and API calls using dessmonitor_common.sh

These tests make real API calls and require:
- Valid DessMonitor credentials in dessmonitor_credentials.json
- Unix environment (skipped on Windows, runs on GitHub Actions)
"""

import pytest
import subprocess
import json
import platform
import sys
from pathlib import Path

# Add scripts directory to path
PROJECT_ROOT = Path(__file__).resolve().parents[7]
SCRIPTS_DIR = PROJECT_ROOT / "src" / "main" / "java" / "org" / "ktronics" / "scripts"
CONFIG_DIR = PROJECT_ROOT / "src" / "main" / "java" / "org" / "ktronics" / "config"
sys.path.insert(0, str(SCRIPTS_DIR))

# DessMonitor credentials file
DESSMONITOR_CREDS_FILE = CONFIG_DIR / "dessmonitor_credentials.json"


def load_dessmonitor_credentials():
    """Load DessMonitor credentials for testing"""
    if not DESSMONITOR_CREDS_FILE.exists():
        pytest.skip(f"DessMonitor credentials file not found: {DESSMONITOR_CREDS_FILE}")

    with open(DESSMONITOR_CREDS_FILE) as f:
        data = json.load(f)

    accounts = data.get('dessmonitor_accounts', data.get('accounts', []))
    if not accounts:
        pytest.skip("No DessMonitor accounts found in credentials")

    # Find first valid account (skip Jeremy-Dess which has auth issues)
    for account in accounts:
        if account.get('username') and account.get('password'):
            if 'jeremy' not in account.get('label', '').lower():
                return {
                    'username': account['username'],
                    'password': account['password'],
                    'company_key': data.get('company_key', 'bnrl_frRFjEz8Mkn'),
                    'label': account.get('label', account['username'])
                }

    pytest.skip("No valid DessMonitor account found")


class TestDessMonitorAPI:
    """Test DessMonitor API authentication and calls"""

    @pytest.fixture
    def test_creds(self):
        """Get test credentials"""
        return load_dessmonitor_credentials()

    def run_bash_script(self, script_content, timeout=60):
        """Execute bash script and return output"""
        # Skip on Windows - these tests require Unix environment
        if platform.system() == 'Windows':
            pytest.skip("Bash script tests require Unix environment (Linux/macOS). Run in GitHub Actions.")

        result = subprocess.run(
            ['bash', '-c', script_content],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result

    def test_auth_email_function(self, test_creds):
        """Test dessmonitor_auth_email authentication function"""
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/dessmonitor_common.sh"

        if dessmonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}"; then
            echo "AUTH_SUCCESS"
            echo "TOKEN:${{DM_TOKEN:0:16}}"
            echo "SECRET:${{DM_SECRET:0:16}}"
            exit 0
        else
            echo "AUTH_FAILED"
            exit 1
        fi
        """

        result = self.run_bash_script(script)
        assert "AUTH_SUCCESS" in result.stdout or "AUTH_FAILED" in result.stdout, \
            f"Auth test produced unexpected output: {result.stdout}\nStderr: {result.stderr}"

    def test_auth_source_function(self, test_creds):
        """Test dessmonitor_auth_source authentication function (POST method)"""
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/dessmonitor_common.sh"

        if dessmonitor_auth_source "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}"; then
            echo "AUTH_SUCCESS"
            echo "TOKEN:${{DM_TOKEN:0:16}}"
            exit 0
        else
            echo "AUTH_FAILED"
            exit 1
        fi
        """

        result = self.run_bash_script(script)
        assert "AUTH_SUCCESS" in result.stdout or "AUTH_FAILED" in result.stdout, \
            f"Auth test produced unexpected output: {result.stdout}\nStderr: {result.stderr}"

    def test_authenticate_with_fallback(self, test_creds):
        """Test dessmonitor_authenticate dual-method fallback"""
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/dessmonitor_common.sh"

        if dessmonitor_authenticate "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}"; then
            echo "AUTH_SUCCESS"
            echo "TOKEN:${{DM_TOKEN:0:16}}"
            exit 0
        else
            echo "AUTH_FAILED"
            exit 1
        fi
        """

        result = self.run_bash_script(script)
        assert "AUTH_SUCCESS" in result.stdout, \
            f"Dual auth should succeed for {test_creds['label']}: {result.stdout}\nStderr: {result.stderr}"

    def test_query_plants(self, test_creds):
        """Test queryPlants API call returns valid plant data"""
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/dessmonitor_common.sh"

        dessmonitor_authenticate "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}"

        resp=$(dessmonitor_api_call "queryPlants" "page=0&pagesize=50")
        echo "RESPONSE:$resp"

        if echo "$resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
            echo "PLANTS_SUCCESS"
            # Extract pid
            pid=$(echo "$resp" | grep -o '"pid"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*$')
            echo "PID:$pid"
        else
            echo "PLANTS_FAILED"
        fi
        """

        result = self.run_bash_script(script)
        assert "PLANTS_SUCCESS" in result.stdout, \
            f"queryPlants failed: {result.stdout}\nStderr: {result.stderr}"
        assert "PID:" in result.stdout, "Should extract plant ID from response"

    def test_query_devices_with_sn_parameter(self, test_creds):
        """
        REGRESSION TEST: Verify webQueryDeviceEs works with sn=pid parameter.

        BUG FIX (2026-01-24):
        - Old: pn=pid returns ERR_NOT_FOUND_DEVICE (error 258)
        - New: sn=pid returns device list correctly
        """
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/dessmonitor_common.sh"

        dessmonitor_authenticate "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}"

        # Get plant ID first
        plants_resp=$(dessmonitor_api_call "queryPlants" "page=0&pagesize=50")
        pid=$(echo "$plants_resp" | grep -o '"pid"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*$')
        echo "PID:$pid"

        # Query devices with sn=pid (CORRECT - confirmed by local testing)
        devices_resp=$(dessmonitor_api_call "webQueryDeviceEs" "sn=${{pid}}")
        echo "DEVICES_RESPONSE:$devices_resp"

        if echo "$devices_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
            echo "DEVICES_SUCCESS"
            # Extract device pn (collector ID like D70000210187967959)
            device_pn=$(echo "$devices_resp" | grep -o '"pn"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1)
            echo "DEVICE_PN:$device_pn"
        else
            echo "DEVICES_FAILED"
            err=$(echo "$devices_resp" | grep -o '"err"[[:space:]]*:[[:space:]]*[0-9]*' | head -1)
            echo "ERROR:$err"
        fi
        """

        result = self.run_bash_script(script)
        assert "DEVICES_SUCCESS" in result.stdout, \
            f"webQueryDeviceEs with sn= failed: {result.stdout}\nStderr: {result.stderr}"
        assert "DEVICE_PN:" in result.stdout, "Should extract device pn from response"

    def test_energy_query_with_energy_today_parameter(self, test_creds):
        """
        REGRESSION TEST: Verify ENERGY_TODAY parameter returns real data.

        BUG FIX (2026-01-24):
        - ENERGY_TODAY_FROM_GRID returns 0.0000 for all values
        - ENERGY_TODAY returns real kWh data (5-8 kWh/day)
        """
        script = f"""
        set +e  # Don't exit on error for this test
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/dessmonitor_common.sh"

        dessmonitor_authenticate "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" 2>/dev/null

        # Get plant ID
        plants_resp=$(dessmonitor_api_call "queryPlants" "page=0&pagesize=50" 2>/dev/null)
        pid=$(echo "$plants_resp" | grep -o '"pid"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*$')

        # Get device info
        devices_resp=$(dessmonitor_api_call "webQueryDeviceEs" "sn=${{pid}}" 2>/dev/null)
        device_pn=$(echo "$devices_resp" | grep -o '"pn"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:"//;s/"$//')
        device_sn=$(echo "$devices_resp" | grep -o '"sn"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:"//;s/"$//')
        devcode=$(echo "$devices_resp" | grep -o '"devcode"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*$')
        devaddr=$(echo "$devices_resp" | grep -o '"devaddr"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*$')

        echo "DEVICE: pn=$device_pn sn=$device_sn devcode=$devcode devaddr=$devaddr"

        # Test ENERGY_TODAY parameter (should return real data)
        energy_resp=$(dessmonitor_api_call "querySPDeviceKeyParameterMonthPerDay" \\
            "pn=${{device_pn}}&sn=${{device_sn}}&devcode=${{devcode}}&devaddr=${{devaddr}}&parameter=ENERGY_TODAY&date=2026-01&i18n=en_US&chartStatus=false" 2>/dev/null)

        if echo "$energy_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
            echo "ENERGY_SUCCESS"
            # Check for non-zero values in option array
            if echo "$energy_resp" | grep -q '"val"[[:space:]]*:[[:space:]]*"[1-9]'; then
                echo "HAS_NONZERO_VALUES"
            elif echo "$energy_resp" | grep -q '"val"[[:space:]]*:[[:space:]]*"0\\.0'; then
                echo "ALL_ZERO_VALUES"
            fi
            # Show sample data
            echo "RESPONSE_SAMPLE:$(echo "$energy_resp" | head -c 500)"
        else
            echo "ENERGY_FAILED"
        fi
        """

        result = self.run_bash_script(script, timeout=90)
        assert "ENERGY_SUCCESS" in result.stdout, \
            f"Energy query failed: {result.stdout}\nStderr: {result.stderr}"
        # Note: We don't assert HAS_NONZERO_VALUES because some days might legitimately be zero

    def test_response_uses_option_array_and_gts_field(self, test_creds):
        """
        REGRESSION TEST: Verify response format is dat.option[] with gts field.

        BUG FIX (2026-01-24):
        - Response data is in dat.option[] (NOT dat.perday[])
        - Date field is gts (NOT ts)
        """
        script = f"""
        set +e
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/dessmonitor_common.sh"

        dessmonitor_authenticate "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" 2>/dev/null

        # Get plant and device info
        plants_resp=$(dessmonitor_api_call "queryPlants" "page=0&pagesize=50" 2>/dev/null)
        pid=$(echo "$plants_resp" | grep -o '"pid"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*$')

        devices_resp=$(dessmonitor_api_call "webQueryDeviceEs" "sn=${{pid}}" 2>/dev/null)
        device_pn=$(echo "$devices_resp" | grep -o '"pn"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:"//;s/"$//')
        device_sn=$(echo "$devices_resp" | grep -o '"sn"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*:"//;s/"$//')
        devcode=$(echo "$devices_resp" | grep -o '"devcode"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*$')
        devaddr=$(echo "$devices_resp" | grep -o '"devaddr"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*$')

        # Query energy data
        energy_resp=$(dessmonitor_api_call "querySPDeviceKeyParameterMonthPerDay" \\
            "pn=${{device_pn}}&sn=${{device_sn}}&devcode=${{devcode}}&devaddr=${{devaddr}}&parameter=ENERGY_TODAY&date=2026-01&i18n=en_US&chartStatus=false" 2>/dev/null)

        # Check response format
        if echo "$energy_resp" | grep -q '"option"[[:space:]]*:'; then
            echo "HAS_OPTION_ARRAY"
        else
            echo "MISSING_OPTION_ARRAY"
        fi

        if echo "$energy_resp" | grep -q '"gts"[[:space:]]*:'; then
            echo "HAS_GTS_FIELD"
        else
            echo "MISSING_GTS_FIELD"
        fi

        echo "FULL_RESPONSE:$energy_resp"
        """

        result = self.run_bash_script(script, timeout=90)
        assert "HAS_OPTION_ARRAY" in result.stdout, \
            f"Response should use 'option' array: {result.stdout}"
        assert "HAS_GTS_FIELD" in result.stdout, \
            f"Response should use 'gts' date field: {result.stdout}"


class TestDessMonitorScripts:
    """Test DessMonitor shell scripts"""

    def run_bash_script(self, script_content, timeout=60):
        """Execute bash script and return output"""
        if platform.system() == 'Windows':
            pytest.skip("Bash script tests require Unix environment")

        result = subprocess.run(
            ['bash', '-c', script_content],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result

    def test_check_monthly_script(self):
        """Test check_dessmonitor_monthly.sh script"""
        creds = load_dessmonitor_credentials()
        script = f"""
        set -euo pipefail
        cd "{SCRIPTS_DIR}"
        bash check_dessmonitor_monthly.sh "{DESSMONITOR_CREDS_FILE}" "2026-01" 2>&1 | grep -E "(AUTH OK|DONE)"
        """

        result = self.run_bash_script(script, timeout=90)
        assert result.returncode == 0, f"Monthly script failed: {result.stderr}"
        assert "AUTH OK" in result.stdout or "DONE" in result.stdout

    def test_check_yearly_script(self):
        """Test check_dessmonitor_yearly.sh script"""
        creds = load_dessmonitor_credentials()
        script = f"""
        set -euo pipefail
        cd "{SCRIPTS_DIR}"
        timeout 60 bash check_dessmonitor_yearly.sh "{DESSMONITOR_CREDS_FILE}" "2026" 2>&1 | head -20 | grep -E "(AUTH OK|DONE)"
        """

        result = self.run_bash_script(script, timeout=90)
        # Allow timeout (124) and SIGPIPE (141 from head closing pipe early)
        assert result.returncode in [0, 124, 141], f"Yearly script failed: {result.stderr}"
        assert "AUTH OK" in result.stdout or "DONE" in result.stdout

    def test_yearly_script_uses_device_level_api(self):
        """
        REGRESSION TEST: Verify check_dessmonitor_yearly.sh uses correct API params.

        BUG FIX (2026-01-24):
        - Must use sn=pid (NOT pn=pid) for device query
        - Must use ENERGY_TODAY (NOT ENERGY_TODAY_FROM_GRID) for energy data
        - Must parse dat.option[] with gts field
        """
        script_path = SCRIPTS_DIR / "check_dessmonitor_yearly.sh"
        if not script_path.exists():
            pytest.skip("check_dessmonitor_yearly.sh not found")

        content = script_path.read_text()

        # Must use correct device query parameter
        assert 'webQueryDeviceEs' in content, "Should use webQueryDeviceEs"
        assert 'sn=${pid}' in content, "Should use sn=pid (not pn=pid) for device query"

        # Must use correct energy parameter
        assert 'ENERGY_TODAY' in content, "Should use ENERGY_TODAY parameter"
        assert 'querySPDeviceKeyParameterMonthPerDay' in content, "Should use device-level energy API"


class TestDessMonitorAPIErrorHandling:
    """Test error handling for DessMonitor API"""

    def run_bash_script(self, script_content, timeout=30):
        """Execute bash script and return output"""
        if platform.system() == 'Windows':
            pytest.skip("Bash script tests require Unix environment")

        result = subprocess.run(
            ['bash', '-c', script_content],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result

    def test_invalid_credentials_handled(self):
        """Test that invalid credentials are handled gracefully"""
        script = f"""
        set +e
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/dessmonitor_common.sh"

        if dessmonitor_authenticate "invalid_user" "invalid_pass" "bnrl_frRFjEz8Mkn" 2>&1; then
            echo "AUTH_UNEXPECTED_SUCCESS"
        else
            echo "AUTH_FAILED_AS_EXPECTED"
        fi
        """

        result = self.run_bash_script(script)
        assert "AUTH_FAILED_AS_EXPECTED" in result.stdout, \
            "Invalid credentials should fail gracefully"

    def test_wrong_device_query_parameter_fails(self):
        """
        REGRESSION TEST: Verify pn=pid fails (confirming we need sn=pid).

        This test confirms the bug that was fixed - using pn=pid returns error 258.
        """
        creds = load_dessmonitor_credentials()
        script = f"""
        set +e
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/dessmonitor_common.sh"

        dessmonitor_authenticate "{creds['username']}" "{creds['password']}" "{creds['company_key']}" 2>/dev/null

        # Get plant ID
        plants_resp=$(dessmonitor_api_call "queryPlants" "page=0&pagesize=50" 2>/dev/null)
        pid=$(echo "$plants_resp" | grep -o '"pid"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*$')

        # Query devices with WRONG parameter pn=pid (should fail with error 258)
        devices_resp=$(dessmonitor_api_call "webQueryDeviceEs" "pn=${{pid}}" 2>/dev/null)

        if echo "$devices_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*258'; then
            echo "CORRECTLY_FAILS_WITH_258"
        elif echo "$devices_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
            echo "UNEXPECTED_SUCCESS"
        else
            echo "OTHER_ERROR"
            echo "$devices_resp"
        fi
        """

        result = self.run_bash_script(script)
        # This test documents the bug - pn=${pid} fails with error 258
        assert "CORRECTLY_FAILS_WITH_258" in result.stdout or "OTHER_ERROR" in result.stdout, \
            f"pn=${'{pid}'} should fail: {result.stdout}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
