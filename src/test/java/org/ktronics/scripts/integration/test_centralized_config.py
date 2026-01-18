#!/usr/bin/env python3
"""
Build Verification Test (BVT) for Centralized Configuration

Tests that all scripts use the centralized credentials.json path configuration.
This prevents regressions where scripts might hardcode paths instead of using
the centralized config module.
"""

import pytest
import sys
from pathlib import Path

# Add scripts directory to path
PROJECT_ROOT = Path(__file__).resolve().parents[7]
SCRIPTS_DIR = PROJECT_ROOT / "src" / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from config import CREDENTIALS_PATH


class TestCentralizedConfig:
    """BVT tests for centralized configuration"""

    def test_config_module_exists(self):
        """BVT: config.py module exists and is importable"""
        config_file = SCRIPTS_DIR / "config.py"
        assert config_file.exists(), "config.py module must exist"

    def test_credentials_path_defined(self):
        """BVT: CREDENTIALS_PATH constant is defined in config module"""
        assert CREDENTIALS_PATH is not None, "CREDENTIALS_PATH must be defined"
        assert isinstance(CREDENTIALS_PATH, Path), "CREDENTIALS_PATH must be a Path object"

    def test_credentials_path_value(self):
        """BVT: CREDENTIALS_PATH points to correct location"""
        expected_path = Path("src/main/java/org/ktronics/config/credentials.json")
        assert CREDENTIALS_PATH == expected_path, \
            f"CREDENTIALS_PATH must be {expected_path}, got {CREDENTIALS_PATH}"

    def test_check_anomaly_uses_centralized_config(self):
        """BVT: check_anomaly.py imports from centralized config"""
        check_anomaly_file = SCRIPTS_DIR / "check_anomaly.py"
        content = check_anomaly_file.read_text(encoding='utf-8')

        # Verify it imports from config
        assert "from config import CREDENTIALS_PATH" in content, \
            "check_anomaly.py must import CREDENTIALS_PATH from config module"

        # Verify it uses CREDENTIALS_PATH as default (UC10 Phase 2: supports --credentials CLI arg)
        # The code now has: credentials_path = Path(args.credentials) if args.credentials else CREDENTIALS_PATH
        assert "CREDENTIALS_PATH" in content, \
            "check_anomaly.py must use CREDENTIALS_PATH as default"

        # Verify NO hardcoded path
        assert 'Path("src/main/java/org/ktronics/config/credentials.json")' not in content, \
            "check_anomaly.py must NOT hardcode credentials path"

    def test_generate_device_alarms_uses_centralized_config(self):
        """BVT: generate_device_alarms.py imports from centralized config"""
        script_file = SCRIPTS_DIR / "generate_device_alarms.py"
        content = script_file.read_text(encoding='utf-8')

        assert "from config import CREDENTIALS_PATH" in content, \
            "generate_device_alarms.py must import CREDENTIALS_PATH from config module"

        assert "credentials_file = CREDENTIALS_PATH" in content, \
            "generate_device_alarms.py must use CREDENTIALS_PATH variable"

        assert 'Path("src/main/java/org/ktronics/config/credentials.json")' not in content, \
            "generate_device_alarms.py must NOT hardcode credentials path"

    def test_generate_weekly_report_uses_centralized_config(self):
        """BVT: generate_weekly_report.py imports from centralized config"""
        script_file = SCRIPTS_DIR / "generate_weekly_report.py"
        content = script_file.read_text(encoding='utf-8')

        assert "from config import CREDENTIALS_PATH" in content, \
            "generate_weekly_report.py must import CREDENTIALS_PATH from config module"

        assert "default=str(CREDENTIALS_PATH)" in content, \
            "generate_weekly_report.py must use CREDENTIALS_PATH as default"

        assert 'required=True' not in content or 'credentials' not in content, \
            "generate_weekly_report.py credentials arg must have default (not required)"

    def test_generate_admin_summary_uses_centralized_config(self):
        """BVT: generate_admin_summary.py imports from centralized config"""
        script_file = SCRIPTS_DIR / "generate_admin_summary.py"
        content = script_file.read_text(encoding='utf-8')

        assert "from config import CREDENTIALS_PATH" in content, \
            "generate_admin_summary.py must import CREDENTIALS_PATH from config module"

        assert "default=str(CREDENTIALS_PATH)" in content, \
            "generate_admin_summary.py must use CREDENTIALS_PATH as default"

    def test_bash_common_config_exists(self):
        """BVT: common_config.sh exists for bash scripts"""
        common_config_file = SCRIPTS_DIR / "common_config.sh"
        assert common_config_file.exists(), "common_config.sh must exist for bash scripts"

        content = common_config_file.read_text(encoding='utf-8')
        assert "CREDENTIALS_FILE=" in content, \
            "common_config.sh must define CREDENTIALS_FILE variable"

        assert 'src/main/java/org/ktronics/config/credentials.json' in content, \
            "common_config.sh must point to correct credentials path"

    def test_bash_scripts_source_common_config(self):
        """BVT: All bash scripts source common_config.sh"""
        bash_scripts = [
            "check_shinemonitor_daily.sh",
            "check_shinemonitor_monthly.sh",
            "check_shinemonitor_yearly.sh"
        ]

        for script_name in bash_scripts:
            script_file = SCRIPTS_DIR / script_name
            if not script_file.exists():
                continue  # Skip if script doesn't exist

            content = script_file.read_text(encoding='utf-8')
            assert 'source "$SCRIPT_DIR/common_config.sh"' in content, \
                f"{script_name} must source common_config.sh"

            assert '"${1:-$CREDENTIALS_FILE}"' in content or '"${1:-' in content, \
                f"{script_name} must use CREDENTIALS_FILE as default"

    def test_no_hardcoded_credentials_paths_in_scripts(self):
        """BVT: Verify NO scripts hardcode credentials.json path"""
        python_scripts = [
            "check_anomaly.py",
            "generate_device_alarms.py",
            "generate_weekly_report.py",
            "generate_admin_summary.py"
        ]

        hardcoded_pattern = 'Path("src/main/java/org/ktronics/config/credentials.json")'

        for script_name in python_scripts:
            script_file = SCRIPTS_DIR / script_name
            if not script_file.exists():
                continue

            content = script_file.read_text(encoding='utf-8')

            # Count occurrences (should be 0 after using centralized config)
            count = content.count(hardcoded_pattern)
            assert count == 0, \
                f"{script_name} has {count} hardcoded path(s) - must use centralized config"

    def test_test_files_use_centralized_config(self):
        """BVT: Test files also use centralized config"""
        test_file = Path(__file__).parent / "test_shinemonitor_api.py"
        if not test_file.exists():
            pytest.skip("test_shinemonitor_api.py not found")

        content = test_file.read_text(encoding='utf-8')

        assert "from config import CREDENTIALS_PATH" in content, \
            "test_shinemonitor_api.py must import CREDENTIALS_PATH from config module"

        assert "CREDS_FILE = PROJECT_ROOT / CREDENTIALS_PATH" in content, \
            "test_shinemonitor_api.py must construct path from CREDENTIALS_PATH"

    def test_credentials_file_exists(self):
        """BVT: Actual credentials.json file exists at configured path"""
        credentials_file = PROJECT_ROOT / CREDENTIALS_PATH
        assert credentials_file.exists(), \
            f"credentials.json must exist at {credentials_file}"

    def test_credentials_file_is_valid_json(self):
        """BVT: credentials.json is valid JSON format"""
        import json

        credentials_file = PROJECT_ROOT / CREDENTIALS_PATH

        try:
            with open(credentials_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            assert isinstance(data, dict), "credentials.json must contain a JSON object"
            assert 'accounts' in data, "credentials.json must have 'accounts' key"
            assert isinstance(data['accounts'], list), "'accounts' must be a list"

        except json.JSONDecodeError as e:
            pytest.fail(f"credentials.json is not valid JSON: {e}")

    def test_single_source_of_truth(self):
        """BVT: Only config.py and common_config.sh define credentials path"""
        # These are the ONLY files allowed to define the path
        allowed_files = {
            "config.py",
            "common_config.sh"
        }

        # Check all Python scripts
        for py_file in SCRIPTS_DIR.glob("*.py"):
            if py_file.name in allowed_files:
                continue  # Skip the config files themselves

            content = py_file.read_text(encoding='utf-8')

            # Should NOT define the path directly
            assert 'CREDENTIALS_PATH = Path(' not in content, \
                f"{py_file.name} must NOT define CREDENTIALS_PATH directly"

            assert 'credentials_path = Path("src/main' not in content, \
                f"{py_file.name} must NOT hardcode credentials path"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
