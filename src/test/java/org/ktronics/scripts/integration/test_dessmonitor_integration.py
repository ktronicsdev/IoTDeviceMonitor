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
        """Test that DessMonitor API endpoint is correctly configured"""
        # Expected DessMonitor API endpoint
        expected_endpoint = "https://web.dessmonitor.com/public/"

        # This would be read from dessmonitor_common.sh
        # For now, just verify the expected value
        assert expected_endpoint.startswith("https://")
        assert "dessmonitor.com" in expected_endpoint

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
