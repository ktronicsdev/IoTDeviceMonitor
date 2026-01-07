# ShineMonitor IoT Integration Tests

This directory contains integration tests for the ShineMonitor IoT monitoring system.

## Test Structure

```
src/test/java/org/ktronics/scripts/
├── integration/
│   ├── test_admin_alerts.py          # UC1: Admin production alerts
│   ├── test_customer_weekly.py       # UC2: Customer weekly reports
│   ├── test_device_alarms.py         # UC3: Device alarm alerts (TODO)
│   └── fixtures/                     # Test data and expected outputs
├── unit/                             # Unit tests (TODO)
├── pytest.ini                        # Pytest configuration
└── README.md                         # This file
```

## Running Tests

### Prerequisites

Install pytest:
```bash
pip install pytest pytest-cov
```

### Run All Tests

```bash
cd src/test/java/org/ktronics/scripts
pytest
```

### Run Specific Test File

```bash
pytest integration/test_customer_weekly.py
```

### Run Specific Test Case

```bash
pytest integration/test_customer_weekly.py::TestCustomerWeeklyReports::test_weekly_production_calculation
```

### Run with Coverage

```bash
pytest --cov=../../../../../main/java/org/ktronics/scripts
```

### Run Only Integration Tests

```bash
pytest integration/
```

## Test Cases

### UC1: Admin Production Alerts (test_admin_alerts.py)

Tests the anomaly detection system that sends alerts to admin for production issues.

**Test Cases:**
- `test_red_alert_low_production_3days` - RED alert when plant < 20% baseline for 3 days
- `test_orange_alert_low_production_3months` - ORANGE alert when plant < 40% baseline for 3 months
- `test_zero_production_ignored_after_1month` - Auto-ignore plants with 0 production for 1 month
- `test_all_systems_operational` - No alerts when all plants normal
- `test_customer_specific_alerts_generated` - Customer-specific alerts created correctly

### UC2: Customer Weekly Reports (test_customer_weekly.py)

Tests the weekly report generation and email delivery to customers.

**Test Cases:**
- `test_csv_column_names_kwh_not_energy_kwh` - **CRITICAL BUG FIX** - Verify CSV uses 'kwh' column
- `test_weekly_production_calculation` - Calculate correct weekly totals from daily CSV
- `test_monthly_production_sum` - Calculate correct monthly total
- `test_yearly_production_from_yearly_csv` - Read yearly total from yearly CSV (month,kwh format)
- `test_multiple_plants_per_customer` - Customer with multiple plants gets combined report
- `test_format_weekly_email_no_alerts` - Format weekly email correctly
- `test_load_credentials` - Load customer credentials from JSON

### UC3: Device Alarm Alerts (test_device_alarms.py)

**TODO:** Tests for device alarm detection and notification system.

## Fixtures

Test fixtures are located in `integration/fixtures/`:

- `test_data/` - Sample CSV files for testing
- `test_credentials.json` - Test credentials
- Expected output files for validation

## Notes

- Tests use temporary directories to avoid affecting production data
- State files are isolated per test
- Tests mock datetime where necessary for reproducibility
- All tests clean up after themselves
