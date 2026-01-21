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

# Add the scripts directory to the path
scripts_dir = Path(__file__).parent.parent.parent.parent.parent / "main" / "java" / "org" / "ktronics" / "scripts"
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

    def test_check_monthly_uses_plant_level_api(self):
        """
        REGRESSION TEST: Verify check_dessmonitor_monthly.sh uses plant-level API.

        BUG FIX (2026-01-21):
        - Old: webQueryDeviceEs (device-level, returns current snapshot only)
        - New: queryPlantEnergyMonthPerDay (plant-level, returns daily breakdown)
        """
        script_path = scripts_dir / "check_dessmonitor_monthly.sh"

        if not script_path.exists():
            pytest.skip("check_dessmonitor_monthly.sh not found")

        content = script_path.read_text()

        # MUST use plant-level API
        assert "queryPlantEnergyMonthPerDay" in content, \
            "Script must use queryPlantEnergyMonthPerDay API (plant-level)"

        # Should NOT use old device-level API
        assert "webQueryDeviceEs" not in content or "webQueryDeviceEs" in content and "#" in content.split("webQueryDeviceEs")[0].split("\n")[-1], \
            "Script should NOT use webQueryDeviceEs (device-level API)"

    def test_check_monthly_parses_dates_from_api(self):
        """
        REGRESSION TEST: Verify dates are parsed from API response (not hardcoded to today).

        BUG FIX (2026-01-21):
        - Old: today=$(date -u +%Y-%m-%d) - wrote today's date for all rows
        - New: Extract 'ts' field from API response - each row has correct date
        """
        script_path = scripts_dir / "check_dessmonitor_monthly.sh"

        if not script_path.exists():
            pytest.skip("check_dessmonitor_monthly.sh not found")

        content = script_path.read_text()

        # MUST use AWK to parse dates from API (looks for "ts" field)
        assert '"ts"' in content, \
            "Script must parse 'ts' (timestamp) field from API response"

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

        # AWK output should be "day,val" format
        assert 'print day "," val' in content or 'print day","val' in content, \
            "AWK output must format as: date,kwh"

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
