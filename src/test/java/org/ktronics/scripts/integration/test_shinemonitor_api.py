#!/usr/bin/env python3
"""
Integration tests for ShineMonitor API shared functions
Tests authentication and API calls using shinemonitor_common.sh
"""

import pytest
import subprocess
import json
import tempfile
import platform
import sys
from pathlib import Path

# Add scripts directory to path to import centralized config
PROJECT_ROOT = Path(__file__).resolve().parents[7]
SCRIPTS_DIR = PROJECT_ROOT / "src" / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from config import CREDENTIALS_PATH

# Use centralized credentials path
CREDS_FILE = PROJECT_ROOT / CREDENTIALS_PATH

def load_test_credentials():
    """Load credentials for testing"""
    if not CREDS_FILE.exists():
        pytest.skip(f"Credentials file not found: {CREDS_FILE}")

    with open(CREDS_FILE) as f:
        data = json.load(f)

    return {
        'username': data['accounts'][0]['username'],
        'password': data['accounts'][0]['password'],
        'company_key': data['company_key']
    }


class TestShineMonitorAPI:
    """Test ShineMonitor API authentication and calls"""

    @pytest.fixture
    def test_creds(self):
        """Get test credentials"""
        return load_test_credentials()

    def run_bash_script(self, script_content, timeout=30):
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
        """Test shinemonitor_auth_email authentication function"""
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        if shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}"; then
            echo "AUTH_SUCCESS"
            echo "TOKEN:${{SM_TOKEN:0:16}}"
            echo "SECRET:${{SM_SECRET:0:16}}"
            exit 0
        else
            echo "AUTH_FAILED"
            exit 1
        fi
        """

        result = self.run_bash_script(script)

        assert result.returncode == 0, f"Authentication failed: {result.stderr}"
        assert "AUTH_SUCCESS" in result.stdout
        assert "TOKEN:" in result.stdout
        assert "SECRET:" in result.stdout

    def test_query_plants_api(self, test_creds):
        """Test queryPlants API call"""
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2

        response=$(shinemonitor_api_call "queryPlants" "")
        echo "$response"
        """

        result = self.run_bash_script(script)

        assert result.returncode == 0, f"API call failed: {result.stderr}"

        # Parse JSON response
        response = json.loads(result.stdout.strip())
        assert response['err'] == 0, f"API returned error: {response.get('desc')}"
        assert 'dat' in response
        assert 'total' in response['dat']

    def test_query_month_energy_api(self, test_creds):
        """Test queryPlantEnergyMonth API call"""
        # First get a plant ID
        get_plant_script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2

        plants=$(shinemonitor_api_call "queryPlants" "")
        echo "$plants" | grep -o '"pid":[0-9]*' | head -1 | grep -o '[0-9]*'
        """

        result = self.run_bash_script(get_plant_script)
        assert result.returncode == 0, "Failed to get plant ID"

        plant_id = result.stdout.strip()
        assert plant_id, "No plant ID found"

        # Now test monthly energy API
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2

        response=$(shinemonitor_api_call "queryPlantEnergyMonth" "plantid={plant_id}&date=2026-01")
        echo "$response"
        """

        result = self.run_bash_script(script)

        assert result.returncode == 0, f"API call failed: {result.stderr}"

        response = json.loads(result.stdout.strip())
        assert response['err'] == 0, f"API returned error: {response.get('desc')}"

    def test_query_year_energy_api(self, test_creds):
        """Test queryPlantEnergyYear API call"""
        # First get a plant ID
        get_plant_script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2

        plants=$(shinemonitor_api_call "queryPlants" "")
        echo "$plants" | grep -o '"pid":[0-9]*' | head -1 | grep -o '[0-9]*'
        """

        result = self.run_bash_script(get_plant_script)
        assert result.returncode == 0, "Failed to get plant ID"

        plant_id = result.stdout.strip()
        assert plant_id, "No plant ID found"

        # Now test yearly energy API
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2

        response=$(shinemonitor_api_call "queryPlantEnergyYear" "plantid={plant_id}&year=2026")
        echo "$response"
        """

        result = self.run_bash_script(script)

        assert result.returncode == 0, f"API call failed: {result.stderr}"

        response = json.loads(result.stdout.strip())
        assert response['err'] == 0, f"API returned error: {response.get('desc')}"

    def test_query_plants_warning_api(self, test_creds):
        """Test webQueryPlantsWarning API call (device alarms)"""
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2

        response=$(shinemonitor_api_call "webQueryPlantsWarning" "date=")
        echo "$response"
        """

        result = self.run_bash_script(script)

        assert result.returncode == 0, f"API call failed: {result.stderr}"

        response = json.loads(result.stdout.strip())
        assert response['err'] == 0, f"API returned error: {response.get('desc')}"
        assert 'dat' in response

    def test_check_device_alarms_script(self, test_creds):
        """Test check_device_alarms.sh end-to-end"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            output_file = f.name

        try:
            script = f"""
            set -euo pipefail
            cd "{SCRIPTS_DIR}"
            bash check_device_alarms.sh "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" "{output_file}"
            """

            result = self.run_bash_script(script)

            assert result.returncode == 0, f"Script failed: {result.stderr}"
            assert Path(output_file).exists(), "Output file not created"

            # Verify JSON output
            with open(output_file) as f:
                data = json.load(f)

            assert data['err'] == 0, f"API error in output: {data.get('desc')}"
            assert 'dat' in data

        finally:
            Path(output_file).unlink(missing_ok=True)

    def test_check_monthly_script(self):
        """Test check_shinemonitor_monthly.sh"""
        script = f"""
        set -euo pipefail
        cd "{SCRIPTS_DIR}"
        bash check_shinemonitor_monthly.sh "{CREDS_FILE}" "2026-01" 2>&1 | grep -E "(AUTH OK|DONE)"
        """

        # Use 90-second timeout (processes 20+ accounts)
        result = self.run_bash_script(script, timeout=90)

        assert result.returncode == 0, f"Monthly script failed: {result.stderr}"
        assert "AUTH OK" in result.stdout or "DONE" in result.stdout

    def test_check_yearly_script(self):
        """Test check_shinemonitor_yearly.sh"""
        script = f"""
        set -euo pipefail
        cd "{SCRIPTS_DIR}"
        timeout 60 bash check_shinemonitor_yearly.sh "{CREDS_FILE}" "2026" 2>&1 | head -20 | grep -E "(AUTH OK|DONE)"
        """

        # Use 90-second timeout (processes 20+ accounts)
        result = self.run_bash_script(script, timeout=90)

        # Allow timeout (124) and SIGPIPE (141 from head closing pipe early)
        assert result.returncode in [0, 124, 141], f"Yearly script failed: {result.stderr}"
        assert "AUTH OK" in result.stdout or "DONE" in result.stdout


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
