# ShineMonitor IoT Device Monitoring System

[![GitHub Actions](https://github.com/ktronicsdev/IoTDeviceMonitor/workflows/ShineMonitor%20Daily%20Monitor/badge.svg)](https://github.com/ktronicsdev/IoTDeviceMonitor/actions)

## Description

Automated monitoring system for ShineMonitor solar panel installations with intelligent anomaly detection and email alerting.

**Features:**

- 🔄 Automated data collection from 20+ solar plants
- 📊 Daily and monthly production tracking
- 🚨 Smart anomaly detection with RED/ORANGE severity levels
- 📧 Email alerts with actionable recommendations
- 📈 Historical data retention and trending
- ⏰ Scheduled monitoring (2x daily via GitHub Actions)

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
      "label": "Plant Name",
      "username": "your_username",
      "password": "your_password"
    }
  ]
}
```

### 3. Run data collection manually

```bash
# Fetch monthly data
./src/main/java/org/ktronics/scripts/check_shinemonitor_monthly.sh \
  src/main/java/org/ktronics/config/credentials.json 2025-12

# Run anomaly detection
python3 src/main/java/org/ktronics/scripts/check_anomaly.py \
  --data-dir data \
  --out-dir alerts \
  --state-file state/alerts_state.json
```

### 4. View alerts

```bash
cat alerts/alerts.txt
```

## GitHub Actions Setup

The system runs automatically via GitHub Actions twice daily (4:30 AM & 4:30 PM UTC).

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
│   └── trigger-shinemonitor.yml    # Automated workflow
├── src/main/java/org/ktronics/
│   ├── config/
│   │   └── credentials.json        # API credentials (gitignored)
│   └── scripts/
│       ├── shinemonitor_common.sh  # Shared configuration
│       ├── check_shinemonitor_monthly.sh  # Fetch monthly data
│       ├── check_shinemonitor_yearly.sh   # Fetch yearly data
│       ├── check_anomaly.py        # Anomaly detection
│       └── send_email.py           # Email notifications
├── data/                           # CSV time-series data
├── alerts/                         # Generated alert reports
└── state/                          # Alert state tracking
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

## Contributing

This is an internal monitoring system. For issues or improvements, contact the development team.

## License

Proprietary - KTronics Development
