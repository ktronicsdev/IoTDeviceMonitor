# ShineMonitor IoT Device Monitoring System

[![GitHub Actions](https://github.com/ktronicsdev/IoTDeviceMonitor/workflows/ShineMonitor%20Daily%20Monitor/badge.svg)](https://github.com/ktronicsdev/IoTDeviceMonitor/actions)

## Description

Automated monitoring system for ShineMonitor solar panel installations with intelligent anomaly detection and email alerting.

**Features:**

- Automated data collection from 20+ solar plants
- Daily, monthly, and yearly production tracking
- Smart anomaly detection with RED/ORANGE severity levels
- Personalized email alerts for individual customers
- Weekly progress reports sent to customers
- Device alarm monitoring with 3-send rule
- Historical data retention and trending
- Scheduled monitoring (6x daily via GitHub Actions)

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

**Report Contents:**

- Weekly production summary (last 7 days)
- Monthly progress (current month)
- Yearly totals (year-to-date)
- Plant count and combined statistics

**Email Recipients:** Customers with email in credentials.json

**Test Coverage:** 11/11 PASSED (100%)

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

Test mode bypasses 3-send limit for Gayan-IMH device alarms, allowing continuous testing.

**Implementation:**

Test mode (`--test-mode` flag in `generate_device_alarms.py`):
- **Bypasses ignored flag** - Sends alarms even if marked as ignored
- **Bypasses send count limit** - Sends alarms even after 3 sends
- **Always includes most recent alarm** - For test customer (Gayan-IMH)

**Use Case:**
- Allows developers to test device alarm workflow on every push/manual trigger
- Gayan-IMH receives device alarms regardless of 3-send rule
- Other customers still respect 3-send limit on scheduled runs

**Implementation File:** [generate_device_alarms.py:413-420](src/main/java/org/ktronics/scripts/generate_device_alarms.py#L413-L420)

**Related Use Cases:**
- UC5 (Test Mode Filter) - Filters to Gayan-IMH only
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

**Test Coverage:** 7 tests (100% pass rate)

---

## Bug Fixes

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

- `SHINEMONITOR_CREDENTIALS_JSON`: Contents of credentials.json
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
│   ├── trigger-shinemonitor.yml       # Main monitoring (6x daily)
│   └── trigger-customer-reports.yml   # Weekly reports (Sunday)
├── src/main/java/org/ktronics/
│   ├── config/
│   │   └── credentials.json           # API credentials (gitignored)
│   └── scripts/
│       ├── config.py                  # Centralized Python config
│       ├── common_config.sh           # Centralized Bash config
│       ├── shinemonitor_common.sh     # Shared API utilities
│       ├── check_shinemonitor_monthly.sh  # Fetch monthly data
│       ├── check_shinemonitor_yearly.sh   # Fetch yearly data
│       ├── check_device_alarms.sh     # Fetch device alarms
│       ├── check_anomaly.py           # Anomaly detection
│       ├── generate_device_alarms.py  # Device alarm processing
│       ├── generate_weekly_report.py  # Weekly report generation
│       ├── generate_admin_summary.py  # Admin summary generation
│       ├── send_customer_emails.py    # Customer email sending
│       └── send_email.py              # System-wide email notifications
├── data/                              # CSV time-series data
├── alarms/                            # Device alarm JSON files
├── alerts/                            # Generated alert reports
│   ├── alerts.json                    # System-wide alerts
│   ├── alerts.txt                     # Formatted alert report
│   └── customer_alerts.json           # Customer-specific alerts
├── reports/                           # Weekly customer reports
│   └── archive/                       # Report archives (last 4 weeks)
└── state/                             # State tracking
    ├── alerts_state.json              # Admin alert state
    └── device_alarms_state.json       # Device alarm state
```

## Testing

### Integration Test Suite

The project includes comprehensive integration tests covering all business logic.

#### Test Coverage: 93 tests (100% pass rate)

| Suite | Tests | Status |
| ----- | ----- | ------ |
| UC1 (Admin Alerts) | 15/15 | PASSED (100%) |
| UC2 (Customer Weekly) | 11/11 | PASSED (100%) |
| UC3 (Device Alarms) | 38/38 | PASSED (100%) - includes UC4, UC5, UC6, UC7, UC8 |
| UC9 (Email Optimization) | 7/7 | PASSED (100%) |
| BVT (Centralized Config) | 14/14 | PASSED (100%) |
| API Tests (Bash Scripts) | 8/8 | PASSED in CI (skipped on Windows) |
| **TOTAL** | **93/93** | **PASSED (100%)** |

```bash
# Run all tests
cd src/test/java/org/ktronics/scripts
py -m pytest integration/ -v

# Run specific test suite
py -m pytest integration/test_admin_alerts.py -v      # UC1: Admin alerts
py -m pytest integration/test_customer_weekly.py -v   # UC2: Customer reports
py -m pytest integration/test_device_alarms.py -v     # UC3: Device alarms
py -m pytest integration/test_centralized_config.py -v # BVT: Config tests
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
