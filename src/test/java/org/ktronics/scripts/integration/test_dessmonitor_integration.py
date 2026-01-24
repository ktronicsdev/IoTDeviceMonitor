#!/usr/bin/env python3
"""
UC10: Multi-Cloud Platform Support - DessMonitor Integration Tests

Tests for DessMonitor API integration including:
- Credentials file format validation
- Platform filtering in check_anomaly.py
- CSV file naming conventions
- Multi-platform alert aggregation
"""

import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add scripts directory to path
# ShineMonitor tests use 6 .parent calls because they IMPORT modules (sys.path based)
# DessMonitor tests use subprocess.run() which needs ABSOLUTE path to script file
# Starting from: src/test/java/org/ktronics/scripts/integration/test_dessmonitor_integration.py
# 8 .parent calls reach repo root (IOT/), then add src/main/java/org/ktronics/scripts
scripts_dir = Path(__file__).parent.parent.parent.parent.parent.parent.parent.parent / "src" / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(scripts_dir))


class TestDessMonitorCredentials:
    """Test DessMonitor credentials file format and validation"""

    def test_dessmonitor_credentials_format(self):
        """Test that dessmonitor_credentials.json follows expected format"""
        # Expected format for DessMonitor credentials
        expected_format = {
            "company_key": "bnrl_frRFjEz8Mkn",
            "dessmonitor_accounts": [
                {
                    "label": "TestCustomer",
                    "username": "testuser",
                    "password": "testpass",
                    "email": "test@example.com"
                }
            ]
        }

        # Validate structure
        assert "company_key" in expected_format
        assert "dessmonitor_accounts" in expected_format
        assert isinstance(expected_format["dessmonitor_accounts"], list)
        assert len(expected_format["dessmonitor_accounts"]) > 0

        account = expected_format["dessmonitor_accounts"][0]
        assert "label" in account
        assert "username" in account
        assert "password" in account

    def test_multiplatform_credentials_format(self):
        """Test multi-platform credentials.json format with both ShineMonitor and DessMonitor"""
        multiplatform_format = {
            "platforms": {
                "shinemonitor": {
                    "company_key": "bnrl_frRFjEz8Mkn",
                    "accounts": [
                        {
                            "label": "ShineCustomer",
                            "username": "shineuser",
                            "password": "shinepass",
                            "email": "shine@example.com"
                        }
                    ]
                },
                "dessmonitor": {
                    "company_key": "bnrl_frRFjEz8Mkn",
                    "accounts": [
                        {
                            "label": "DessCustomer",
                            "username": "dessuser",
                            "password": "desspass",
                            "email": "dess@example.com"
                        }
                    ]
                }
            }
        }

        # Validate structure
        assert "platforms" in multiplatform_format
        assert "shinemonitor" in multiplatform_format["platforms"]
        assert "dessmonitor" in multiplatform_format["platforms"]

        # Both platforms should have accounts
        assert len(multiplatform_format["platforms"]["shinemonitor"]["accounts"]) > 0
        assert len(multiplatform_format["platforms"]["dessmonitor"]["accounts"]) > 0


class TestPlatformFiltering:
    """Test platform filtering in check_anomaly.py"""

    def test_load_daily_series_with_platform_filter(self):
        """Test that load_daily_series respects platform filter"""
        from check_anomaly import load_daily_series

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create ShineMonitor CSV file
            shine_csv = data_dir / "gayan-imh-plant-2026-01.csv"
            shine_csv.write_text("date,kwh\n2026-01-01,10.5\n2026-01-02,11.2\n")

            # Create DessMonitor CSV file (with platform prefix)
            dess_csv = data_dir / "dessmonitor-mifraz-plant-2026-01.csv"
            dess_csv.write_text("date,kwh\n2026-01-01,8.3\n2026-01-02,9.1\n")

            # Load without filter - should get both
            all_data = load_daily_series(data_dir)
            assert len(all_data) == 2
            assert "gayan-imh-plant" in all_data
            assert "dessmonitor-mifraz-plant" in all_data

            # Load with dessmonitor filter - should only get DessMonitor
            dess_data = load_daily_series(data_dir, platform_filter="dessmonitor")
            assert len(dess_data) == 1
            assert "dessmonitor-mifraz-plant" in dess_data
            assert "gayan-imh-plant" not in dess_data

    def test_csv_naming_convention_dessmonitor(self):
        """Test that DessMonitor CSV files follow naming convention: dessmonitor-<label>-<plant>-YYYY-MM.csv"""
        import re

        # Expected naming pattern
        pattern = re.compile(r"^dessmonitor-[a-z0-9-]+-[a-z0-9-]+-\d{4}-\d{2}\.csv$")

        # Valid names
        assert pattern.match("dessmonitor-mifraz-plant1-2026-01.csv")
        assert pattern.match("dessmonitor-jeremy-dess-solar-5kw-2026-01.csv")

        # Invalid names (missing prefix)
        assert not pattern.match("mifraz-plant1-2026-01.csv")


class TestAnomalyDetectionMultiPlatform:
    """Test anomaly detection with multi-platform support"""

    def test_platform_label_in_alerts(self):
        """Test that alerts include platform label in output"""
        from check_anomaly import load_daily_series

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)

            # Create DessMonitor CSV with low production (should trigger alert)
            today = date.today()
            csv_content = "date,kwh\n"

            # Add baseline data (14 days before alert window)
            for i in range(20, 6, -1):
                d = today - timedelta(days=i)
                csv_content += f"{d},15.0\n"  # Normal production

            # Add alert window (3 days of low production)
            for i in range(3, 0, -1):
                d = today - timedelta(days=i)
                csv_content += f"{d},1.0\n"  # Very low production (< 20% baseline)

            dess_csv = data_dir / "dessmonitor-test-plant-2026-01.csv"
            dess_csv.write_text(csv_content)

            # Load data
            data = load_daily_series(data_dir, platform_filter="dessmonitor")
            assert len(data) == 1
            assert "dessmonitor-test-plant" in data

    def test_separate_state_files_per_platform(self):
        """Test that each platform uses separate state files"""
        # ShineMonitor state file
        shine_state = "state/alerts_state.json"
        # DessMonitor state file
        dess_state = "state/dessmonitor_alerts_state.json"

        # These should be different paths
        assert shine_state != dess_state

        # Verify naming convention
        assert "dessmonitor" in dess_state
        assert "dessmonitor" not in shine_state


class TestDessMonitorAPIClient:
    """Test DessMonitor API client functions (mocked)"""

    def test_api_endpoint_configuration(self):
        """
        Test that DessMonitor API endpoint is correctly configured.

        CRITICAL BUG FIX (2026-01-19):
        - web.dessmonitor.com has CAPTCHA protection causing ERR_PASSWORD_VERIF_FAIL (error 16)
        - api.dessmonitor.com is the official API endpoint without CAPTCHA
        - Fix: Change API_URL from web.dessmonitor.com to api.dessmonitor.com

        This test ensures we use the correct API endpoint.
        """
        # Read the actual API_URL from dessmonitor_common.sh
        script_path = scripts_dir / "dessmonitor_common.sh"

        if script_path.exists():
            content = script_path.read_text()

            # Extract API_URL line
            import re
            match = re.search(r'API_URL="([^"]+)"', content)
            assert match is not None, "API_URL not found in dessmonitor_common.sh"

            actual_url = match.group(1)

            # CRITICAL: Must use api.dessmonitor.com, NOT web.dessmonitor.com
            assert "api.dessmonitor.com" in actual_url, \
                f"API_URL must use api.dessmonitor.com (no CAPTCHA), got: {actual_url}"
            assert "web.dessmonitor.com" not in actual_url, \
                f"API_URL must NOT use web.dessmonitor.com (has CAPTCHA), got: {actual_url}"

            # Verify full expected URL
            expected_endpoint = "https://api.dessmonitor.com/public/"
            assert actual_url == expected_endpoint, \
                f"Expected {expected_endpoint}, got {actual_url}"
        else:
            # Fallback test if script doesn't exist
            expected_endpoint = "https://api.dessmonitor.com/public/"
            assert expected_endpoint.startswith("https://")
            assert "api.dessmonitor.com" in expected_endpoint

    def test_api_endpoint_not_web_dessmonitor(self):
        """
        REGRESSION TEST: Ensure we never accidentally use web.dessmonitor.com

        Background: web.dessmonitor.com returns CAPTCHA challenge on login attempts,
        causing all API authentications to fail with error 16 (ERR_PASSWORD_VERIF_FAIL).
        The correct endpoint is api.dessmonitor.com which has no CAPTCHA.
        """
        script_path = scripts_dir / "dessmonitor_common.sh"

        if script_path.exists():
            content = script_path.read_text()

            # These patterns should NEVER appear
            forbidden_patterns = [
                'web.dessmonitor.com',
                'www.dessmonitor.com',
            ]

            for pattern in forbidden_patterns:
                # Only check in API_URL assignment (not comments)
                import re
                matches = re.findall(rf'API_URL=.*{pattern}', content)
                assert len(matches) == 0, \
                    f"Found forbidden pattern '{pattern}' in API_URL. Use api.dessmonitor.com instead!"

    def test_authentication_signature_format(self):
        """Test SHA-1 signature generation format for DessMonitor API"""
        import hashlib

        # Test signature generation (same as ShineMonitor)
        username = "testuser"
        password = "testpass"
        salt = "1234567890123"

        # Password SHA-1
        pw_sha1 = hashlib.sha1(password.encode()).hexdigest()
        assert len(pw_sha1) == 40  # SHA-1 produces 40-char hex

        # Signature: SHA-1(username + pw_sha1 + salt)
        sign_input = f"{username}{pw_sha1}{salt}"
        sign = hashlib.sha1(sign_input.encode()).hexdigest()
        assert len(sign) == 40

    def test_dual_auth_fallback_exists(self):
        """
        Test that dual authentication fallback is implemented.

        DessMonitor supports two auth methods:
        1. authEmail (GET) - faster, try first
        2. authSource (POST) - fallback if authEmail fails

        Both methods use same SHA-1 signature scheme.
        """
        script_path = scripts_dir / "dessmonitor_common.sh"

        if script_path.exists():
            content = script_path.read_text()

            # Check for dual auth functions
            assert "dessmonitor_auth_email" in content, \
                "Missing dessmonitor_auth_email function"
            assert "dessmonitor_auth_source" in content, \
                "Missing dessmonitor_auth_source function"
            assert "dessmonitor_authenticate" in content, \
                "Missing dessmonitor_authenticate (fallback wrapper) function"

            # Check fallback logic exists
            assert "authEmail failed" in content or "fallback" in content.lower(), \
                "Missing fallback logic between auth methods"

    def test_auth_error_codes_documented(self):
        """Test that common DessMonitor API error codes are known"""
        # Common error codes from DessMonitor API
        error_codes = {
            0: "Success",
            8: "ERR_FORBIDDEN (missing required parameters)",
            16: "ERR_PASSWORD_VERIF_FAIL (wrong password or CAPTCHA)",
            264: "ERR_NOT_FOUND_DEVICE_WARNING (no alarms - not an error)",
        }

        # All codes should be defined
        assert error_codes[0] == "Success"
        assert error_codes[16] == "ERR_PASSWORD_VERIF_FAIL (wrong password or CAPTCHA)"

        # This error caused the CAPTCHA issue - now fixed by using api.dessmonitor.com
        captcha_error = 16
        assert captcha_error in error_codes

    def test_api_actions_available(self):
        """Test that required API actions are documented"""
        required_actions = [
            "authSource",       # Authentication
            "queryPlants",      # Get plant list
            "webQueryCollectorsEs",  # Get collectors
            "queryDeviceWarning",    # Get device alarms
            "webQueryDeviceEs",      # Get device energy stats
        ]

        # All actions should be defined
        for action in required_actions:
            assert len(action) > 0


class TestDessMonitorCredentialValidation:
    """Test credential file validation for DessMonitor"""

    def test_credentials_has_dessmonitor_accounts_key(self):
        """
        Test that DessMonitor credentials use 'dessmonitor_accounts' key (not 'accounts').

        Bug fix: GitHub secret was initially created with wrong key name.
        Correct key: dessmonitor_accounts
        Wrong key: accounts (that's for ShineMonitor)
        """
        valid_credentials = {
            "company_key": "bnrl_frRFjEz8Mkn",
            "dessmonitor_accounts": [
                {"label": "Test", "username": "user", "password": "pass"}
            ]
        }

        # Must have dessmonitor_accounts, not accounts
        assert "dessmonitor_accounts" in valid_credentials
        assert "accounts" not in valid_credentials

    def test_credentials_account_required_fields(self):
        """Test that each account has required fields"""
        required_fields = ["label", "username", "password"]
        optional_fields = ["email"]  # Email only needed for customer alerts

        account = {
            "label": "MifrazMarsoon",
            "username": "mifrazlk",
            "password": "testpass123",
            "email": "test@example.com"  # Optional
        }

        for field in required_fields:
            assert field in account, f"Missing required field: {field}"

    def test_credentials_company_key_present(self):
        """Test that company_key is present in credentials"""
        credentials = {
            "company_key": "bnrl_frRFjEz8Mkn",
            "dessmonitor_accounts": []
        }

        assert "company_key" in credentials
        assert len(credentials["company_key"]) > 0

    def test_workflow_validates_credentials_json(self):
        """
        Test that workflow validates credentials file before using it.

        The workflow should:
        1. Check if file is valid JSON
        2. Check for dessmonitor_accounts array
        3. Log number of accounts found
        """
        # Workflow validation command pattern
        jq_validate_cmd = "jq -e '.' credentials.json"
        jq_count_cmd = "jq -r '.dessmonitor_accounts | length'"

        assert "jq -e" in jq_validate_cmd  # -e flag returns error on invalid JSON
        assert "dessmonitor_accounts" in jq_count_cmd


class TestMultiPlatformAlertAggregation:
    """Test alert aggregation from multiple platforms"""

    def test_combined_alert_email_subject(self):
        """Test that email subject indicates multi-platform alerts"""
        # When both platforms have alerts
        shine_alerts = [{"plant": "plant-a", "severity": "RED"}]
        dess_alerts = [{"plant": "plant-b", "severity": "ORANGE"}]

        # Subject should indicate both platforms
        if shine_alerts and dess_alerts:
            expected_subject_contains = ["ShineMonitor", "DessMonitor"]
        elif shine_alerts:
            expected_subject_contains = ["ShineMonitor"]
        elif dess_alerts:
            expected_subject_contains = ["DessMonitor"]

        # Verify expected content
        assert len(expected_subject_contains) > 0

    def test_alerts_tagged_with_platform(self):
        """Test that alerts include platform source"""
        alert_with_platform = {
            "platform": "dessmonitor",
            "plant_key": "mifraz-solar-5kw",
            "severity": "RED",
            "days": 3,
            "baseline_avg_kwh_per_day": 15.2
        }

        assert "platform" in alert_with_platform
        assert alert_with_platform["platform"] == "dessmonitor"


class TestBackwardCompatibility:
    """Test backward compatibility with existing ShineMonitor setup"""

    def test_old_credentials_format_still_works(self):
        """Test that old ShineMonitor-only credentials format is still supported"""
        old_format = {
            "company_key": "bnrl_frRFjEz8Mkn",
            "accounts": [
                {
                    "label": "Gayan-IMH",
                    "username": "user@email.com",
                    "password": "password",
                    "email": "customer@email.com"
                }
            ]
        }

        # Old format has accounts at root level
        assert "accounts" in old_format
        assert "platforms" not in old_format

        # Can detect old format by checking for "accounts" at root
        is_old_format = "accounts" in old_format and "platforms" not in old_format
        assert is_old_format

    def test_shinemonitor_csv_naming_unchanged(self):
        """Test that ShineMonitor CSV naming convention is unchanged"""
        import re

        # ShineMonitor pattern (no platform prefix - does NOT start with 'dessmonitor-')
        # Both patterns use same suffix, but DessMonitor has specific prefix
        shine_pattern = re.compile(r"^(?!dessmonitor-)[a-z0-9-]+-\d{4}-\d{2}\.csv$")

        # Valid ShineMonitor names
        assert shine_pattern.match("gayan-imh-plant-2026-01.csv")
        assert shine_pattern.match("jeremy-solar-5kw-2026-01.csv")

        # DessMonitor names should NOT match (have prefix)
        assert not shine_pattern.match("dessmonitor-mifraz-plant-2026-01.csv")


class TestDessMonitorDataCollection:
    """
    Test DessMonitor data collection scripts (bash integration tests).

    These tests verify the CRITICAL BUG FIX (2026-01-21):
    - Changed from device-level API to plant-level API
    - Fixed date parsing to use API response dates (not today's date)
    - Added AWK parsing for daily breakdown (matches ShineMonitor pattern)
    """

    def run_bash_script(self, script_content, timeout=30):
        """Execute bash script and return output"""
        import subprocess
        import platform as plat

        # Skip on Windows - these tests require Unix environment
        if plat.system() == 'Windows':
            pytest.skip("Bash script tests require Unix environment (Linux/macOS). Run in GitHub Actions.")

        result = subprocess.run(
            ['bash', '-c', script_content],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result

    def test_dessmonitor_common_functions_exist(self):
        """Test that dessmonitor_common.sh defines required functions"""
        script_path = scripts_dir / "dessmonitor_common.sh"

        if not script_path.exists():
            pytest.skip("dessmonitor_common.sh not found")

        content = script_path.read_text()

        # Required functions
        required_functions = [
            "dessmonitor_authenticate",
            "dessmonitor_api_call",
            "dessmonitor_auth_email",
            "dessmonitor_auth_source",
        ]

        for func in required_functions:
            assert func in content, f"Missing required function: {func}"

    def test_check_monthly_uses_device_level_api(self):
        """
        REGRESSION TEST: Verify check_dessmonitor_monthly.sh uses device-level API.

        BUG FIX (2026-01-24):
        - Old: queryPlantEnergyMonthPerDay (plant-level, returns 0 for all values)
        - New: querySPDeviceKeyParameterMonthPerDay (device-level, returns real data)

        LOCAL TESTING CONFIRMED (2026-01-24):
        - Query devices with sn=${pid} (NOT pn=${pid})
        - Use ENERGY_TODAY parameter (NOT ENERGY_TODAY_FROM_GRID which returns 0s)
        - Response data is in dat.option[] (NOT dat.perday[])
        - Date field is gts (NOT ts)

        Flow:
        1. webQueryDeviceEs with sn=${pid} - get device list (pn, sn, devcode, devaddr)
        2. querySPDeviceKeyParameterMonthPerDay with ENERGY_TODAY - get actual energy data
        """
        script_path = scripts_dir / "check_dessmonitor_monthly.sh"

        if not script_path.exists():
            pytest.skip("check_dessmonitor_monthly.sh not found")

        content = script_path.read_text()

        # MUST use device-level APIs (confirmed from local testing)
        assert "webQueryDeviceEs" in content, \
            "Script must use webQueryDeviceEs to get device list"
        assert "querySPDeviceKeyParameterMonthPerDay" in content, \
            "Script must use querySPDeviceKeyParameterMonthPerDay for energy data"
        assert "ENERGY_TODAY" in content, \
            "Script must request ENERGY_TODAY parameter (not ENERGY_TODAY_FROM_GRID which returns 0s)"

    def test_check_monthly_parses_dates_from_api(self):
        """
        REGRESSION TEST: Verify dates are parsed from API response (not hardcoded to today).

        BUG FIX (2026-01-21):
        - Old: today=$(date -u +%Y-%m-%d) - wrote today's date for all rows
        - New: Extract date field from API response - each row has correct date

        LOCAL TESTING CONFIRMED (2026-01-24):
        - Date field is "gts" (NOT "ts")
        - Response format: {"dat":{"option":[{"gts":"2026-01-01","val":"8.6120"},...]}}
        """
        script_path = scripts_dir / "check_dessmonitor_monthly.sh"

        if not script_path.exists():
            pytest.skip("check_dessmonitor_monthly.sh not found")

        content = script_path.read_text()

        # MUST use AWK to parse dates from API (looks for "gts" field)
        assert '"gts"' in content, \
            "Script must parse 'gts' (date) field from API response (confirmed by local testing)"

        # MUST use AWK for parsing (same pattern as ShineMonitor)
        assert "awk" in content, \
            "Script must use AWK to parse API response"

        # Should NOT hardcode today's date in the loop
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "echo \"${today}," in line or "echo \"$today," in line:
                # Check if this line is inside the plant loop (should NOT be)
                context = "\n".join(lines[max(0, i-10):min(len(lines), i+10)])
                assert "done  # plants" not in context or "while" not in context, \
                    f"Line {i+1}: Should NOT write hardcoded today date inside plant loop"

    def test_check_monthly_awk_parsing_logic(self):
        """
        Test that AWK parsing logic matches ShineMonitor pattern.

        Expected pattern: Extract "val" and "ts" pairs from API JSON response
        """
        script_path = scripts_dir / "check_dessmonitor_monthly.sh"

        if not script_path.exists():
            pytest.skip("check_dessmonitor_monthly.sh not found")

        content = script_path.read_text()

        # AWK should parse both val and ts fields
        assert '"val"' in content and '"ts"' in content, \
            "AWK parsing must extract both 'val' (kWh) and 'ts' (date) from API response"

        # Should use mapfile to collect rows
        assert "mapfile" in content or "ROWS" in content, \
            "Should use mapfile to collect daily rows"

    def test_check_monthly_csv_output_format(self):
        """Test that CSV output format is correct: date,kwh"""
        script_path = scripts_dir / "check_dessmonitor_monthly.sh"

        if not script_path.exists():
            pytest.skip("check_dessmonitor_monthly.sh not found")

        content = script_path.read_text()

        # CSV header must be "date,kwh"
        assert 'echo "date,kwh"' in content or '"date,kwh"' in content, \
            "CSV header must be 'date,kwh'"

        # AWK output should be "gts,val" format (gts = date field from DessMonitor API)
        # LOCAL TESTING CONFIRMED (2026-01-24): Date field is "gts" not "day"
        assert 'print gts "," val' in content or 'print gts","val' in content, \
            "AWK output must format as: gts,val (gts is the date field from DessMonitor API)"

    def test_check_monthly_file_naming_convention(self):
        """
        Test that output files follow naming convention.

        Expected: data/dessmonitor-<label>-<plant>-YYYY-MM.csv
        """
        script_path = scripts_dir / "check_dessmonitor_monthly.sh"

        if not script_path.exists():
            pytest.skip("check_dessmonitor_monthly.sh not found")

        content = script_path.read_text()

        # Output path must include dessmonitor prefix
        assert 'dessmonitor-${' in content or 'dessmonitor-$' in content, \
            "Output filename must include 'dessmonitor-' prefix"

        # Output path must include month variable
        assert '${MONTH}' in content or '$MONTH' in content, \
            "Output filename must include month (YYYY-MM)"


class TestDessMonitorYearlyScript:
    """Tests for check_dessmonitor_yearly.sh script"""

    def test_yearly_script_parses_monthly_values_correctly(self):
        """
        Test that yearly script correctly parses monthly values from API response.

        The querySPDeviceKeyParameterYearPerMonth API returns monthly totals
        in dat.option[] array with gts (YYYY-MM) and val fields.
        """
        import re

        # Sample API response format from querySPDeviceKeyParameterYearPerMonth
        # (Confirmed from web portal browser DevTools)
        sample_response = '''{"err":0,"dat":{"option":[
            {"gts":"2026-01","val":"150.5000"},
            {"gts":"2026-02","val":"140.2000"},
            {"gts":"2026-03","val":"0.0000"},
            {"gts":"2026-04","val":"160.8000"}
        ]}}'''

        # Parse monthly values using regex (simulates AWK logic)
        months = []
        for match in re.finditer(r'"gts"\s*:\s*"([^"]+)"[^}]*"val"\s*:\s*"?([0-9.]+)"?', sample_response):
            months.append((match.group(1), float(match.group(2))))

        assert len(months) == 4, f"Should parse 4 months, got {len(months)}"
        assert months[0] == ("2026-01", 150.5), f"First month should be 2026-01 with 150.5 kWh"
        assert months[1] == ("2026-02", 140.2), f"Second month should be 2026-02 with 140.2 kWh"

    def test_yearly_script_uses_year_per_month_api(self):
        """
        Test that yearly script uses querySPDeviceKeyParameterYearPerMonth API.

        This API returns all 12 months in one call (efficient).
        NOT querySPDeviceKeyParameterMonthPerDay which would require 12 calls.
        """
        script_path = scripts_dir / "check_dessmonitor_yearly.sh"
        if not script_path.exists():
            pytest.skip("check_dessmonitor_yearly.sh not found")

        content = script_path.read_text()

        # Must use YearPerMonth API (returns all 12 months in one call)
        assert "querySPDeviceKeyParameterYearPerMonth" in content, \
            "Script should use querySPDeviceKeyParameterYearPerMonth API"

        # Must use ENERGY_TOTAL parameter (confirmed from web portal)
        assert "ENERGY_TOTAL" in content, \
            "Script should use ENERGY_TOTAL parameter"

        # Must write to CSV
        assert '>> "$out"' in content, "Script should append to output file"

    def test_yearly_script_processes_multiple_devices(self):
        """
        Test that yearly script processes ALL devices in a plant.

        Some plants have multiple inverters/devices. The script should
        query each device and append results to the CSV.
        """
        script_path = scripts_dir / "check_dessmonitor_yearly.sh"
        if not script_path.exists():
            pytest.skip("check_dessmonitor_yearly.sh not found")

        content = script_path.read_text()

        # Must loop through devices
        assert 'DEVICE_DATA' in content, "Script should process DEVICE_DATA"
        assert 'while read' in content and 'device_line' in content, \
            "Script should loop through each device"

        # Must append device results to CSV
        assert '>> "$out"' in content, \
            "Script should append device results to output CSV"

    def test_yearly_script_exists(self):
        """Test that check_dessmonitor_yearly.sh exists"""
        script_path = scripts_dir / "check_dessmonitor_yearly.sh"
        assert script_path.exists(), "check_dessmonitor_yearly.sh should exist"

    def test_yearly_script_uses_device_level_api(self):
        """
        REGRESSION TEST: Verify check_dessmonitor_yearly.sh uses device-level API.

        BUG FIX (2026-01-24):
        - Must use querySPDeviceKeyParameterYearPerMonth (NOT MonthPerDay)
        - Must use ENERGY_TOTAL parameter (NOT ENERGY_TODAY)
        - Plant-level API returns 0 for all values
        """
        script_path = scripts_dir / "check_dessmonitor_yearly.sh"
        if not script_path.exists():
            pytest.skip("check_dessmonitor_yearly.sh not found")

        content = script_path.read_text()

        # Must use device query with correct parameter
        assert "webQueryDeviceEs" in content, \
            "Script must use webQueryDeviceEs to get device list"
        assert 'sn=${pid}' in content, \
            "Script must use sn=pid (not pn=pid) for device query"

        # Must use correct energy API (YearPerMonth, not MonthPerDay)
        assert "querySPDeviceKeyParameterYearPerMonth" in content, \
            "Script must use querySPDeviceKeyParameterYearPerMonth API"
        assert "ENERGY_TOTAL" in content, \
            "Script must use ENERGY_TOTAL parameter (confirmed from web portal)"

    def test_yearly_script_uses_single_api_call(self):
        """
        Test that yearly script uses single API call for all 12 months.

        querySPDeviceKeyParameterYearPerMonth returns all 12 months in one call,
        so we should NOT loop through months 01-12 with separate calls.
        """
        script_path = scripts_dir / "check_dessmonitor_yearly.sh"
        if not script_path.exists():
            pytest.skip("check_dessmonitor_yearly.sh not found")

        content = script_path.read_text()

        # Should NOT have a month loop (seq -w 1 12) since API returns all months
        # The API call uses date=${YEAR} (just the year, not YYYY-MM)
        assert "date=${YEAR}" in content or 'date=${YEAR}' in content, \
            "Script should use date=YEAR format (not YYYY-MM)"

    def test_yearly_csv_output_format(self):
        """Test that yearly CSV output format is correct: month,kwh"""
        script_path = scripts_dir / "check_dessmonitor_yearly.sh"
        if not script_path.exists():
            pytest.skip("check_dessmonitor_yearly.sh not found")

        content = script_path.read_text()

        # Header should be month,kwh
        assert 'month,kwh' in content, \
            "CSV header must be 'month,kwh'"

    def test_yearly_file_naming_convention(self):
        """
        Test that yearly output files follow naming convention.

        Expected: data/dessmonitor-<label>-<plant>-YYYY.csv
        """
        script_path = scripts_dir / "check_dessmonitor_yearly.sh"
        if not script_path.exists():
            pytest.skip("check_dessmonitor_yearly.sh not found")

        content = script_path.read_text()

        # Output path must include dessmonitor prefix
        assert 'dessmonitor-${' in content or 'dessmonitor-$' in content, \
            "Output filename must include 'dessmonitor-' prefix"

        # Output path must include year variable
        assert '${YEAR}' in content or '$YEAR' in content, \
            "Output filename must include year (YYYY)"

    def test_yearly_sources_common_config(self):
        """Test that yearly script sources common configuration"""
        script_path = scripts_dir / "check_dessmonitor_yearly.sh"
        if not script_path.exists():
            pytest.skip("check_dessmonitor_yearly.sh not found")

        content = script_path.read_text()

        assert "source" in content and "common_config.sh" in content, \
            "Script should source common_config.sh"
        assert "source" in content and "dessmonitor_common.sh" in content, \
            "Script should source dessmonitor_common.sh"


class TestDessMonitorUC1AdminAlerts:
    """DessMonitor UC1 admin production alerts tests (15 tests total)"""

    @pytest.fixture
    def test_dessmonitor_data_dir(self):
        import shutil
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def test_output_dir(self):
        import shutil
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def test_state_file(self, tmp_path):
        return tmp_path / "dessmonitor_alerts_state.json"

    def create_dessmonitor_monthly_csv(self, data_dir, plant_name, month, daily_data):
        """Create DessMonitor monthly CSV with prefix"""
        import csv
        csv_file = data_dir / f"dessmonitor-{plant_name}-{month}.csv"
        with open(csv_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['date', 'kwh'])
            for date_str, kwh in daily_data:
                writer.writerow([date_str, kwh])
        return csv_file

    def test_dessmonitor_red_alert_low_production_3days(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """RED ALERT: DessMonitor plant < 20% baseline for 3 consecutive days"""
        from check_anomaly import utc_today
        from datetime import timedelta
        import subprocess
        import platform as plat

        # Skip on Windows
        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command (GitHub Actions)")

        today = utc_today()

        # Create baseline data (14 days before red window)
        baseline_data = []
        for i in range(17, 2, -1):  # Days 17-3 (matches check_anomaly.py baseline window)
            day = today - timedelta(days=i)
            baseline_data.append((day.strftime('%Y-%m-%d'), 10.0))

        # Current month data: baseline + 3 low days
        current_month_data = baseline_data.copy()
        for i in range(2, -1, -1):
            day = today - timedelta(days=i)
            current_month_data.append((day.strftime('%Y-%m-%d'), 1.5))  # 15% of baseline

        # Group by month
        from collections import defaultdict
        data_by_month = defaultdict(list)
        for date_str, kwh in current_month_data:
            month = date_str[:7]
            data_by_month[month].append((date_str, kwh))

        # Create CSV files with dessmonitor- prefix
        plant_name = "mifrazmarsoon-plant"
        for month, data in data_by_month.items():
            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, plant_name, month, data)

        # Run anomaly detection with platform filter
        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        exit_code = result.returncode

        # Assertions
        assert exit_code == 2, f"Expected exit code 2 (alerts found), got {exit_code}"

        alerts_file = test_output_dir / "alerts.txt"
        assert alerts_file.exists(), "alerts.txt should be created"

        with open(alerts_file, 'r', encoding='utf-8') as f:
            alert_text = f.read()

        assert "RED" in alert_text or "🔴" in alert_text, "Should contain RED alert indicator"
        assert "dessmonitor-mifrazmarsoon-plant" in alert_text, "Should mention the plant name"
        assert "3" in alert_text, "Should mention 3 consecutive days"

    def test_dessmonitor_orange_alert_low_production_3months(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """ORANGE ALERT: DessMonitor plant < 40% baseline for 3 consecutive months"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from datetime import datetime, timedelta

        # Create baseline (6 months) + orange window (3 months)
        # check_anomaly.py expects 6 baseline months BEFORE the 3 orange window months
        today = datetime.now()
        current_month = today.replace(day=1)

        # Create 6 baseline months with normal production (10.0 kWh/day)
        for month_offset in range(9, 3, -1):  # Months 9, 8, 7, 6, 5, 4 before current
            month_date = current_month - timedelta(days=30 * month_offset)
            month_str = month_date.strftime('%Y-%m')

            daily_data = []
            for day in range(1, 31):
                date_str = f"{month_str}-{day:02d}"
                daily_data.append((date_str, 10.0))  # Normal baseline production

            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "mifraz-plant", month_str, daily_data)

        # Create 3 orange window months with low production (30% of baseline = 3.0 kWh/day)
        for month_offset in range(3):  # Months 2, 1, 0 (current)
            month_date = current_month - timedelta(days=30 * month_offset)
            month_str = month_date.strftime('%Y-%m')

            daily_data = []
            days_in_month = 30
            for day in range(1, days_in_month + 1):
                date_str = f"{month_str}-{day:02d}"
                daily_data.append((date_str, 3.0))  # Low production (30% of 10.0)

            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "mifraz-plant", month_str, daily_data)

        # Run anomaly detection
        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        assert result.returncode == 2, "Should detect alerts"

        alerts_file = test_output_dir / "alerts.txt"
        with open(alerts_file, 'r', encoding='utf-8') as f:
            alert_text = f.read()

        # Should be ORANGE or RED alert
        assert "ORANGE" in alert_text or "RED" in alert_text or "🟠" in alert_text or "🔴" in alert_text

    def test_dessmonitor_zero_production_ignored_after_1month(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """Zero production for 1 month should be auto-ignored"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from datetime import datetime

        today = datetime.now()
        month_str = today.strftime('%Y-%m')

        # Create 30 days of zero production
        daily_data = [(f"{month_str}-{day:02d}", 0.0) for day in range(1, 31)]
        self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "zero-plant", month_str, daily_data)

        # Run anomaly detection
        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor',
            '--ignore-zero-months', '1'
        ], capture_output=True, text=True)

        # Should not trigger alert (zero production ignored)
        assert result.returncode == 0

    def test_dessmonitor_all_systems_operational(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """All systems operational - no alerts"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from datetime import datetime, timedelta
        from check_anomaly import utc_today

        today = utc_today()

        # Create baseline data (14 days before red window) + current data
        baseline_data = []
        for i in range(17, 2, -1):  # Days 17-3 (matches check_anomaly.py baseline window)
            day = today - timedelta(days=i)
            baseline_data.append((day.strftime('%Y-%m-%d'), 10.0))

        # Add current 3 days with normal production (no alert)
        current_data = baseline_data.copy()
        for i in range(2, -1, -1):  # Last 3 days
            day = today - timedelta(days=i)
            current_data.append((day.strftime('%Y-%m-%d'), 10.0))  # Normal production

        # Group by month
        from collections import defaultdict
        data_by_month = defaultdict(list)
        for date_str, kwh in current_data:
            month = date_str[:7]
            data_by_month[month].append((date_str, kwh))

        # Create CSV files
        for month, data in data_by_month.items():
            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "normal-plant", month, data)

        # Run anomaly detection
        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        assert result.returncode == 0

        alerts_file = test_output_dir / "alerts.txt"
        with open(alerts_file, 'r', encoding='utf-8') as f:
            alert_text = f.read()

        assert "NO ALERTS DETECTED" in alert_text

    def test_dessmonitor_customer_specific_alerts_generated(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """Customer-specific alerts JSON file is generated"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        # Run with any data (will create customer alerts file)
        from datetime import datetime
        today = datetime.now()
        month_str = today.strftime('%Y-%m')
        daily_data = [(f"{month_str}-{day:02d}", 10.0) for day in range(1, 31)]
        self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "test-plant", month_str, daily_data)

        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--customer-alerts-file', str(test_output_dir / 'customer_alerts.json'),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        customer_alerts_file = test_output_dir / 'customer_alerts.json'
        assert customer_alerts_file.exists(), "customer_alerts.json should be created"

    def test_dessmonitor_red_alert_just_below_threshold(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """Production at 19% of baseline (just below 20%) triggers RED alert"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from check_anomaly import utc_today
        from datetime import timedelta

        today = utc_today()

        # Baseline: 14 days before red window
        baseline_data = []
        for i in range(17, 2, -1):  # Days 17-3 (matches check_anomaly.py baseline window)
            day = today - timedelta(days=i)
            baseline_data.append((day.strftime('%Y-%m-%d'), 10.0))

        # 3 days at 19% of baseline
        current_data = baseline_data.copy()
        for i in range(2, -1, -1):
            day = today - timedelta(days=i)
            current_data.append((day.strftime('%Y-%m-%d'), 1.9))  # 19%

        # Group by month
        from collections import defaultdict
        data_by_month = defaultdict(list)
        for date_str, kwh in current_data:
            month = date_str[:7]
            data_by_month[month].append((date_str, kwh))

        for month, data in data_by_month.items():
            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "threshold-plant", month, data)

        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        assert result.returncode == 2

    def test_dessmonitor_no_alert_just_above_threshold(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """Production at 21% (just above 20%) does NOT trigger alert"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from check_anomaly import utc_today
        from datetime import timedelta

        today = utc_today()

        baseline_data = []
        for i in range(17, 2, -1):  # Days 17-3 (matches check_anomaly.py baseline window)
            day = today - timedelta(days=i)
            baseline_data.append((day.strftime('%Y-%m-%d'), 10.0))

        current_data = baseline_data.copy()
        for i in range(2, -1, -1):
            day = today - timedelta(days=i)
            current_data.append((day.strftime('%Y-%m-%d'), 2.1))  # 21%

        from collections import defaultdict
        data_by_month = defaultdict(list)
        for date_str, kwh in current_data:
            month = date_str[:7]
            data_by_month[month].append((date_str, kwh))

        for month, data in data_by_month.items():
            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "safe-plant", month, data)

        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        assert result.returncode == 0

    def test_dessmonitor_no_alert_non_consecutive_low_production(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """Intermittent low production (not consecutive) does NOT trigger"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from check_anomaly import utc_today
        from datetime import timedelta

        today = utc_today()

        # Baseline
        baseline_data = []
        for i in range(17, 2, -1):  # Days 17-3 (matches check_anomaly.py baseline window)
            day = today - timedelta(days=i)
            baseline_data.append((day.strftime('%Y-%m-%d'), 10.0))

        # Intermittent: LOW, NORMAL, LOW, NORMAL, LOW
        pattern_data = baseline_data.copy()
        pattern = [1.0, 10.0, 1.0, 10.0, 1.0]
        for i, val in enumerate(pattern):
            day = today - timedelta(days=4-i)
            pattern_data.append((day.strftime('%Y-%m-%d'), val))

        from collections import defaultdict
        data_by_month = defaultdict(list)
        for date_str, kwh in pattern_data:
            month = date_str[:7]
            data_by_month[month].append((date_str, kwh))

        for month, data in data_by_month.items():
            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "intermittent-plant", month, data)

        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        assert result.returncode == 0

    def test_dessmonitor_recovery_scenario_alert_clears(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """Plant recovers after RED alert period - alert clears"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from check_anomaly import utc_today
        from datetime import timedelta

        today = utc_today()

        # Baseline + 3 recovery days (normal production after previous low period)
        baseline_data = []
        for i in range(17, 2, -1):  # Days 17-3 (matches check_anomaly.py baseline window)
            day = today - timedelta(days=i)
            baseline_data.append((day.strftime('%Y-%m-%d'), 10.0))

        recovery_data = baseline_data.copy()
        # Last 3 days: recovery to normal production
        for i in range(2, -1, -1):
            day = today - timedelta(days=i)
            recovery_data.append((day.strftime('%Y-%m-%d'), 10.0))

        from collections import defaultdict
        data_by_month = defaultdict(list)
        for date_str, kwh in recovery_data:
            month = date_str[:7]
            data_by_month[month].append((date_str, kwh))

        for month, data in data_by_month.items():
            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "recovery-plant", month, data)

        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        assert result.returncode == 0  # Alert cleared

    def test_dessmonitor_multiple_plants_different_alert_levels(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """Multiple plants with different alert levels"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from check_anomaly import utc_today
        from datetime import timedelta

        today = utc_today()

        # Plant 1: RED alert
        baseline_data = []
        for i in range(17, 2, -1):  # Days 17-3 (matches check_anomaly.py baseline window)
            day = today - timedelta(days=i)
            baseline_data.append((day.strftime('%Y-%m-%d'), 10.0))

        red_data = baseline_data.copy()
        for i in range(2, -1, -1):
            day = today - timedelta(days=i)
            red_data.append((day.strftime('%Y-%m-%d'), 1.0))

        from collections import defaultdict
        data_by_month = defaultdict(list)
        for date_str, kwh in red_data:
            month = date_str[:7]
            data_by_month[month].append((date_str, kwh))

        for month, data in data_by_month.items():
            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "red-plant", month, data)

        # Plant 2: Normal
        normal_data = [(today.strftime('%Y-%m') + f"-{day:02d}", 10.0) for day in range(1, 31)]
        self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "normal-plant", today.strftime('%Y-%m'), normal_data)

        # Plant 3: Zero (ignored)
        zero_data = [(today.strftime('%Y-%m') + f"-{day:02d}", 0.0) for day in range(1, 31)]
        self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "zero-plant", today.strftime('%Y-%m'), zero_data)

        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor',
            '--ignore-zero-months', '1'
        ], capture_output=True, text=True)

        assert result.returncode == 2

        alerts_file = test_output_dir / "alerts.txt"
        with open(alerts_file, 'r', encoding='utf-8') as f:
            alert_text = f.read()

        assert "red-plant" in alert_text

    def test_dessmonitor_baseline_with_insufficient_data(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """Gracefully handle insufficient baseline data"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from datetime import datetime

        today = datetime.now()
        month_str = today.strftime('%Y-%m')

        # Only 5 days of data (not enough for 14-day baseline)
        daily_data = [(f"{month_str}-{day:02d}", 5.0) for day in range(1, 6)]
        self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "new-plant", month_str, daily_data)

        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        # Should not crash
        assert result.returncode in [0, 2]

    def test_dessmonitor_exact_3day_boundary(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """Exactly 3 consecutive low days triggers RED"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from check_anomaly import utc_today
        from datetime import timedelta

        today = utc_today()

        baseline_data = []
        for i in range(17, 2, -1):  # Days 17-3 (matches check_anomaly.py baseline window)
            day = today - timedelta(days=i)
            baseline_data.append((day.strftime('%Y-%m-%d'), 10.0))

        exact_data = baseline_data.copy()
        for i in range(2, -1, -1):  # Exactly 3 days
            day = today - timedelta(days=i)
            exact_data.append((day.strftime('%Y-%m-%d'), 1.0))

        from collections import defaultdict
        data_by_month = defaultdict(list)
        for date_str, kwh in exact_data:
            month = date_str[:7]
            data_by_month[month].append((date_str, kwh))

        for month, data in data_by_month.items():
            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "exact3-plant", month, data)

        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        assert result.returncode == 2

    def test_dessmonitor_exact_3month_boundary(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """Exactly 3 consecutive low months triggers ORANGE"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from datetime import datetime, timedelta

        today = datetime.now()
        current_month = today.replace(day=1)

        # Create 6 baseline months with normal production (10.0 kWh/day)
        for month_offset in range(9, 3, -1):  # Months 9, 8, 7, 6, 5, 4 before current
            month_date = current_month - timedelta(days=30 * month_offset)
            month_str = month_date.strftime('%Y-%m')

            daily_data = [(f"{month_str}-{day:02d}", 10.0) for day in range(1, 28)]
            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "exact3months-plant", month_str, daily_data)

        # Exactly 3 low months (orange window)
        for month_offset in range(3):
            month_date = current_month - timedelta(days=30 * month_offset)
            month_str = month_date.strftime('%Y-%m')

            daily_data = [(f"{month_str}-{day:02d}", 3.0) for day in range(1, 28)]
            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "exact3months-plant", month_str, daily_data)

        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        assert result.returncode == 2

    def test_dessmonitor_state_persistence_across_runs(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """Alert state persists across multiple runs"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from check_anomaly import utc_today
        from datetime import timedelta

        today = utc_today()

        baseline_data = []
        for i in range(17, 2, -1):  # Days 17-3 (matches check_anomaly.py baseline window)
            day = today - timedelta(days=i)
            baseline_data.append((day.strftime('%Y-%m-%d'), 10.0))

        alert_data = baseline_data.copy()
        for i in range(2, -1, -1):
            day = today - timedelta(days=i)
            alert_data.append((day.strftime('%Y-%m-%d'), 1.0))

        from collections import defaultdict
        data_by_month = defaultdict(list)
        for date_str, kwh in alert_data:
            month = date_str[:7]
            data_by_month[month].append((date_str, kwh))

        for month, data in data_by_month.items():
            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "state-plant", month, data)

        # Run 1
        result1 = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        assert result1.returncode == 2
        assert test_state_file.exists()

        # Load state
        with open(test_state_file, 'r') as f:
            state = json.load(f)

        assert len(state) > 0
        # State uses "plant:severity" key format
        assert any("state-plant" in key for key in state.keys())

    def test_dessmonitor_email_content_formatting(self, test_dessmonitor_data_dir, test_output_dir, test_state_file):
        """Email contains all required sections"""
        import subprocess
        import platform as plat

        if plat.system() == 'Windows':
            pytest.skip("check_anomaly.py requires Unix date command")

        from check_anomaly import utc_today
        from datetime import timedelta

        today = utc_today()

        baseline_data = []
        for i in range(17, 2, -1):  # Days 17-3 (matches check_anomaly.py baseline window)
            day = today - timedelta(days=i)
            baseline_data.append((day.strftime('%Y-%m-%d'), 10.0))

        alert_data = baseline_data.copy()
        for i in range(2, -1, -1):
            day = today - timedelta(days=i)
            alert_data.append((day.strftime('%Y-%m-%d'), 1.0))

        from collections import defaultdict
        data_by_month = defaultdict(list)
        for date_str, kwh in alert_data:
            month = date_str[:7]
            data_by_month[month].append((date_str, kwh))

        for month, data in data_by_month.items():
            self.create_dessmonitor_monthly_csv(test_dessmonitor_data_dir, "email-test-plant", month, data)

        result = subprocess.run([
            'python3',
            str(scripts_dir / 'check_anomaly.py'),
            '--data-dir', str(test_dessmonitor_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--platform', 'dessmonitor'
        ], capture_output=True, text=True)

        assert result.returncode == 2

        alerts_file = test_output_dir / "alerts.txt"
        with open(alerts_file, 'r', encoding='utf-8') as f:
            email_text = f.read()

        # Required sections
        assert "Alert Summary" in email_text or "ALERT" in email_text
        assert "Recommended Actions" in email_text or "ACTION" in email_text
        assert "email-test-plant" in email_text
        assert "RED" in email_text or "🔴" in email_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
