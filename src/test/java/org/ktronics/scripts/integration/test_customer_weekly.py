#!/usr/bin/env python3
"""
Integration tests for customer weekly report generation
Tests UC2: Verify customer weekly report sent and all values are correct
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

from generate_weekly_report import get_weekly_summary, format_weekly_email, load_credentials, get_customer_plants


class TestCustomerWeeklyReports:
    """Test customer weekly report generation and calculations"""

    @pytest.fixture
    def test_data_dir(self):
        """Create temporary test data directory"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def test_credentials_file(self, tmp_path):
        """Create test credentials file"""
        creds = {
            "company_key": "test-key",
            "accounts": [
                {
                    "label": "Test Customer",
                    "username": "test@test.com",
                    "password": "testpass",
                    "email": "customer@example.com"
                }
            ]
        }
        creds_file = tmp_path / "credentials.json"
        with open(creds_file, 'w') as f:
            json.dump(creds, f)
        return creds_file

    def create_monthly_csv(self, data_dir, plant_name, month, daily_data):
        """Helper: Create monthly CSV file with daily data"""
        csv_file = data_dir / f"{plant_name}-{month}.csv"
        with open(csv_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['date', 'kwh'])
            for date_str, kwh in daily_data:
                writer.writerow([date_str, kwh])
        return csv_file

    def create_yearly_csv(self, data_dir, plant_name, year, monthly_data):
        """Helper: Create yearly CSV file with monthly data"""
        csv_file = data_dir / f"{plant_name}-{year}.csv"
        with open(csv_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['month', 'kwh'])
            for month_str, kwh in monthly_data:
                writer.writerow([month_str, kwh])
        return csv_file

    def test_csv_column_names_kwh_not_energy_kwh(self, test_data_dir):
        """
        CRITICAL BUG FIX TEST
        Verify that CSV files use 'kwh' column (not 'energy_kwh')
        This was causing all values to show 0.00 kWh
        """
        # Create test CSV with actual column name 'kwh'
        plant_name = "test-plant"
        daily_data = [
            ('2026-01-01', 10.5),
            ('2026-01-02', 11.2),
            ('2026-01-03', 9.8),
        ]

        csv_file = self.create_monthly_csv(test_data_dir, plant_name, '2026-01', daily_data)

        # Verify CSV has 'kwh' column
        with open(csv_file, 'r') as f:
            reader = csv.DictReader(f)
            assert 'kwh' in reader.fieldnames
            assert 'energy_kwh' not in reader.fieldnames

            # Verify data can be read with 'kwh' column
            rows = list(reader)
            assert len(rows) == 3
            assert float(rows[0]['kwh']) == 10.5

    def test_weekly_production_calculation(self, test_data_dir):
        """Calculate correct weekly totals from daily CSV"""
        today = datetime(2026, 1, 7)  # Tuesday
        week_ago = today - timedelta(days=7)

        # Create 7 days of data (Jan 1-7)
        daily_data = [
            ('2026-01-01', 10.5),  # Day 1
            ('2026-01-02', 11.2),  # Day 2
            ('2026-01-03', 9.8),   # Day 3
            ('2026-01-04', 10.1),  # Day 4
            ('2026-01-05', 12.3),  # Day 5
            ('2026-01-06', 8.7),   # Day 6
            ('2026-01-07', 9.4),   # Day 7
        ]

        plant_name = "test-plant"
        self.create_monthly_csv(test_data_dir, plant_name, '2026-01', daily_data)

        # Get weekly summary - pass plant base names (strings), not file paths
        plant_base_names = [plant_name]

        # Mock datetime in get_weekly_summary to use our test date
        import generate_weekly_report
        original_datetime = generate_weekly_report.datetime

        class MockDateTime:
            @staticmethod
            def now():
                return today
            @staticmethod
            def strptime(date_string, format_string):
                return original_datetime.strptime(date_string, format_string)

        generate_weekly_report.datetime = MockDateTime

        try:
            summary = get_weekly_summary(plant_base_names, test_data_dir)

            # Expected weekly total = sum of all 7 days
            expected_weekly = sum(kwh for _, kwh in daily_data)
            assert summary['total_weekly'] == pytest.approx(expected_weekly, 0.01)
            assert summary['total_weekly'] == pytest.approx(72.0, 0.01)

            # Expected daily average
            expected_avg = expected_weekly / 7
            assert summary['plants'][0]['daily_average'] == pytest.approx(expected_avg, 0.01)
            assert summary['plants'][0]['daily_average'] == pytest.approx(10.29, 0.01)
        finally:
            generate_weekly_report.datetime = original_datetime

    def test_monthly_production_sum(self, test_data_dir):
        """Calculate correct monthly total"""
        import calendar
        today = date.today()
        current_month = today.strftime('%Y-%m')
        days_in_month = calendar.monthrange(today.year, today.month)[1]

        # Create full month of data for current month (dynamic)
        daily_data = [(f'{current_month}-{day:02d}', 10.0 + day * 0.1) for day in range(1, days_in_month + 1)]

        plant_name = "test-plant"
        self.create_monthly_csv(test_data_dir, plant_name, current_month, daily_data)

        plant_base_names = [plant_name]
        summary = get_weekly_summary(plant_base_names, test_data_dir)

        # Expected monthly total = sum of all days
        expected_monthly = sum(kwh for _, kwh in daily_data)
        assert summary['total_monthly'] == pytest.approx(expected_monthly, 0.01)

    def test_yearly_production_from_yearly_csv(self, test_data_dir):
        """Read yearly total from yearly CSV file (month,kwh format)"""
        # Create yearly CSV with monthly totals
        monthly_data = [
            ('2026-01', 250.5),
            ('2026-02', 280.3),
            ('2026-03', 310.2),
            ('2026-04', 295.8),
            ('2026-05', 320.1),
            ('2026-06', 305.4),
            ('2026-07', 330.2),
            ('2026-08', 315.7),
            ('2026-09', 290.5),
            ('2026-10', 275.3),
            ('2026-11', 260.8),
            ('2026-12', 285.6),
        ]

        plant_name = "test-plant"
        self.create_yearly_csv(test_data_dir, plant_name, '2026', monthly_data)

        # Create minimal monthly file for current month
        self.create_monthly_csv(test_data_dir, plant_name, '2026-01', [('2026-01-01', 10.0)])

        plant_base_names = [plant_name]
        summary = get_weekly_summary(plant_base_names, test_data_dir)

        # Expected yearly total = sum of all months
        expected_yearly = sum(kwh for _, kwh in monthly_data)
        assert summary['total_yearly'] == pytest.approx(expected_yearly, 0.01)
        assert summary['total_yearly'] == pytest.approx(3520.4, 0.01)

    def test_multiple_plants_per_customer(self, test_data_dir):
        """Customer with multiple plants gets combined report"""
        # Create 3 plants for same customer (use last 2 days, dynamic dates)
        today = date.today()
        day1 = (today - timedelta(days=1)).strftime('%Y-%m-%d')
        day2 = today.strftime('%Y-%m-%d')
        current_month = today.strftime('%Y-%m')

        plants_data = [
            ("customer-plant1", [(day1, 10.0), (day2, 11.0)]),
            ("customer-plant2", [(day1, 15.0), (day2, 16.0)]),
            ("customer-plant3", [(day1, 8.0), (day2, 9.0)]),
        ]

        plant_base_names = []
        for plant_name, daily_data in plants_data:
            self.create_monthly_csv(test_data_dir, plant_name, current_month, daily_data)
            plant_base_names.append(plant_name)

        summary = get_weekly_summary(plant_base_names, test_data_dir)

        # Should have 3 plants in summary
        assert len(summary['plants']) == 3

        # Total weekly should be sum of all plants
        expected_total = (10+11) + (15+16) + (8+9)
        assert summary['total_weekly'] == pytest.approx(expected_total, 0.01)

    def test_format_weekly_email_no_alerts(self, test_data_dir):
        """Format weekly email with no alerts"""
        summary = {
            'week_start': '2026-01-01',
            'week_end': '2026-01-07',
            'current_month': '2026-01',
            'prev_month': '2025-12',
            'current_year': '2026',
            'prev_year': '2025',
            'total_weekly': 72.0,
            'total_monthly': 310.5,
            'total_prev_monthly': 0.0,
            'total_yearly': 3520.4,
            'total_prev_yearly': 0.0,
            'plants': [
                {
                    'name': 'test-plant',
                    'weekly_kwh': 72.0,
                    'daily_average': 10.29,
                    'monthly_kwh': 310.5,
                    'yearly_kwh': 3520.4
                }
            ]
        }

        email_body = format_weekly_email("Test Customer", summary, [])

        # Verify email contains key information
        assert "WEEKLY SOLAR PRODUCTION REPORT" in email_body
        assert "Test Customer" in email_body
        assert "72.00 kWh" in email_body
        assert "310.50 kWh" in email_body
        assert "3520.40 kWh" in email_body
        assert "ALL SYSTEMS OPERATIONAL" in email_body

    def test_load_credentials(self, test_credentials_file):
        """Load customer credentials from JSON file"""
        accounts = load_credentials(test_credentials_file)

        assert len(accounts) == 1
        assert accounts[0]['label'] == "Test Customer"
        assert accounts[0]['email'] == "customer@example.com"

    def test_previous_month_comparison(self, test_data_dir):
        """
        NEW FEATURE TEST: Previous month comparison
        Verify that previous month totals are read and calculated correctly
        """
        import calendar
        plant_name = "test-plant"
        today = date.today()
        current_month = today.strftime('%Y-%m')
        days_in_current = calendar.monthrange(today.year, today.month)[1]

        # Previous month
        first_of_month = today.replace(day=1)
        prev_month_date = first_of_month - timedelta(days=1)
        prev_month = prev_month_date.strftime('%Y-%m')
        days_in_prev = calendar.monthrange(prev_month_date.year, prev_month_date.month)[1]

        # Create current month data (dynamic)
        current_data = [(f'{current_month}-{day:02d}', 10.0) for day in range(1, days_in_current + 1)]
        self.create_monthly_csv(test_data_dir, plant_name, current_month, current_data)

        # Create previous month data (dynamic)
        prev_data = [(f'{prev_month}-{day:02d}', 8.0) for day in range(1, days_in_prev + 1)]
        self.create_monthly_csv(test_data_dir, plant_name, prev_month, prev_data)

        plant_base_names = [plant_name]
        summary = get_weekly_summary(plant_base_names, test_data_dir)

        # Verify previous month total
        expected_prev_monthly = 8.0 * days_in_prev
        assert summary['total_prev_monthly'] == pytest.approx(expected_prev_monthly, 0.01)

        # Verify current month total
        expected_current_monthly = 10.0 * days_in_current
        assert summary['total_monthly'] == pytest.approx(expected_current_monthly, 0.01)

        # Verify month-over-month change
        expected_change = expected_current_monthly - expected_prev_monthly
        assert summary['total_monthly'] - summary['total_prev_monthly'] == pytest.approx(expected_change, 0.01)

    def test_previous_year_comparison(self, test_data_dir):
        """
        NEW FEATURE TEST: Previous year comparison
        Verify that previous year totals are read and calculated correctly
        """
        plant_name = "test-plant"

        # Create current year data (2026)
        current_year_data = [
            ('2026-01', 310.0),
            ('2026-02', 320.0),
            ('2026-03', 330.0),
        ]
        self.create_yearly_csv(test_data_dir, plant_name, '2026', current_year_data)

        # Create previous year data (2025)
        prev_year_data = [
            ('2025-01', 250.0),
            ('2025-02', 260.0),
            ('2025-03', 270.0),
            ('2025-04', 280.0),
            ('2025-05', 290.0),
            ('2025-06', 300.0),
            ('2025-07', 310.0),
            ('2025-08', 320.0),
            ('2025-09', 330.0),
            ('2025-10', 340.0),
            ('2025-11', 350.0),
            ('2025-12', 360.0),
        ]
        self.create_yearly_csv(test_data_dir, plant_name, '2025', prev_year_data)

        # Create minimal monthly file for current month
        self.create_monthly_csv(test_data_dir, plant_name, '2026-01', [('2026-01-01', 10.0)])

        plant_base_names = [plant_name]
        summary = get_weekly_summary(plant_base_names, test_data_dir)

        # Verify previous year total (sum of all 12 months)
        expected_prev_yearly = sum(kwh for _, kwh in prev_year_data)
        assert summary['total_prev_yearly'] == pytest.approx(expected_prev_yearly, 0.01)
        assert summary['total_prev_yearly'] == pytest.approx(3660.0, 0.01)

        # Verify current year total
        expected_current_yearly = sum(kwh for _, kwh in current_year_data)
        assert summary['total_yearly'] == pytest.approx(expected_current_yearly, 0.01)

    def test_format_email_with_previous_period_comparisons(self, test_data_dir):
        """
        NEW FEATURE TEST: Email formatting with previous month/year comparisons
        Verify email includes comparison data with change indicators
        """
        summary = {
            'week_start': '2026-01-01',
            'week_end': '2026-01-07',
            'current_month': '2026-01',
            'prev_month': '2025-12',
            'current_year': '2026',
            'prev_year': '2025',
            'total_weekly': 72.0,
            'total_monthly': 310.0,
            'total_prev_monthly': 248.0,
            'total_yearly': 960.0,
            'total_prev_yearly': 3660.0,
            'plants': [
                {
                    'name': 'test-plant',
                    'weekly_kwh': 72.0,
                    'daily_average': 10.29,
                    'monthly_kwh': 310.0,
                    'yearly_kwh': 960.0
                }
            ]
        }

        email_body = format_weekly_email("Test Customer", summary, [])

        # Verify email contains current month
        assert "This Month (2026-01)" in email_body
        assert "310.00 kWh" in email_body

        # Verify email contains previous month
        assert "Last Month (2025-12)" in email_body
        assert "248.00 kWh" in email_body

        # Verify month-over-month change (positive)
        assert "Month Change:" in email_body
        assert "+62.00 kWh" in email_body

        # Verify email contains current year
        assert "This Year (2026)" in email_body
        assert "960.00 kWh" in email_body

        # Verify email contains previous year
        assert "Last Year (2025)" in email_body
        assert "3660.00 kWh" in email_body

        # Verify year-over-year change (negative)
        assert "Year Change:" in email_body
        assert "-2700.00 kWh" in email_body

    def test_missing_previous_period_files_handled_gracefully(self, test_data_dir):
        """
        EDGE CASE TEST: Missing previous month/year files should not crash
        Should default to 0 for missing previous periods
        """
        plant_name = "test-plant"

        # Create only current month data (no previous month/year files) - use last 7 days, dynamic dates
        today = date.today()
        current_month = today.strftime('%Y-%m')
        current_data = [
            ((today - timedelta(days=7-i)).strftime('%Y-%m-%d'), 10.0)
            for i in range(1, 8)
        ]
        self.create_monthly_csv(test_data_dir, plant_name, current_month, current_data)

        plant_base_names = [plant_name]
        summary = get_weekly_summary(plant_base_names, test_data_dir)

        # Should not crash and should default to 0
        assert summary['total_prev_monthly'] == 0.0
        assert summary['total_prev_yearly'] == 0.0

        # Current totals should still work
        assert summary['total_monthly'] > 0
        assert summary['total_weekly'] > 0

    # =========================================================================
    # UC10: DessMonitor Platform Support Tests
    # =========================================================================

    def test_load_credentials_dessmonitor_platform(self, tmp_path):
        """
        UC10 TEST: Load credentials with DessMonitor platform parameter
        Verifies that dessmonitor_accounts key is used for DessMonitor platform
        """
        # Create DessMonitor credentials file
        creds = {
            "company_key": "bnrl_test",
            "dessmonitor_accounts": [
                {
                    "label": "DessCustomer1",
                    "username": "dessuser1",
                    "password": "pass123",
                    "email": "dess@example.com"
                },
                {
                    "label": "DessCustomer2",
                    "username": "dessuser2",
                    "password": "pass456"
                    # No email - should be skipped in report generation
                }
            ]
        }
        creds_file = tmp_path / "dessmonitor_credentials.json"
        with open(creds_file, 'w') as f:
            json.dump(creds, f)

        # Load with dessmonitor platform
        accounts = load_credentials(creds_file, platform='dessmonitor')

        assert len(accounts) == 2
        assert accounts[0]['label'] == "DessCustomer1"
        assert accounts[0]['email'] == "dess@example.com"
        assert accounts[1]['label'] == "DessCustomer2"
        assert 'email' not in accounts[1]

    def test_load_credentials_shinemonitor_default(self, tmp_path):
        """
        UC10 TEST: Load credentials defaults to ShineMonitor (accounts key)
        Verifies backward compatibility with existing credentials format
        """
        creds = {
            "company_key": "shine_test",
            "accounts": [
                {
                    "label": "ShineCustomer",
                    "username": "shineuser",
                    "password": "pass789",
                    "email": "shine@example.com"
                }
            ],
            "dessmonitor_accounts": [
                {
                    "label": "DessCustomer",
                    "username": "dessuser",
                    "password": "pass000",
                    "email": "dess@example.com"
                }
            ]
        }
        creds_file = tmp_path / "credentials.json"
        with open(creds_file, 'w') as f:
            json.dump(creds, f)

        # Load without platform (default = shinemonitor)
        accounts = load_credentials(creds_file)
        assert len(accounts) == 1
        assert accounts[0]['label'] == "ShineCustomer"

        # Load with explicit shinemonitor platform
        accounts = load_credentials(creds_file, platform='shinemonitor')
        assert len(accounts) == 1
        assert accounts[0]['label'] == "ShineCustomer"

    def test_get_customer_plants_dessmonitor_filter(self, test_data_dir):
        """
        UC10 TEST: get_customer_plants filters by dessmonitor- prefix
        Verifies that DessMonitor platform only returns DessMonitor CSV files
        """
        today = date.today()
        current_month = today.strftime('%Y-%m')

        # Create ShineMonitor CSV files
        self.create_monthly_csv(test_data_dir, "customer-plant1", current_month, [('2026-01-01', 10.0)])
        self.create_monthly_csv(test_data_dir, "customer-plant2", current_month, [('2026-01-01', 15.0)])

        # Create DessMonitor CSV files (prefixed with dessmonitor-)
        self.create_monthly_csv(test_data_dir, "dessmonitor-customer-plant3", current_month, [('2026-01-01', 20.0)])
        self.create_monthly_csv(test_data_dir, "dessmonitor-customer-plant4", current_month, [('2026-01-01', 25.0)])

        # Get plants for ShineMonitor (default) - should NOT include dessmonitor files
        shine_plants = get_customer_plants(test_data_dir, "customer")
        assert len(shine_plants) == 2
        assert "customer-plant1" in shine_plants
        assert "customer-plant2" in shine_plants
        assert "dessmonitor-customer-plant3" not in shine_plants

        # Get plants for DessMonitor - should ONLY include dessmonitor files
        dess_plants = get_customer_plants(test_data_dir, "customer", platform='dessmonitor')
        assert len(dess_plants) == 2
        assert "dessmonitor-customer-plant3" in dess_plants
        assert "dessmonitor-customer-plant4" in dess_plants
        assert "customer-plant1" not in dess_plants

    def test_get_customer_plants_shinemonitor_excludes_dessmonitor(self, test_data_dir):
        """
        UC10 TEST: ShineMonitor platform excludes dessmonitor- prefixed files
        Ensures backward compatibility - ShineMonitor reports don't include DessMonitor data
        """
        today = date.today()
        current_month = today.strftime('%Y-%m')

        # Create mixed CSV files
        self.create_monthly_csv(test_data_dir, "gayan-imh-plant", current_month, [('2026-01-01', 10.0)])
        self.create_monthly_csv(test_data_dir, "dessmonitor-gayan-imh-dessplant", current_month, [('2026-01-01', 20.0)])

        # Default (no platform) should exclude dessmonitor files
        plants = get_customer_plants(test_data_dir, "gayan-imh")
        assert len(plants) == 1
        assert plants[0] == "gayan-imh-plant"

        # Explicit shinemonitor should also exclude dessmonitor files
        plants = get_customer_plants(test_data_dir, "gayan-imh", platform='shinemonitor')
        assert len(plants) == 1
        assert plants[0] == "gayan-imh-plant"


    def test_dynamic_months_no_hardcoding(self, test_data_dir):
        """
        REGRESSION TEST: Verify monthly/previous-month calculations work with
        dynamic dates. This test failed when months were hardcoded to 2026-01
        and the calendar rolled to February 2026.
        Tests: current month total, previous month total, and month-over-month change.
        """
        import calendar
        today = date.today()
        current_month = today.strftime('%Y-%m')
        days_in_current = calendar.monthrange(today.year, today.month)[1]

        # Previous month (dynamic)
        first_of_month = today.replace(day=1)
        prev_month_date = first_of_month - timedelta(days=1)
        prev_month = prev_month_date.strftime('%Y-%m')
        days_in_prev = calendar.monthrange(prev_month_date.year, prev_month_date.month)[1]

        plant_name = "dynamic-test-plant"

        # Create current month CSV with 12.0 kWh per day
        current_data = [(f'{current_month}-{d:02d}', 12.0) for d in range(1, days_in_current + 1)]
        self.create_monthly_csv(test_data_dir, plant_name, current_month, current_data)

        # Create previous month CSV with 9.0 kWh per day
        prev_data = [(f'{prev_month}-{d:02d}', 9.0) for d in range(1, days_in_prev + 1)]
        self.create_monthly_csv(test_data_dir, plant_name, prev_month, prev_data)

        summary = get_weekly_summary([plant_name], test_data_dir)

        # Current month total
        expected_current = 12.0 * days_in_current
        assert summary['total_monthly'] == pytest.approx(expected_current, 0.01), \
            f"Current month ({current_month}) total wrong: got {summary['total_monthly']}, expected {expected_current}"

        # Previous month total
        expected_prev = 9.0 * days_in_prev
        assert summary['total_prev_monthly'] == pytest.approx(expected_prev, 0.01), \
            f"Prev month ({prev_month}) total wrong: got {summary['total_prev_monthly']}, expected {expected_prev}"

        # Month-over-month change
        assert summary['total_monthly'] - summary['total_prev_monthly'] == \
            pytest.approx(expected_current - expected_prev, 0.01)

        # Verify summary uses correct month labels
        assert summary['current_month'] == current_month
        assert summary['prev_month'] == prev_month


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
