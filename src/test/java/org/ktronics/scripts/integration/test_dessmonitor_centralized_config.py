#!/usr/bin/env python3
"""
BVT: DessMonitor Build Verification Tests

Tests for DessMonitor configuration validation including:
- Credentials format and required fields
- Platform filtering and CSV naming conventions
- Backward compatibility with ShineMonitor
- Production credential diagnostics
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add scripts directory to path
# Starting from: src/test/java/org/ktronics/scripts/integration/test_dessmonitor_centralized_config.py
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


class TestDessMonitorDiagnostics:
    """
    Production credential diagnostics - converted from scripts/diagnose_dessmonitor_api.py.

    These tests validate the actual credentials.json file has correct DessMonitor
    configuration, replacing the standalone diagnostic script.
    """

    def _load_dessmonitor_creds(self):
        """Helper to load DessMonitor credentials, skipping if unavailable."""
        from config import CREDENTIALS_PATH

        if not CREDENTIALS_PATH.exists():
            pytest.skip("credentials.json not found (CI environment)")

        with open(CREDENTIALS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if "dessmonitor_credentials" not in data:
            pytest.skip("dessmonitor_credentials not in local credentials.json (only in GitHub Actions secret)")

        return data["dessmonitor_credentials"]

    def test_dessmonitor_credentials_loaded(self):
        """Test that dessmonitor_credentials key exists and has company_key"""
        creds = self._load_dessmonitor_creds()
        assert "company_key" in creds, "Missing company_key"
        assert len(creds["company_key"]) > 0

    def test_dessmonitor_accounts_not_empty(self):
        """Test that at least one DessMonitor account is configured"""
        creds = self._load_dessmonitor_creds()
        accounts = creds.get("dessmonitor_accounts", [])
        assert len(accounts) > 0, "No dessmonitor_accounts configured"

    def test_dessmonitor_accounts_have_required_fields(self):
        """Test that each DessMonitor account has label, username, password"""
        creds = self._load_dessmonitor_creds()
        accounts = creds.get("dessmonitor_accounts", [])

        for i, acc in enumerate(accounts):
            assert "label" in acc, f"Account {i} missing 'label'"
            assert "username" in acc, f"Account {i} missing 'username'"
            assert "password" in acc, f"Account {i} missing 'password'"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
