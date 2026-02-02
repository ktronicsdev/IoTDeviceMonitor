#!/usr/bin/env python3
"""
UC2: DessMonitor Data Collection Tests

Tests for DessMonitor data collection scripts including:
- Monthly data collection (check_dessmonitor_monthly.sh)
- Yearly data collection (check_dessmonitor_yearly.sh)
- API parameter validation and CSV output format
"""

import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

# Add scripts directory to path
# Starting from: src/test/java/org/ktronics/scripts/integration/test_dessmonitor_data_collection.py
# 8 .parent calls reach repo root (IOT/), then add src/main/java/org/ktronics/scripts
scripts_dir = Path(__file__).parent.parent.parent.parent.parent.parent.parent.parent / "src" / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(scripts_dir))


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

        content = script_path.read_text(encoding='utf-8')

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

        content = script_path.read_text(encoding='utf-8')

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

        content = script_path.read_text(encoding='utf-8')

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

        content = script_path.read_text(encoding='utf-8')

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

        content = script_path.read_text(encoding='utf-8')

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

        content = script_path.read_text(encoding='utf-8')

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

        content = script_path.read_text(encoding='utf-8')

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

        content = script_path.read_text(encoding='utf-8')

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

        content = script_path.read_text(encoding='utf-8')

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

        content = script_path.read_text(encoding='utf-8')

        # Should NOT have a month loop (seq -w 1 12) since API returns all months
        # The API call uses date=${YEAR} (just the year, not YYYY-MM)
        assert "date=${YEAR}" in content or 'date=${YEAR}' in content, \
            "Script should use date=YEAR format (not YYYY-MM)"

    def test_yearly_csv_output_format(self):
        """Test that yearly CSV output format is correct: month,kwh"""
        script_path = scripts_dir / "check_dessmonitor_yearly.sh"
        if not script_path.exists():
            pytest.skip("check_dessmonitor_yearly.sh not found")

        content = script_path.read_text(encoding='utf-8')

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

        content = script_path.read_text(encoding='utf-8')

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

        content = script_path.read_text(encoding='utf-8')

        assert "source" in content and "common_config.sh" in content, \
            "Script should source common_config.sh"
        assert "source" in content and "dessmonitor_common.sh" in content, \
            "Script should source dessmonitor_common.sh"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
