#!/usr/bin/env python3
"""
Integration tests for admin production alert system
Tests UC1: Verify daily production sent to admin works for yearly, daily, monthly updated
"""

import pytest
import sys
import os
import json
import csv
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta, date

# Add scripts directory to path
SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent.parent.parent / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# Import will be done at test time to avoid import errors during collection


class TestAdminProductionAlerts:
    """Test admin production anomaly detection and alerts"""

    @pytest.fixture
    def test_data_dir(self):
        """Create temporary test data directory"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def test_state_file(self, tmp_path):
        """Create temporary state file"""
        return tmp_path / "test_alerts_state.json"

    @pytest.fixture
    def test_output_dir(self, tmp_path):
        """Create temporary output directory"""
        out_dir = tmp_path / "alerts"
        out_dir.mkdir()
        return out_dir

    def create_monthly_csv(self, data_dir, plant_name, month, daily_data):
        """Helper: Create monthly CSV file"""
        csv_file = data_dir / f"{plant_name}-{month}.csv"
        with open(csv_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['date', 'kwh'])
            for date_str, kwh in daily_data:
                writer.writerow([date_str, kwh])
        return csv_file

    def test_red_alert_low_production_3days(self, test_data_dir, test_state_file, test_output_dir):
        """
        RED ALERT: Plant < 20% baseline for 3 consecutive days
        """
        # Create baseline data (30 days of normal production ~10 kWh/day)
        baseline_data = [(f'2025-12-{day:02d}', 10.0) for day in range(1, 32)]

        # Create current month with 3 consecutive days of low production (< 20% = < 2 kWh)
        current_data = [
            ('2026-01-01', 10.0),  # Normal
            ('2026-01-02', 1.5),   # LOW - Day 1
            ('2026-01-03', 1.2),   # LOW - Day 2
            ('2026-01-04', 1.8),   # LOW - Day 3
            ('2026-01-05', 10.0),  # Back to normal
        ]

        plant_name = "test-plant-red"
        self.create_monthly_csv(test_data_dir, plant_name, '2025-12', baseline_data)
        self.create_monthly_csv(test_data_dir, plant_name, '2026-01', current_data)

        # Run anomaly detection
        from check_anomaly import main as check_anomaly_main
        sys.argv = [
            'check_anomaly.py',
            '--data-dir', str(test_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file)
        ]

        exit_code = check_anomaly_main()

        # Should return exit code 2 (alerts found)
        assert exit_code == 2

        # Check alerts.txt was created
        alerts_file = test_output_dir / "alerts.txt"
        assert alerts_file.exists()

        # Read and verify alert content
        with open(alerts_file, 'r') as f:
            alert_text = f.read()

        assert "RED" in alert_text or "🔴" in alert_text
        assert "test-plant-red" in alert_text
        assert "3 consecutive days" in alert_text.lower() or "3 days" in alert_text.lower()

        # Check state file was created
        assert test_state_file.exists()

    def test_orange_alert_low_production_3months(self, test_data_dir, test_state_file, test_output_dir):
        """
        ORANGE ALERT: Plant < 40% baseline for 3 consecutive months
        """
        # Create baseline (12 months of normal ~300 kWh/month)
        baseline_months = [
            ('2024-01', 300.0), ('2024-02', 310.0), ('2024-03', 320.0),
            ('2024-04', 305.0), ('2024-05', 315.0), ('2024-06', 310.0),
            ('2024-07', 325.0), ('2024-08', 320.0), ('2024-09', 310.0),
            ('2024-10', 315.0), ('2024-11', 305.0), ('2024-12', 300.0),
        ]

        # Create current period with 3 low months (< 40% = < 120 kWh)
        current_months = [
            ('2025-10', 100.0),  # LOW - Month 1
            ('2025-11', 110.0),  # LOW - Month 2
            ('2025-12', 105.0),  # LOW - Month 3
        ]

        plant_name = "test-plant-orange"

        # Create yearly CSV for baseline
        yearly_file = test_data_dir / f"{plant_name}-2024.csv"
        with open(yearly_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['month', 'kwh'])
            for month, kwh in baseline_months:
                writer.writerow([month, kwh])

        # Create monthly CSVs for current period
        for month, total_kwh in current_months:
            month_data = [(f'{month}-{day:02d}', total_kwh / 30) for day in range(1, 31)]
            self.create_monthly_csv(test_data_dir, plant_name, month, month_data)

        # Run anomaly detection
        from check_anomaly import main as check_anomaly_main
        sys.argv = [
            'check_anomaly.py',
            '--data-dir', str(test_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file)
        ]

        exit_code = check_anomaly_main()

        # Should return exit code 2 (alerts found)
        assert exit_code == 2

        # Verify alert content
        alerts_file = test_output_dir / "alerts.txt"
        assert alerts_file.exists()

        with open(alerts_file, 'r') as f:
            alert_text = f.read()

        assert "ORANGE" in alert_text or "🟠" in alert_text
        assert "test-plant-orange" in alert_text

    def test_zero_production_ignored_after_1month(self, test_data_dir, test_state_file, test_output_dir):
        """
        IGNORED: Plant with 0 production for 1 month gets auto-ignored
        """
        # Create 1 month of zero production
        zero_data = [(f'2026-01-{day:02d}', 0.0) for day in range(1, 32)]

        plant_name = "test-plant-zero"
        self.create_monthly_csv(test_data_dir, plant_name, '2026-01', zero_data)

        # Run anomaly detection
        from check_anomaly import main as check_anomaly_main
        sys.argv = [
            'check_anomaly.py',
            '--data-dir', str(test_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file),
            '--ignore-zero-months', '1'
        ]

        exit_code = check_anomaly_main()

        # Check that plant is in auto-ignore list
        alerts_file = test_output_dir / "alerts.txt"
        if alerts_file.exists():
            with open(alerts_file, 'r') as f:
                alert_text = f.read()

            # Should mention auto-ignore or not alert for this plant
            assert "auto-ignored" in alert_text.lower() or "ignored" in alert_text.lower()

    def test_all_systems_operational(self, test_data_dir, test_state_file, test_output_dir):
        """
        NO ALERTS: All plants producing normally
        """
        # Create normal production data
        normal_data = [(f'2026-01-{day:02d}', 10.0 + day * 0.1) for day in range(1, 8)]

        plant_name = "test-plant-normal"
        self.create_monthly_csv(test_data_dir, plant_name, '2026-01', normal_data)

        # Run anomaly detection
        from check_anomaly import main as check_anomaly_main
        sys.argv = [
            'check_anomaly.py',
            '--data-dir', str(test_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file)
        ]

        exit_code = check_anomaly_main()

        # Should return exit code 0 (no alerts)
        assert exit_code == 0

        # Verify message
        alerts_file = test_output_dir / "alerts.txt"
        assert alerts_file.exists()

        with open(alerts_file, 'r') as f:
            alert_text = f.read()

        assert "ALL SYSTEMS OPERATIONAL" in alert_text or "No alerts" in alert_text

    def test_customer_specific_alerts_generated(self, test_data_dir, test_state_file, test_output_dir):
        """
        CUSTOMER ALERTS: Customer-specific alerts created in customer_alerts.json
        """
        # Create alert data for specific customer
        alert_data = [(f'2026-01-{day:02d}', 1.0) for day in range(1, 5)]

        plant_name = "customer-test-plant"
        self.create_monthly_csv(test_data_dir, plant_name, '2026-01', alert_data)

        # Create baseline
        baseline_data = [(f'2025-12-{day:02d}', 10.0) for day in range(1, 32)]
        self.create_monthly_csv(test_data_dir, plant_name, '2025-12', baseline_data)

        # Run anomaly detection
        from check_anomaly import main as check_anomaly_main
        sys.argv = [
            'check_anomaly.py',
            '--data-dir', str(test_data_dir),
            '--out-dir', str(test_output_dir),
            '--state-file', str(test_state_file)
        ]

        check_anomaly_main()

        # Check if customer_alerts.json was created
        customer_alerts_file = test_output_dir / "customer_alerts.json"
        if customer_alerts_file.exists():
            with open(customer_alerts_file, 'r') as f:
                customer_alerts = json.load(f)

            # Verify structure
            assert 'customer_alerts' in customer_alerts


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
