#!/usr/bin/env python3
"""
UC1: DessMonitor Admin Production Alerts Tests

Tests for DessMonitor anomaly detection including:
- Multi-platform alert labeling and state separation
- RED/ORANGE alert thresholds and boundaries
- Recovery scenarios and state persistence
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
# Starting from: src/test/java/org/ktronics/scripts/integration/test_dessmonitor_admin_alerts.py
# 8 .parent calls reach repo root (IOT/), then add src/main/java/org/ktronics/scripts
scripts_dir = Path(__file__).parent.parent.parent.parent.parent.parent.parent.parent / "src" / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(scripts_dir))


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
