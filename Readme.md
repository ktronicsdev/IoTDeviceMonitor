# ShineMonitor IoT Device Monitoring System

[![GitHub Actions](https://github.com/ktronicsdev/IoTDeviceMonitor/workflows/ShineMonitor%20Daily%20Monitor/badge.svg)](https://github.com/ktronicsdev/IoTDeviceMonitor/actions)

## Description

Automated monitoring system for ShineMonitor solar panel installations with intelligent anomaly detection and email alerting.

**Features:**

- 🔄 Automated data collection from 20+ solar plants
- 📊 Daily, monthly, and yearly production tracking
- 🚨 Smart anomaly detection with RED/ORANGE severity levels
- 📧 Personalized email alerts for individual customers
- 📅 Weekly progress reports sent to customers
- 🔕 3-day auto-ignore rule for persistent alerts
- 📈 Historical data retention and trending
- ⏰ Scheduled monitoring (6x daily via GitHub Actions)

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
- Personalized Auto-ignored alert notifications for after 3 consecutive days harvesting lost > 20% (for 3 days)
- Personalized Auto-ignored alert notifications for alarams (2 times a day)

### 3. Run data collection manually

```bash
# Fetch monthly data
sh ./src/main/java/org/ktronics/scripts/check_shinemonitor_monthly.sh \
  src/main/java/org/ktronics/config/credentials.json 2025-12

# Run anomaly detection
python3 src/main/java/org/ktronics/scripts/check_anomaly.py \
  --data-dir data \
  --out-dir alerts \
  --state-file state/alerts_state.json

# Run alaram detection
sh  ./check_device_alarms.sh "customer_username" "customer_password" "bnrl_frRFjEz8Mkn" "alarms/customer_alerts.json"
```

### 4. View alerts

```bash
cat alerts/alerts.txt
```

## Customer Email System

Customers with email addresses in `credentials.json` receive two types of emails:

### 1. Customer-Specific Alert Emails (6x daily with main monitoring)

When anomalies are detected in the main monitoring workflow (runs 6x daily), **both admin and customers** receive alert emails with a 3-day auto-ignore rule:

**3-Day Auto-Ignore Rule** (applies to both admin and customer alerts):

- **Day 0** (first detection): Alert email sent
- **Day 1**: Reminder email sent
- **Day 2**: Final alert email sent
- **Day 3+**: Alert auto-ignored, **NO emails sent** until issue resolved

This prevents alert fatigue while ensuring proper notification.

**Email Recipients**:

- **Admin**: Receives system-wide alerts for all plants at <ktronicssolar@gmail.com>
- **Customers**: Receive alerts only for their own plants (if email address configured in credentials.json)

### 2. Weekly Progress Reports

**Schedule**: Every Sunday at 18:00 UTC

Customers receive personalized weekly reports including:

- **Weekly Production Summary**: Total kWh for the past 7 days
- **Monthly Progress**: Current month's production
- **Yearly Totals**: Year-to-date production
- **Plant-by-Plant Breakdown**: Individual performance for each solar installation
- **Active Alerts**: Any ongoing issues (if applicable)

### Customer Alert Tracking

The system automatically:

- Maps plants to customers based on naming patterns
- Tracks alert duration per customer
- Sends only relevant alerts to affected customers
- Maintains state between workflow runs

**Plant-to-Customer Mapping Example**:

- Customer label: `"Lahiru Ryan"`
- Matches plants: `lahiru-ryan-*`, `lahiruryan-*`, etc.

## GitHub Actions Setup

The system runs automatically via GitHub Actions:

- **Main Monitoring**: 6x daily (every 4 hours)
  - Detects anomalies across all plants
  - Sends system-wide status to <ktronicssolar@gmail.com>
  - Tracks customer-specific alerts (3-day auto-ignore)
  - Sends customer-specific alert emails to individual customers

- **Weekly Reports**: Every Sunday at 18:00 UTC
  - Sends personalized reports to customers with email addresses
  - No system notifications (silent operation)

### Required Secrets

Configure these in GitHub Settings → Secrets:

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
│       ├── shinemonitor_common.sh     # Shared configuration
│       ├── check_shinemonitor_monthly.sh  # Fetch monthly data
│       ├── check_shinemonitor_yearly.sh   # Fetch yearly data
│       ├── check_anomaly.py           # Anomaly detection + customer alert generation
│       ├── email_utils.py             # Shared email utilities
│       ├── generate_weekly_report.py  # Weekly report generation
│       ├── send_customer_emails.py    # Customer email sending (alerts & reports)
│       └── send_email.py              # System-wide email notifications
├── data/                              # CSV time-series data
├── alerts/                            # Generated alert reports
│   ├── alerts.json                    # System-wide alerts
│   ├── alerts.txt                     # Formatted alert report
│   └── customer_alerts.json           # Customer-specific alerts
├── reports/                           # Weekly customer reports
│   └── archive/                       # Report archives (last 4 weeks)
└── state/                             # State tracking
    └── alerts_state.json              # Unified alert state (admin & customer 3-day auto-ignore)
```

## Alert Format

Alerts are formatted with clear visual indicators:

```
================================================================================
║                          ⚠ ATTENTION REQUIRED ⚠                              ║
║                      Status: 1 CRITICAL, 0 WARNING                           ║
================================================================================

┌─ ALERT SUMMARY ──────────────────────────────────────────────────────────
│ 🔴 [RED] plant-name
│   Issue: Production dropped 85.5% below normal
│   Normal: 5.50 kWh/day  →  Current: 0.80 kWh/day
└────────────────────────────────────────────────────────

┌─ RECOMMENDED ACTIONS ────────────────────────────────────────────────────
│ plant-name:
│   1. Check inverter status and error codes
│   2. Verify grid connection and breaker status
│   3. Inspect panels for shading or physical damage
│   4. Contact maintenance team if issue persists
└──────────────────────────────────────────────────────
```

## Testing

### Integration Test Suite

The project includes comprehensive integration tests covering all business logic.

#### Test Coverage: 46 tests

- ✅ **UC1 (Admin Alerts): 15/15 PASSED (100%)**
- ✅ **UC2 (Customer Weekly): 11/11 PASSED (100%)**
- ✅ **UC3 (Device Alarms): 12/12 PASSED (100%)**
- ⏭️ **API Tests: 8/8 SKIPPED on Windows** (run in GitHub Actions/Linux)

```bash
# Run all tests
cd src/test/java/org/ktronics/scripts
py -m pytest integration/ -v

# Run specific test suite
py -m pytest integration/test_admin_alerts.py -v      # UC1: Admin alerts
py -m pytest integration/test_customer_weekly.py -v   # UC2: Customer reports
py -m pytest integration/test_device_alarms.py -v     # UC3: Device alarms
```

See [src/test/java/org/ktronics/scripts/README.md](src/test/java/org/ktronics/scripts/README.md) for detailed test documentation.

### Test Weekly Report Generation Locally

```bash
# Generate reports for all customers
python3 src/main/java/org/ktronics/scripts/generate_weekly_report.py \
  --credentials src/main/java/org/ktronics/config/credentials.json \
  --data-dir data \
  --output-dir reports

# View generated reports
ls -la reports/
cat reports/weekly_report_*.txt
```

### Test Customer Alert Tracking

```bash
# Process customer alerts
python3 src/main/java/org/ktronics/scripts/track_customer_alerts.py \
  --alerts alerts/alerts.json \
  --credentials src/main/java/org/ktronics/config/credentials.json \
  --state-file state/customer_alerts_state.json \
  --auto-ignore-days 3 \
  --output alerts/customer_alerts.json

# View customer alerts
cat alerts/customer_alerts.json
```

### Test Email Sending (Manual)

```bash
# Set SMTP credentials
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=your-email@gmail.com
export SMTP_PASS=your-app-password

# Send test weekly reports
python3 src/main/java/org/ktronics/scripts/send_customer_emails.py \
  --reports-dir reports
```

## Troubleshooting

### No Weekly Emails Received

1. Check customer has `email` field in credentials.json
2. Verify SMTP credentials in GitHub Secrets
3. Check workflow logs for errors
4. Verify plant naming matches customer label (case-insensitive)

### Alerts Not Auto-Ignoring

1. Check `state/customer_alerts_state.json` file
2. Verify `auto-ignore-days` is set to 3
3. Check main workflow includes customer alert tracking step
4. Review workflow logs for tracking step

### Plants Not Matching Customers

The system uses fuzzy matching based on customer labels:

- Customer: "Lahiru Ryan" → matches: `lahiru-ryan-*`, `lahiruryan-*`
- Customer: "Gayan-IMH" → matches: `gayan-imh-*`, `gayanim-*`

Ensure plant file names contain recognizable parts of the customer label.

## Contributing

This is an internal monitoring system. For issues or improvements, contact the development team.

## License

Proprietary - KTronics Development
