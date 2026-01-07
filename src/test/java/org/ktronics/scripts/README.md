# ShineMonitor IoT Integration Tests

This directory contains integration tests for the ShineMonitor IoT monitoring system.

## Test Structure

```
src/test/java/org/ktronics/scripts/
├── integration/
│   ├── test_admin_alerts.py          # UC1: Admin production alerts
│   ├── test_customer_weekly.py       # UC2: Customer weekly reports
│   ├── test_device_alarms.py         # UC3: Device alarm alerts
│   ├── test_shinemonitor_api.py      # ShineMonitor API integration tests
│   └── fixtures/                     # Test data and expected outputs
├── unit/                             # Unit tests (TODO)
├── pytest.ini                        # Pytest configuration
└── README.md                         # This file
```

## Running Tests

### Prerequisites

**Required:**
- Python 3.11 or higher
- pytest

**Installation:**
```bash
# Install pytest
py -m pip install pytest

# Optional: Install pytest-cov for coverage reports
py -m pip install pytest-cov
```

### Quick Start

```bash
# Navigate to test directory
cd src/test/java/org/ktronics/scripts

# Run all tests
py -m pytest integration/ -v

# Run tests with verbose output
py -m pytest integration/ -v --tb=short
```

### Run Specific Test Suites

```bash
# UC1: Admin Production Alerts (5 tests)
py -m pytest integration/test_admin_alerts.py -v

# UC2: Customer Weekly Reports (11 tests)
py -m pytest integration/test_customer_weekly.py -v

# UC3: Device Alarms (12 tests) ✅ 100% PASS RATE
py -m pytest integration/test_device_alarms.py -v

# API Tests (8 tests - require bash/Unix environment)
py -m pytest integration/test_shinemonitor_api.py -v
```

### Run Specific Test Case

```bash
# Run a single test
py -m pytest integration/test_device_alarms.py::TestDeviceAlarmSystem::test_filter_alarms_first_send -v

# Run with detailed output
py -m pytest integration/test_admin_alerts.py::TestAdminProductionAlerts::test_red_alert_low_production_3days -v -s
```

### Run with Coverage

```bash
# Run with coverage report
py -m pytest integration/ --cov=../../../../../main/java/org/ktronics/scripts --cov-report=html

# View coverage report
start htmlcov/index.html  # Windows
open htmlcov/index.html   # macOS
```

## Current Test Status

**Total: 36 tests**
- ✅ **Passed: 20 tests (56%)**
- ❌ **Failed: 16 tests (44%)**

### By Test Suite:
- **UC1 (Admin Alerts): 5/5 PASSED (100%)** ✅
- **UC2 (Customer Weekly): 4/11 PASSED (36%)** ⚠️
- **UC3 (Device Alarms): 12/12 PASSED (100%)** ✅
- **API Tests: 0/8 PASSED** ⚠️ (fail on Windows, work in GitHub Actions/Linux)

### Notes on Test Failures:
- **UC2 failures**: Pre-existing issues with `get_weekly_summary()` data handling
- **API test failures**: Bash script tests require Unix environment (WSL error on Windows)
  - These tests will pass in GitHub Actions (Ubuntu)
  - Tests now correctly load credentials from `src/main/java/org/ktronics/config/credentials.json`

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

Tests the device alarm monitoring and notification system.

**Test Cases:**
- `test_parse_alarm_files_single_customer` - Parse alarms from single customer JSON
- `test_parse_alarm_files_multiple_customers` - Parse alarms from multiple customers
- `test_create_alarm_key_unique` - Verify alarm keys are unique per plant/device/warning
- `test_filter_alarms_first_send` - First time seeing alarm - should send
- `test_filter_alarms_max_sends_reached` - Alarm sent 3 times - should NOT send
- `test_filter_alarms_4hour_interval` - Alarm sent 2 hours ago - should NOT send (4-hour interval)
- `test_filter_alarms_4hour_interval_passed` - Alarm sent 5 hours ago - should send (2nd time)
- `test_format_email_no_alarms` - Format email when no alarms (all clear)
- `test_format_email_with_alarms` - Format email with device alarms
- `test_update_alarm_state_increment_send_count` - Update state after sending - increment send count
- `test_update_alarm_state_auto_ignore_after_3_sends` - Update state after 3rd send - auto-ignore
- `test_state_persistence` - Alarm state save and load

### ShineMonitor API Tests (test_shinemonitor_api.py)

Tests the ShineMonitor API authentication and shared functions.

**Test Cases:**
- `test_auth_email_function` - Test shinemonitor_auth_email() authentication
- `test_query_plants_api` - Test queryPlants API call
- `test_query_month_energy_api` - Test queryPlantEnergyMonth API call
- `test_query_year_energy_api` - Test queryPlantEnergyYear API call
- `test_query_plants_warning_api` - Test webQueryPlantsWarning API call (device alarms)
- `test_check_device_alarms_script` - Test check_device_alarms.sh end-to-end
- `test_check_monthly_script` - Test check_shinemonitor_monthly.sh
- `test_check_yearly_script` - Test check_shinemonitor_yearly.sh

**Note:** These tests require valid credentials in `src/main/java/org/ktronics/config/credentials.json` and will make real API calls.

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
