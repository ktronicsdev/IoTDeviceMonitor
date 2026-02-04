# Multi-Cloud IoT Device Monitoring System

[![ShineMonitor](https://github.com/ktronicsdev/IoTDeviceMonitor/workflows/ShineMonitor%20Daily%20Monitor/badge.svg)](https://github.com/ktronicsdev/IoTDeviceMonitor/actions)
[![DessMonitor](https://github.com/ktronicsdev/IoTDeviceMonitor/workflows/DessMonitor%20Daily%20Monitor/badge.svg)](https://github.com/ktronicsdev/IoTDeviceMonitor/actions)
[![Dashboard](https://img.shields.io/badge/UC%20Dashboard-Live-blue)](https://ktronicsdev.github.io/IoTDeviceMonitor/dashboard.html)

## Description

Automated monitoring system for **multi-cloud solar panel installations** with intelligent anomaly detection and email alerting. Supports both **ShineMonitor** and **DessMonitor** platforms.

**Supported Platforms:**

| Platform     | API Endpoint            | Status           |
|--------------|-------------------------|------------------|
| ShineMonitor | `web.shinemonitor.com`  | Production       |
| DessMonitor  | `web.dessmonitor.com`   | Production (UC10)|

**Features:**

- **Multi-cloud support** for ShineMonitor and DessMonitor platforms
- Automated data collection from 20+ solar plants
- Daily, monthly, and yearly production tracking
- Smart anomaly detection with RED/ORANGE severity levels
- Personalized email alerts for individual customers
- Weekly progress reports sent to customers
- Device alarm monitoring with 3-send rule
- Historical data retention and trending
- Scheduled monitoring (6x daily via GitHub Actions)
- Platform-specific CSV naming and state management

---

## Use Cases

### UC1: Admin Production Alerts

Monitors all solar plants for production anomalies and sends system-wide alerts to admin.

**Features:**

- RED alert: Production < 20% of baseline for 3 consecutive days
- ORANGE alert: Production < 40% of baseline for 3 consecutive months
- Auto-ignore: Plants with 0 production for 1 month
- 3-day auto-ignore rule prevents alert fatigue

**Email Recipient:** <ktronicssolar@gmail.com>

**Test Coverage:** 15/15 PASSED (100%)

---

### UC2: Customer Weekly Reports

Sends personalized weekly production reports to customers every Sunday.

**Schedule:** Every Sunday at 04:00 UTC

**Platforms Supported:**
- **ShineMonitor:** [trigger-customer-reports.yml](.github/workflows/trigger-customer-reports.yml)
- **DessMonitor:** [trigger-dessmonitor-weekly-reports.yml](.github/workflows/trigger-dessmonitor-weekly-reports.yml)

**Report Contents:**

- Weekly production summary (last 7 days)
- Monthly progress (current month)
- Yearly totals (year-to-date)
- Plant count and combined statistics
- Active production alerts (if any)

**Email Recipients:** Customers with email in credentials.json (platform-specific)

**Platform Filtering:**
- ShineMonitor reports: Exclude `dessmonitor-*` CSV files
- DessMonitor reports: Only include `dessmonitor-*` CSV files
- Email subject: DessMonitor emails prefixed with `[DessMonitor]`

**Admin Summary:**
- Aggregates all customers per platform
- Sent to admin on ALL triggers (push, manual, schedule)
- Platform-specific summaries (ShineMonitor vs DessMonitor)

**UC8 Schedule Filter:**
- **Admin emails:** ALWAYS sent (all triggers)
- **Customer emails:** ONLY on scheduled runs (Sunday)
- **Push/manual triggers:** Admin-only mode (build verification)

**Test Coverage:** 16/16 PASSED (100%)
- Core UC2: 12 tests
- UC10 Platform Support: 4 tests

---

### UC3: Device Alarm Alerts

Monitors device-level warnings from ShineMonitor API and sends targeted notifications.

**Features:**

- Admin device alarms: Sent to <ktronicssolar@gmail.com>
- Customer device alarms: Sent to individual customers (mapped by plant ownership)
- 3-send rule: Each alarm sent 3 times (every 4 hours), then auto-ignored
- 4-hour interval: Prevents spam
- State persistence with human-readable fields (customer, plant, message)

**Test Coverage:** 38/38 PASSED (100%) - includes UC4, UC5, UC6, UC7, UC8 tests

---

### UC4: Test Customer Email Control

Enables continuous testing of device alarm workflow using Gayan-IMH as test customer, bypassing normal 3-send limits on push/manual triggers.

**Workflow Behavior:**

**Scheduled Runs (every 4 hours):**
- All customers: Normal alarm processing with 3-send limit
- Gayan-IMH: Treated same as other customers (receives alarms normally)

**Push/Manual Runs:**
- All customers: Alarm processing skipped for emails
- Gayan-IMH ONLY: Receives device alarm emails with test mode enabled
  - Bypasses ignored flag
  - Bypasses send count limit (3-send rule disabled)
  - Always includes most recent alarm

**Implementation:**

**Device Alarm Processing** ([generate_device_alarms.py:413-420](src/main/java/org/ktronics/scripts/generate_device_alarms.py#L413-L420)):
- `--test-mode` flag bypasses 3-send limit and ignored flag
- Used on push/manual triggers only ([workflow:192-206](/.github/workflows/trigger-shinemonitor.yml#L192-L206))

**Customer Email Filtering** ([workflow:252-260](/.github/workflows/trigger-shinemonitor.yml#L252-L260)):
- Scheduled runs: Send to ALL customers
- Push/manual runs: `--test-customer-only` filters to Gayan-IMH only

**Related Use Cases:**
- UC5 (Test Mode Filter) - Filters alarms to Gayan-IMH in test mode
- UC8 (Schedule Filter) - Controls customer email delivery based on trigger type

**Implementation Status:** ✅ **IMPLEMENTED** (Session 13)

**Test Coverage:** 3 tests (100% pass rate)

---

### UC5: Test Mode Filter (Part of UC3)

Test mode filters alarms to Gayan-IMH (test customer) only.

**Purpose:** Allows testing alarm workflow without sending to all customers

**Parent Use Case:** UC3 (Device Alarm Alerts)

**Test Coverage:** 2 tests

---

### UC6: Log Summary Counts (Part of UC3)

Adds admin visibility in GitHub Actions logs showing alarm summary.

**Output:** `Summary: X alarms from Y customers affecting Z plants`

**Parent Use Case:** UC3 (Device Alarm Alerts)

**Test Coverage:** 1 test

---

### UC7: State JSON Enhancement (Part of UC3)

Human-readable device alarm state file with customer, plant, and message fields.

**State File:** `state/device_alarms_state.json`

**Fields Added:**

- `customer` - Customer name (e.g., "Gayan-IMH")
- `plant` - Plant name (e.g., "imbulgoda 3kw")
- `message` - Alarm message (e.g., "Low battery")

**Parent Use Case:** UC3 (Device Alarm Alerts)

**Test Coverage:** 1 test

---

### UC8: Weekly Report Schedule Filter

Controls when customer emails are sent based on workflow trigger type.

**Behavior:**

| Trigger | Admin Email | Customer Emails |
| ------- | ----------- | --------------- |
| Schedule (Sunday) | Yes | Yes |
| Push | Yes | No |
| Manual | Yes | No |

**Test Coverage:** 1 test

---

### UC9: Admin Alert Email Optimization

Reduces admin email noise by only sending emails when alert state changes.

**Implementation:**

Hash-based state detection in GitHub Actions workflow:

- **Calculate hash** of `alerts/alerts.json` using SHA256
- **Compare with previous hash** from `state/admin_email_state.txt`
- **Send email when:**
  - Alert content changed (hash mismatch)
  - Alerts appeared (empty → hash)
  - Alerts cleared (hash → different hash)
  - Push/manual trigger (build verification)
- **Skip email when:**
  - Scheduled run AND hash unchanged (same state)

**State File:** `state/admin_email_state.txt`

**Email Reduction:** From 6 emails/day to ~1-2 emails/day (83% reduction)

**Implementation File:** [trigger-shinemonitor.yml:247-321](/.github/workflows/trigger-shinemonitor.yml#L247-L321)

**How It Works:**

1. Generate `alerts/alerts.json` with current alerts
2. Calculate SHA256 hash of file content
3. Load previous hash from state file
4. Compare hashes to detect changes
5. Send email only if changed or push/manual trigger
6. Save current hash for next run

**Implementation Status:** ✅ **IMPLEMENTED** (Session 13)

**Test Coverage:** 9 tests (100% pass rate)

---

### UC10: Multi-Cloud Platform Support (DessMonitor)

Extends monitoring capabilities to DessMonitor platform, running in parallel with ShineMonitor.

**Implementation Status:** ✅ **Phase 1 IMPLEMENTED** (Session 14)

**Features:**

- Separate workflow (`trigger-dessmonitor.yml`) runs every 4 hours (offset from ShineMonitor)
- Platform-specific credentials file (`dessmonitor_credentials.json`)
- CSV naming convention: `dessmonitor-{label}-{plant}-YYYY-MM.csv`
- Separate state files for alert tracking
- Shared anomaly detection with `--platform` filter

**Platform Comparison:**

| Feature              | ShineMonitor                    | DessMonitor                        |
|----------------------|---------------------------------|------------------------------------|
| Workflow             | `trigger-shinemonitor.yml`      | `trigger-dessmonitor.yml`          |
| Schedule             | 02:30, 06:30, 10:30, 14:30...   | 03:00, 07:00, 11:00, 15:00...      |
| Credentials          | `credentials.json`              | `dessmonitor_credentials.json`     |
| CSV Prefix           | (none)                          | `dessmonitor-`                     |
| State File           | `alerts_state.json`             | `dessmonitor_alerts_state.json`    |
| Test Customer        | Gayan-IMH                       | MifrazMarsoon                      |

**API Scripts:**

- `dessmonitor_common.sh` - API client with dual auth fallback
- `check_dessmonitor_monthly.sh` - Monthly energy data fetcher

**GitHub Secrets Required:**

- `DESSMONITOR_CREDENTIALS_JSON` - DessMonitor account credentials

**Test Coverage:** 112 tests across 7 test files (see DessMonitor Test Suite below)

---

### UC11: Customer Plant ROI Verification (OffGrid Backup Analysis)

Analyzes historical device data to calculate ROI from OffGrid (battery backup) usage. Tracks when the system provides power from battery during grid outages.

**Implementation Status:** ✅ **IMPLEMENTED** (Session 15)

**Purpose:**

- Verify customer ROI by analyzing backup power usage over time
- Track when `work_state = OffGrid` and `PLoad > 0`
- Calculate total backup hours, energy provided, and estimated cost savings
- Generate monthly breakdown and event history

**Scripts:**

| Script | Purpose |
|--------|---------|
| `check_plant_roi.py` | Fetch historical device data via `queryDeviceDataOneDayPaging` API |
| `analyze_plant_roi.py` | Analyze CSV data and generate ROI report |

**Usage:**

```bash
# Fetch last 365 days of data
py check_plant_roi.py --customer Abeetha --days 365

# Fetch specific year
py check_plant_roi.py --customer Abeetha --start-date 2024-01-01 --end-date 2024-12-31

# Generate ROI report
py analyze_plant_roi.py --input roi/abeetha-device-data-365days.csv --customer "Abeetha"
```

**Output Metrics:**

- Total OffGrid time (hours)
- Total OffGrid energy (kWh)
- Number of backup events
- Average/longest event duration
- Peak load during backup
- Monthly breakdown
- Estimated cost savings (Rs.)

**Sample 3-Year Results (Abeetha):**

| Year | OffGrid Time | Energy | Events | Peak Load | Savings |
|------|--------------|--------|--------|-----------|---------|
| 2023 | 55.3 hrs | 15.68 kWh | 60 | 3,159 W | Rs. 314 |
| 2024 | 24.6 hrs | 6.25 kWh | 44 | 1,577 W | Rs. 125 |
| 2025-26 | 68.8 hrs | 12.02 kWh | 72 | 2,716 W | Rs. 240 |
| **Total** | **148.7 hrs** | **33.95 kWh** | **176** | 3,159 W | **Rs. 679** |

**Data Storage:** `roi/` directory (gitignored for customer privacy)

**API Endpoint:** `queryDeviceDataOneDayPaging` - Returns 5-minute interval device metrics

---

## Bug Fixes

### UC9: Hash Timestamp Bug (Session 14)

**Problem:** Admin emails sent 6x daily even when alert content unchanged

**Root Cause:** `alerts.json` includes `generated_at` timestamp which changes on every run, causing hash to always change

**Fix:** Use `jq` to hash only alert content (excluding timestamp):

```bash
# Before (buggy):
sha256sum alerts/alerts.json

# After (fixed):
jq -cS '{alerts, suppressed, ignored}' alerts/alerts.json | sha256sum
```

**Commit:** `f624472`

---

### UC3: API Field Name Mismatch (Session 10)

**Problem:** Admin device alarm emails showed "Unknown Plant", "Unknown Device", "No message"

**Root Cause:** Code expected field names `pId`, `devId`, `warnId` but API returns `pid`, `pn`, `id`

**Fix:** Check both field name variants in:

- `create_alarm_key()` - Alarm key generation
- `create_customer_device_alarms()` - Customer mapping
- `format_device_alarms_email()` - Email formatting

**Commits:** `e06e813`, `b10bad9`, `5480ffa`

---

### UC3: JSON Parsing Bug (Session 7)

**Problem:** Device alarm emails not sent despite alarms being fetched

**Root Cause:**

- Code expected `{"dat": [...]}` (array)
- API returns `{"dat": {"total": N, "warning": [...]}}` (object)

**Fix:** Handle both response formats in `generate_device_alarms.py`

**Commit:** `7cdc8cd`

---

### UC3: Test Mode 3-Send Limit Bug (Session 12)

**Problem:** Test mode showed "Send #4/3" - alarm sent 4 times despite 3-send limit

**Root Cause:** Test mode bypassed ignore check and send_count validation

**Fix:** Added same validation as `filter_alarms_to_send()` to test mode

**Commit:** `334035b`

---

### UC2: CSV Column Name Bug (Session 1)

**Problem:** Weekly reports showed 0.00 kWh values

**Root Cause:** Code expected column `energy_kwh` but CSV files use `kwh`

**Fix:** Changed `row['energy_kwh']` to `row['kwh']` in `generate_weekly_report.py`

---

### UC2: Date Comparison Bug (Session 8)

**Problem:** Weekly reports missing first day of data

**Root Cause:** Compared datetime objects WITH time components causing boundary issues

**Fix:** Compare date objects WITHOUT time: `week_ago.date() <= row_date <= today.date()`

**Commit:** `44d52f4`

---

### BVT: Centralized Config (Session 8)

**Problem:** UC3 device alarms failed with `FileNotFoundError: credentials.json`

**Root Cause:** 10 files had different hardcoded credentials paths

**Fix:** Created centralized config modules:

- `config.py` - Python: `CREDENTIALS_PATH` constant
- `common_config.sh` - Bash: `CREDENTIALS_FILE` variable

**Commit:** `5356e75`

---

## Architecture

- **Data Collection**: Bash scripts fetch data from ShineMonitor API
- **Anomaly Detection**: Python-based ML detection with configurable thresholds
- **Alerting**: Email notifications via SMTP
- **Automation**: GitHub Actions workflows for scheduled execution
- **Storage**: CSV files for time-series data, JSON for alert state

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/ktronicsdev/IoTDeviceMonitor
cd IoTDeviceMonitor
```

### 2. Configure credentials

Create `src/main/java/org/ktronics/config/credentials.json`:

```json
{
  "company_key": "your_company_key",
  "accounts": [
    {
      "label": "Customer Name",
      "username": "customer_username",
      "password": "customer_password",
      "email": "customer@example.com"
    }
  ]
}
```

**Note**: The `email` field is optional. Customers with email addresses will receive:

- Weekly progress reports (every Sunday)
- Personalized alert notifications for harvesting lost > 20% (for 3 days)
- Personalized device alarm notifications (2 times a day)

### 3. Run data collection manually

```bash
# Fetch monthly data
sh ./src/main/java/org/ktronics/scripts/check_shinemonitor_monthly.sh \
  src/main/java/org/ktronics/config/credentials.json 2025-12
```

```bash
# Run anomaly detection
py -m src/main/java/org/ktronics/scripts/check_anomaly.py \
  --data-dir data \
  --out-dir alerts \
  --state-file state/alerts_state.json
```

```bash
# Run alarm detection
sh ./src/main/java/org/ktronics/scripts/check_device_alarms.sh "Ganishkawa" "123456" "bnrl_frRFjEz8Mkn" "alarms/customer_alerts.json"
```

### 4. View alerts

```bash
cat alerts/alerts.txt
```

## GitHub Actions Setup

The system runs automatically via GitHub Actions:

- **Main Monitoring**: 6x daily (every 4 hours)
  - Detects anomalies across all plants
  - Sends system-wide status to <ktronicssolar@gmail.com>
  - Tracks customer-specific alerts (3-day auto-ignore)
  - Sends customer-specific alert emails to individual customers

- **Weekly Reports**: Every Sunday at 04:00 UTC
  - Sends personalized reports to customers with email addresses
  - Admin receives summary on all triggers (push/manual/schedule)
  - Customers receive reports only on scheduled runs

### Required Secrets

Configure these in GitHub Settings > Secrets:

- `SHINEMONITOR_CREDENTIALS_JSON`: ShineMonitor account credentials
- `DESSMONITOR_CREDENTIALS_JSON`: DessMonitor account credentials (UC10)
- `SMTP_HOST`: SMTP server (e.g., smtp.gmail.com)
- `SMTP_PORT`: SMTP port (e.g., 587)
- `SMTP_USER`: Email address for sending
- `SMTP_PASS`: Gmail App Password (not regular password!)
- Alert recipient is configured in workflow as `ALERT_TO`

### Gmail App Password Setup

1. Enable 2-Step Verification on your Google Account
2. Go to <https://myaccount.google.com/apppasswords>
3. Create app password for "Mail"
4. Use the 16-character password in `SMTP_PASS` secret

## Alert Configuration

Default thresholds in `check_anomaly.py`:

| Parameter | Default | Description |
| --------- | ------- | ----------- |
| `--red-pct` | 20% | RED alert: production < 20% of baseline |
| `--red-days` | 3 | RED alert: consecutive days threshold |
| `--orange-pct` | 40% | ORANGE alert: production < 40% of baseline |
| `--orange-months` | 3 | ORANGE alert: consecutive months threshold |
| `--ignore-zero-months` | 1 | Ignore plants with 0 production for N months |

## Project Structure

```text
.
├── .github/workflows/
│   ├── trigger-shinemonitor.yml       # ShineMonitor monitoring (6x daily)
│   ├── trigger-dessmonitor.yml        # DessMonitor monitoring (6x daily) - UC10
│   └── trigger-customer-reports.yml   # Weekly reports (Sunday)
├── src/main/java/org/ktronics/
│   ├── config/
│   │   ├── credentials.json           # ShineMonitor credentials (gitignored)
│   │   └── dessmonitor_credentials.json # DessMonitor credentials (gitignored)
│   └── scripts/
│       ├── config.py                  # Centralized Python config
│       ├── common_config.sh           # Centralized Bash config
│       ├── shinemonitor_common.sh     # ShineMonitor API utilities
│       ├── dessmonitor_common.sh      # DessMonitor API utilities (UC10)
│       ├── check_shinemonitor_monthly.sh  # ShineMonitor monthly data
│       ├── check_shinemonitor_yearly.sh   # ShineMonitor yearly data
│       ├── check_dessmonitor_monthly.sh   # DessMonitor monthly data (UC10)
│       ├── check_device_alarms.sh     # Fetch device alarms
│       ├── check_anomaly.py           # Anomaly detection (multi-platform)
│       ├── generate_device_alarms.py  # Device alarm processing
│       ├── generate_weekly_report.py  # Weekly report generation
│       ├── generate_admin_summary.py  # Admin summary generation
│       ├── send_customer_emails.py    # Customer email sending
│       └── send_email.py              # System-wide email notifications
├── data/                              # CSV time-series data
│   ├── {plant}-YYYY-MM.csv            # ShineMonitor data files
│   └── dessmonitor-{plant}-YYYY-MM.csv # DessMonitor data files (UC10)
├── alarms/                            # Device alarm JSON files
├── alerts/                            # Generated alert reports
│   ├── alerts.json                    # ShineMonitor alerts
│   ├── dessmonitor_alerts.json        # DessMonitor alerts (UC10)
│   └── customer_alerts.json           # Customer-specific alerts
├── reports/                           # Weekly customer reports
│   └── archive/                       # Report archives (last 4 weeks)
└── state/                             # State tracking
    ├── alerts_state.json              # ShineMonitor alert state
    ├── dessmonitor_alerts_state.json  # DessMonitor alert state (UC10)
    ├── admin_email_state.txt          # UC9 hash state
    └── device_alarms_state.json       # Device alarm state
```

## Testing

### Integration Test Suite

The project includes comprehensive integration tests covering all business logic.

#### Test Coverage: 231 tests (100% pass rate)

**ShineMonitor (119 tests):**

| Suite | Tests | Status |
| ----- | ----- | ------ |
| UC1 (Admin Alerts) | 15 | PASSED |
| UC2 (Customer Weekly) | 16 | PASSED |
| UC3-UC8 (Device Alarms) | 38 | PASSED |
| UC9 (Email Optimization) | 9 | PASSED |
| UC11 (Plant ROI) | 19 | PASSED |
| BVT (Centralized Config) | 14 | PASSED |
| API Tests | 8 | PASSED in CI |

**DessMonitor (112 tests):**

| Suite | Tests | Status |
| ----- | ----- | ------ |
| UC1 (Admin Alerts) | 19 | PASSED |
| UC2 (Data Collection) | 15 | PASSED |
| UC3-UC8 (Device Alarms) | 19 | PASSED |
| UC9 (Email Optimization) | 9 | Stub |
| UC11 (Plant ROI) | 19 | Stub |
| BVT (Centralized Config) | 13 | PASSED |
| API Tests | 18 | PASSED in CI |

```bash
# Run all tests
cd src/test/java/org/ktronics/scripts
py -m pytest integration/ -v

# Run specific test suite - ShineMonitor
py -m pytest integration/test_admin_alerts.py -v      # UC1: Admin alerts
py -m pytest integration/test_customer_weekly.py -v   # UC2: Customer reports
py -m pytest integration/test_device_alarms.py -v     # UC3: Device alarms
py -m pytest integration/test_centralized_config.py -v # BVT: Config tests
py -m pytest integration/test_uc9_admin_email.py -v    # UC9: Email optimization
py -m pytest integration/test_uc11_plant_roi.py -v     # UC11: Plant ROI

# Run specific test suite - DessMonitor
py -m pytest integration/test_dessmonitor_*.py -v     # All DessMonitor tests (112)
```

See [src/test/java/org/ktronics/scripts/README.md](src/test/java/org/ktronics/scripts/README.md) for detailed test documentation.

## Troubleshooting

### No Weekly Emails Received

1. Check customer has `email` field in credentials.json
2. Verify SMTP credentials in GitHub Secrets
3. Check workflow logs for errors
4. Verify plant naming matches customer label (case-insensitive)

### Alerts Not Auto-Ignoring

1. Check `state/alerts_state.json` file
2. Verify `auto-ignore-days` is set to 3
3. Check main workflow includes customer alert tracking step
4. Review workflow logs for tracking step

### Device Alarms Not Sending

1. Check `state/device_alarms_state.json` for alarm tracking
2. Verify alarm hasn't reached 3-send limit (`send_count >= 3`)
3. Check 4-hour interval hasn't elapsed since last send
4. Review workflow logs for "alarms ready to send" count

### Plants Not Matching Customers

The system uses fuzzy matching based on customer labels:

- Customer: "Lahiru Ryan" -> matches: `lahiru-ryan-*`, `lahiruryan-*`
- Customer: "Gayan-IMH" -> matches: `gayan-imh-*`, `gayanim-*`

Ensure plant file names contain recognizable parts of the customer label.

## Contributing

This is an internal monitoring system. For issues or improvements, contact the development team.

## License

Proprietary - KTronics Development
