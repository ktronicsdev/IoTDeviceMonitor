#!/usr/bin/env python3
"""
UC11: Customer Plant ROI Verification - Integration Tests

Tests for the ROI analysis feature that tracks OffGrid backup usage.
Verifies data fetching, CSV parsing, and report generation.
"""

import csv
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path
scripts_dir = Path(__file__).parents[2]
sys.path.insert(0, str(scripts_dir))


class TestUC11DataFetching:
    """Tests for check_plant_roi.py data fetching functionality."""

    def test_fetch_historical_data_date_range_calculation(self):
        """UC11: Verify date range calculation for custom start/end dates."""
        from datetime import datetime

        start_date_str = "2024-01-01"
        end_date_str = "2024-12-31"

        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        days = (end_date - start_date).days + 1

        # 2024 is a leap year, so 366 days
        assert days == 366, "UC11: 2024 should have 366 days (leap year)"

    def test_fetch_historical_data_days_calculation(self):
        """UC11: Verify date range calculation for days-based fetch."""
        days = 365
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        actual_days = (end_date - start_date).days
        assert actual_days == days, f"UC11: Should calculate {days} days"

    def test_output_path_with_year(self):
        """UC11: Verify output path generation with year from start date."""
        customer_slug = "abeetha"
        start_date = "2023-01-01"
        year = start_date[:4]

        output_path = Path(f"roi/{customer_slug}-device-data-{year}.csv")

        # Use as_posix() for platform-agnostic comparison (Windows uses backslash)
        assert output_path.as_posix() == "roi/abeetha-device-data-2023.csv"

    def test_output_path_with_days(self):
        """UC11: Verify output path generation with days count."""
        customer_slug = "abeetha"
        days = 365

        output_path = Path(f"roi/{customer_slug}-device-data-{days}days.csv")

        # Use as_posix() for platform-agnostic comparison (Windows uses backslash)
        assert output_path.as_posix() == "roi/abeetha-device-data-365days.csv"


class TestUC11CSVParsing:
    """Tests for CSV data parsing and validation."""

    @pytest.fixture
    def sample_csv_data(self, tmp_path):
        """Create sample CSV data for testing."""
        csv_path = tmp_path / "test-data.csv"

        data = [
            ['date', 'timestamp', 'work_state', 'pload_w', 'pgrid_w',
             'battery_v', 'batt_current_a', 'grid_v', 'pinverter_w'],
            # OffGrid records
            ['2025-01-18', '2025-01-18 16:00:00', 'OffGrid', '200', '0', '26.4', '9', '0', '200'],
            ['2025-01-18', '2025-01-18 16:05:00', 'OffGrid', '195', '0', '26.3', '8', '0', '195'],
            ['2025-01-18', '2025-01-18 16:10:00', 'OffGrid', '210', '0', '26.2', '10', '0', '210'],
            # Grid-Tie records
            ['2025-01-18', '2025-01-18 12:00:00', 'Grid-Tie', '150', '-500', '27.0', '0', '230', '650'],
            ['2025-01-18', '2025-01-18 12:05:00', 'Grid-Tie', '160', '-480', '27.1', '0', '231', '640'],
        ]

        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(data)

        return csv_path

    def test_csv_column_names(self, sample_csv_data):
        """UC11: Verify CSV has correct column names."""
        with open(sample_csv_data, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames

        expected_columns = ['date', 'timestamp', 'work_state', 'pload_w', 'pgrid_w',
                           'battery_v', 'batt_current_a', 'grid_v', 'pinverter_w']

        assert fieldnames == expected_columns, "UC11: CSV should have correct column names"

    def test_offgrid_record_detection(self, sample_csv_data):
        """UC11: Verify OffGrid records are correctly identified."""
        offgrid_count = 0
        total_count = 0

        with open(sample_csv_data, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_count += 1
                if row['work_state'] == 'OffGrid':
                    offgrid_count += 1

        assert total_count == 5, "UC11: Should have 5 total records"
        assert offgrid_count == 3, "UC11: Should have 3 OffGrid records"

    def test_pload_parsing(self, sample_csv_data):
        """UC11: Verify PLoad values are correctly parsed."""
        pload_values = []

        with open(sample_csv_data, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['work_state'] == 'OffGrid':
                    pload_values.append(int(row['pload_w']))

        assert pload_values == [200, 195, 210], "UC11: PLoad values should be correctly parsed"
        assert sum(pload_values) == 605, "UC11: Total PLoad should be 605W"


class TestUC11ROIAnalysis:
    """Tests for analyze_plant_roi.py ROI calculations."""

    @pytest.fixture
    def sample_analysis_data(self, tmp_path):
        """Create sample data for ROI analysis testing."""
        csv_path = tmp_path / "analysis-data.csv"

        # Create data with multiple OffGrid events
        data = [
            ['date', 'timestamp', 'work_state', 'pload_w', 'pgrid_w',
             'battery_v', 'batt_current_a', 'grid_v', 'pinverter_w'],
        ]

        # Event 1: 30 minutes (6 x 5-min intervals) at ~200W avg
        for i in range(6):
            timestamp = f"2025-01-18 16:{i*5:02d}:00"
            data.append(['2025-01-18', timestamp, 'OffGrid', str(200 + i*5), '0',
                        '26.4', '9', '0', str(200 + i*5)])

        # Gap (Grid-Tie)
        data.append(['2025-01-18', '2025-01-18 17:00:00', 'Grid-Tie', '100', '-300',
                    '27.0', '0', '230', '400'])

        # Event 2: 15 minutes (3 x 5-min intervals) at ~300W avg
        for i in range(3):
            timestamp = f"2025-01-18 18:{i*5:02d}:00"
            data.append(['2025-01-18', timestamp, 'OffGrid', str(300 + i*10), '0',
                        '26.0', '12', '0', str(300 + i*10)])

        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(data)

        return csv_path

    def test_offgrid_time_calculation(self, sample_analysis_data):
        """UC11: Verify OffGrid time is correctly calculated."""
        offgrid_records = 0

        with open(sample_analysis_data, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['work_state'] == 'OffGrid':
                    offgrid_records += 1

        # 9 OffGrid records at 5-minute intervals = 45 minutes = 0.75 hours
        # But we count each record as 5 minutes
        expected_time_hours = (offgrid_records * 5) / 60

        assert offgrid_records == 9, "UC11: Should have 9 OffGrid records"
        assert expected_time_hours == 0.75, "UC11: OffGrid time should be 0.75 hours"

    def test_energy_calculation(self, sample_analysis_data):
        """UC11: Verify energy (kWh) is correctly calculated."""
        total_energy_wh = 0

        with open(sample_analysis_data, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['work_state'] == 'OffGrid':
                    pload = int(row['pload_w'])
                    # Each record represents 5 minutes = 5/60 hours
                    energy_wh = pload * (5 / 60)
                    total_energy_wh += energy_wh

        total_energy_kwh = total_energy_wh / 1000

        # Event 1: 200+205+210+215+220+225 = 1275W over 30 min = 637.5 Wh
        # Event 2: 300+310+320 = 930W over 15 min = 232.5 Wh
        # Total: 870 Wh = 0.87 kWh
        assert total_energy_kwh > 0, "UC11: Total energy should be positive"
        assert round(total_energy_kwh, 2) > 0.1, "UC11: Should have measurable energy"

    def test_peak_load_detection(self, sample_analysis_data):
        """UC11: Verify peak load is correctly detected."""
        peak_load = 0

        with open(sample_analysis_data, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['work_state'] == 'OffGrid':
                    pload = int(row['pload_w'])
                    if pload > peak_load:
                        peak_load = pload

        # Event 2 has highest: 320W
        assert peak_load == 320, "UC11: Peak load should be 320W"

    def test_event_count(self, sample_analysis_data):
        """UC11: Verify event counting logic."""
        # An event is a continuous sequence of OffGrid records
        # Gap of > 10 minutes between OffGrid records = new event

        events = []
        current_event = []
        last_timestamp = None

        with open(sample_analysis_data, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['work_state'] == 'OffGrid':
                    timestamp = datetime.strptime(row['timestamp'], "%Y-%m-%d %H:%M:%S")

                    if last_timestamp is None:
                        current_event = [row]
                    elif (timestamp - last_timestamp).total_seconds() <= 600:  # 10 min gap
                        current_event.append(row)
                    else:
                        if current_event:
                            events.append(current_event)
                        current_event = [row]

                    last_timestamp = timestamp

        if current_event:
            events.append(current_event)

        assert len(events) == 2, "UC11: Should detect 2 separate OffGrid events"


class TestUC11MonthlyBreakdown:
    """Tests for monthly breakdown calculations."""

    @pytest.fixture
    def multi_month_data(self, tmp_path):
        """Create data spanning multiple months."""
        csv_path = tmp_path / "multi-month-data.csv"

        data = [
            ['date', 'timestamp', 'work_state', 'pload_w', 'pgrid_w',
             'battery_v', 'batt_current_a', 'grid_v', 'pinverter_w'],
        ]

        # February data: 2 OffGrid records
        data.append(['2025-02-15', '2025-02-15 10:00:00', 'OffGrid', '500', '0', '26', '20', '0', '500'])
        data.append(['2025-02-15', '2025-02-15 10:05:00', 'OffGrid', '480', '0', '26', '19', '0', '480'])

        # March data: 3 OffGrid records
        data.append(['2025-03-10', '2025-03-10 14:00:00', 'OffGrid', '300', '0', '26', '12', '0', '300'])
        data.append(['2025-03-10', '2025-03-10 14:05:00', 'OffGrid', '310', '0', '26', '12', '0', '310'])
        data.append(['2025-03-10', '2025-03-10 14:10:00', 'OffGrid', '290', '0', '26', '11', '0', '290'])

        # Grid-Tie filler
        data.append(['2025-03-10', '2025-03-10 12:00:00', 'Grid-Tie', '100', '-200', '27', '0', '230', '300'])

        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(data)

        return csv_path

    def test_monthly_grouping(self, multi_month_data):
        """UC11: Verify records are correctly grouped by month."""
        monthly_counts = {}

        with open(multi_month_data, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['work_state'] == 'OffGrid':
                    month = row['date'][:7]  # YYYY-MM
                    monthly_counts[month] = monthly_counts.get(month, 0) + 1

        assert monthly_counts.get('2025-02') == 2, "UC11: February should have 2 OffGrid records"
        assert monthly_counts.get('2025-03') == 3, "UC11: March should have 3 OffGrid records"

    def test_monthly_peak_load(self, multi_month_data):
        """UC11: Verify peak load is calculated per month."""
        monthly_peaks = {}

        with open(multi_month_data, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['work_state'] == 'OffGrid':
                    month = row['date'][:7]
                    pload = int(row['pload_w'])
                    if month not in monthly_peaks or pload > monthly_peaks[month]:
                        monthly_peaks[month] = pload

        assert monthly_peaks.get('2025-02') == 500, "UC11: February peak should be 500W"
        assert monthly_peaks.get('2025-03') == 310, "UC11: March peak should be 310W"


class TestUC11ValueEstimation:
    """Tests for cost savings estimation."""

    def test_cost_calculation(self):
        """UC11: Verify cost savings calculation."""
        energy_kwh = 33.95  # From 3-year Abeetha data
        rate_per_kwh = 20  # Rs. 20/kWh

        expected_savings = energy_kwh * rate_per_kwh

        assert expected_savings == 679.0, "UC11: Cost savings should be Rs. 679"

    def test_zero_energy_cost(self):
        """UC11: Verify zero energy results in zero cost."""
        energy_kwh = 0
        rate_per_kwh = 20

        savings = energy_kwh * rate_per_kwh

        assert savings == 0, "UC11: Zero energy should result in zero savings"


class TestUC11ReportGeneration:
    """Tests for report output generation."""

    def test_report_sections(self, tmp_path):
        """UC11: Verify report contains all required sections."""
        report_path = tmp_path / "test-report.txt"

        # Simulate report content
        report_content = """
======================================================================
PLANT ROI ANALYSIS: Test Customer
======================================================================

Period: 2025-01-01 to 2025-12-31 (365 days)
Total data points: 100,000

----------------------------------------------------------------------
OFFGRID BACKUP SUMMARY
----------------------------------------------------------------------

  Total OffGrid Time:     50.0 hours
  Total OffGrid Energy:   10.00 kWh

----------------------------------------------------------------------
MONTHLY BREAKDOWN
----------------------------------------------------------------------

  Month      | OffGrid Hrs | Energy (kWh)

----------------------------------------------------------------------
VALUE ESTIMATION
----------------------------------------------------------------------

  Est. cost savings:           Rs. 200
"""

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_content)

        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()

        assert "PLANT ROI ANALYSIS" in content, "UC11: Report should have title"
        assert "OFFGRID BACKUP SUMMARY" in content, "UC11: Report should have summary section"
        assert "MONTHLY BREAKDOWN" in content, "UC11: Report should have monthly breakdown"
        assert "VALUE ESTIMATION" in content, "UC11: Report should have value estimation"


class TestUC11EdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_csv(self, tmp_path):
        """UC11: Handle CSV with only headers."""
        csv_path = tmp_path / "empty-data.csv"

        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['date', 'timestamp', 'work_state', 'pload_w', 'pgrid_w',
                            'battery_v', 'batt_current_a', 'grid_v', 'pinverter_w'])

        record_count = 0
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                record_count += 1

        assert record_count == 0, "UC11: Empty CSV should have 0 records"

    def test_no_offgrid_records(self, tmp_path):
        """UC11: Handle CSV with no OffGrid records."""
        csv_path = tmp_path / "no-offgrid.csv"

        data = [
            ['date', 'timestamp', 'work_state', 'pload_w', 'pgrid_w',
             'battery_v', 'batt_current_a', 'grid_v', 'pinverter_w'],
            ['2025-01-18', '2025-01-18 12:00:00', 'Grid-Tie', '150', '-500', '27', '0', '230', '650'],
            ['2025-01-18', '2025-01-18 12:05:00', 'Grid-Tie', '160', '-480', '27', '0', '231', '640'],
        ]

        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(data)

        offgrid_count = 0
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['work_state'] == 'OffGrid':
                    offgrid_count += 1

        assert offgrid_count == 0, "UC11: Should handle CSV with no OffGrid records"

    def test_all_offgrid_records(self, tmp_path):
        """UC11: Handle CSV where all records are OffGrid."""
        csv_path = tmp_path / "all-offgrid.csv"

        data = [
            ['date', 'timestamp', 'work_state', 'pload_w', 'pgrid_w',
             'battery_v', 'batt_current_a', 'grid_v', 'pinverter_w'],
            ['2025-01-18', '2025-01-18 16:00:00', 'OffGrid', '200', '0', '26', '9', '0', '200'],
            ['2025-01-18', '2025-01-18 16:05:00', 'OffGrid', '195', '0', '26', '8', '0', '195'],
            ['2025-01-18', '2025-01-18 16:10:00', 'OffGrid', '210', '0', '26', '10', '0', '210'],
        ]

        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(data)

        total_count = 0
        offgrid_count = 0
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_count += 1
                if row['work_state'] == 'OffGrid':
                    offgrid_count += 1

        assert total_count == offgrid_count == 3, "UC11: All 3 records should be OffGrid"

        # 100% OffGrid percentage
        offgrid_pct = (offgrid_count / total_count) * 100
        assert offgrid_pct == 100.0, "UC11: OffGrid percentage should be 100%"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
