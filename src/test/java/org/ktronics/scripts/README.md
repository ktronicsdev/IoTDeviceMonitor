# ShineMonitor IoT Integration Tests

This directory contains integration tests for the ShineMonitor IoT monitoring system.

## Test Structure

```
src/test/java/org/ktronics/scripts/
├── integration/
│   ├── test_admin_alerts.py          # UC1: Admin production alerts
│   ├── test_customer_weekly.py       # UC2: Customer weekly reports
│   ├── test_device_alarms.py         # UC3: Device alarm alerts
│   ├── test_centralized_config.py    # BVT: Centralized config verification
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

# Run all tests with verbose output
py -m pytest integration/ -v --tb=short
```

### Run Specific Test Suites

```bash
# UC1: Admin Production Alerts (15 tests) ✅ 100% PASS RATE
py -m pytest integration/test_admin_alerts.py -v
```

```bash
# UC2: Customer Weekly Reports (11 tests) ✅ 100% PASS RATE
py -m pytest integration/test_customer_weekly.py -v
```

```bash
# UC3: Device Alarms (26 tests) ✅ 100% PASS RATE
py -m pytest integration/test_device_alarms.py -v
```

```bash
# BVT: Centralized Config (14 tests) ✅ 100% PASS RATE
py -m pytest integration/test_centralized_config.py -v
```

```bash
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

**Total: 93 tests**

- ✅ **Passed: 93 tests (100%)** in CI environment
- ⏭️ **Skipped: 8 tests** on Windows (API tests only)
- ❌ **Failed: 0 tests (0%)**

### By Test Suite:

- **UC1 (Admin Alerts): 15/15 PASSED (100%)** ✅
- **UC2 (Customer Weekly): 11/11 PASSED (100%)** ✅
- **UC3 (Device Alarms): 38/38 PASSED (100%)** ✅ (includes UC4, UC5, UC6, UC7, UC8)
- **UC9 (Email Optimization): 7/7 PASSED (100%)** ✅
- **BVT (Centralized Config): 14/14 PASSED (100%)** ✅
- **API Tests: 8/8 PASSED in CI** ✅ (skipped on Windows)

### Notes on Platform-Specific Tests:
- **API tests**: Bash script tests require Unix environment (Linux/macOS)
  - Auto-skip on Windows with clear message
  - Run and pass in GitHub Actions (Ubuntu)
  - Tests load credentials from `src/main/java/org/ktronics/config/credentials.json`
  - **100% pass rate in CI environment**

## Test Cases

### UC1: Admin Production Alerts (test_admin_alerts.py)

Tests the anomaly detection system that sends alerts to admin for production issues.

**Test Cases (15 total):**

**Basic Alert Detection:**

- `test_red_alert_low_production_3days` - RED alert when plant < 20% baseline for 3 days
- `test_orange_alert_low_production_3months` - ORANGE alert when plant < 40% baseline for 3 months
- `test_zero_production_ignored_after_1month` - Auto-ignore plants with 0 production for 1 month
- `test_all_systems_operational` - No alerts when all plants normal
- `test_customer_specific_alerts_generated` - Customer-specific alerts created correctly

**Edge Cases & Thresholds:**

- `test_red_alert_just_below_threshold` - Production at 19% (just below 20% RED threshold) triggers alert
- `test_no_alert_just_above_threshold` - Production at 21% (just above 20% threshold) does NOT trigger

**Consecutive Days Logic:**

- `test_no_alert_non_consecutive_low_production` - Intermittent low production (not consecutive) does NOT trigger
- `test_recovery_scenario_alert_clears` - Plant recovers after RED alert period - no alert

**Multiple Plants:**

- `test_multiple_plants_different_alert_levels` - Mixed alert states (RED, normal, ignored) with email formatting

**Boundary Conditions:**

- `test_baseline_with_insufficient_data` - Less than 14 days of data handled gracefully
- `test_exact_3day_boundary` - Exactly 3 consecutive low days triggers alert
- `test_exact_3month_boundary` - Exactly 3 consecutive low months triggers alert

**State & Email:**

- `test_state_persistence_across_runs` - Alert state persists across multiple runs
- `test_email_content_formatting` - Email contains all required sections (summary, actions, breakdown)

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
- `test_previous_month_comparison` - Verify previous month totals are read correctly
- `test_previous_year_comparison` - Verify previous year totals are read correctly
- `test_format_email_with_previous_period_comparisons` - Email formatting with comparison data
- `test_missing_previous_period_files_handled_gracefully` - Missing previous period files default to 0

### UC3: Device Alarm Alerts (test_device_alarms.py)

Tests the device alarm monitoring and notification system (admin + customer notifications).

**Test Cases (26 total):**

**Admin Device Alarms (14 tests):**

- `test_parse_alarm_files_single_customer` - Parse alarms from single customer JSON
- `test_parse_alarm_files_multiple_customers` - Parse alarms from multiple customers
- `test_parse_alarm_files_api_warning_format` - Parse alarms with actual API response format (dat.warning structure)
- `test_parse_alarm_files_detects_warnings_in_response` - **REGRESSION TEST** for alarm detection bug (dat.warning format)
- `test_create_alarm_key_unique` - Verify alarm keys are unique per plant/device/warning
- `test_filter_alarms_first_send` - First time seeing alarm - should send
- `test_filter_alarms_max_sends_reached` - Alarm sent 3 times - should NOT send
- `test_filter_alarms_4hour_interval` - Alarm sent 2 hours ago - should NOT send (4-hour interval)
- `test_filter_alarms_4hour_interval_passed` - Alarm sent 5 hours ago - should send (2nd time)
- `test_format_email_no_alarms` - Format admin email when no alarms (all clear)
- `test_format_email_with_alarms` - Format admin email with device alarms
- `test_update_alarm_state_increment_send_count` - Update state after sending - increment send count
- `test_update_alarm_state_auto_ignore_after_3_sends` - Update state after 3rd send - auto-ignore
- `test_state_persistence` - Alarm state save and load

**Customer Device Alarms (10 tests):**

- `test_load_customer_mapping` - Load plant-to-customer mapping from credentials.json
- `test_create_customer_device_alarms_single_customer` - Map alarms to single customer (Gayan-IMH)
- `test_create_customer_device_alarms_multiple_customers` - Map alarms to multiple customers
- `test_create_customer_device_alarms_skip_no_email` - Skip customers with no email address
- `test_format_customer_device_alarm_email_with_alarms` - Format customer email with device alarms
- `test_format_customer_device_alarm_email_no_alarms` - Format customer email when no alarms
- `test_format_customer_device_alarm_email_max_sends_warning` - Email warning when alarm reaches 3 sends
- `test_send_customer_device_alarms_file_not_found` - Handle missing customer_device_alarms.json
- `test_send_customer_device_alarms_empty_file` - Handle empty customer_device_alarms.json
- `test_send_customer_device_alarms_test_customer_only_mode` - Test-customer-only mode filters for Gayan-IMH

**Manual API Verification (2 tests):**

- `test_manual_api_fetch_ganishkawa` - Documents successful manual API test for Ganishkawa
- `test_manual_api_fetch_namila` - Documents successful manual API test for Namila-Waragoda

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

### BVT (Build Verification Tests)

**Purpose**: Verify centralized configuration system and prevent regressions

**Test Coverage** (14 tests):

**Configuration Module Tests:**

- `test_config_module_exists` - Verify config.py exists and is importable
- `test_credentials_path_defined` - CREDENTIALS_PATH constant is defined
- `test_credentials_path_value` - Path points to correct location
- `test_bash_common_config_exists` - common_config.sh exists for bash scripts

**Script Integration Tests:**

- `test_check_anomaly_uses_centralized_config` - UC1 uses centralized config
- `test_generate_device_alarms_uses_centralized_config` - UC3 uses centralized config
- `test_generate_weekly_report_uses_centralized_config` - UC2 uses centralized config
- `test_generate_admin_summary_uses_centralized_config` - Admin summary uses centralized config
- `test_bash_scripts_source_common_config` - Bash scripts source common_config.sh
- `test_test_files_use_centralized_config` - Test files use centralized config

**Regression Prevention Tests:**

- `test_no_hardcoded_credentials_paths_in_scripts` - No hardcoded paths in scripts
- `test_single_source_of_truth` - Only config modules define path

**Production Verification Tests:**

- `test_credentials_file_exists` - credentials.json exists at configured path
- `test_credentials_file_is_valid_json` - credentials.json is valid JSON

**Why BVT Tests Matter:**

- Catches regressions where scripts might revert to hardcoded paths
- Ensures all scripts follow KISS/DRY principles
- Validates centralized configuration system integrity
- Runs fast (<1 second) - suitable for pre-commit hooks

## Notes

- Tests use temporary directories to avoid affecting production data
- State files are isolated per test
- Tests mock datetime where necessary for reproducibility
- All tests clean up after themselves
- BVT tests run on every test suite execution to catch config regressions early
