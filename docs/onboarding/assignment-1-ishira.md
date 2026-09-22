# Assignment 1: Repository Walkthrough — IoTDeviceMonitor

**Author:** Ishira Perera  
**Date:** September 21, 2026  
**Course / Context:** Industrial Training / KTronics IoT Device Monitoring System

---

## 1. Supported Monitoring Platforms & Associated Scripts

| Platform         | Primary API Utility / Client Script                         | Scheduled Workflow File                      | Key Responsibilities                                                                                      |
| :--------------- | :---------------------------------------------------------- | :------------------------------------------- | :-------------------------------------------------------------------------------------------------------- |
| **ShineMonitor** | `src/main/java/org/ktronics/scripts/shinemonitor_common.sh` | `.github/workflows/trigger-shinemonitor.yml` | API authentication, session key extraction, monthly/yearly yield collection via bash API calls.           |
| **DessMonitor**  | `src/main/java/org/ktronics/scripts/dessmonitor_common.sh`  | `.github/workflows/trigger-dessmonitor.yml`  | Secondary cloud client featuring a dual-authentication fallback mechanism for DessMonitor API endpoints.  |
| **SolisCloud**   | `src/main/java/org/ktronics/scripts/solis_common.py`        | `.github/workflows/trigger-solis-switch.yml` | HMAC-SHA1 signed REST client designed for live status fetching and automated inverter control operations. |

---

## 2. Anomaly Detection & Alerting Rules

The anomaly detection engine evaluates historical production metrics stored in local CSV files against predefined baseline thresholds.

- **RED Alert (Severe Underperformance):**
  - **Trigger:** Daily production drops below **20% of the calculated baseline** for **3 consecutive days**.
  - **Significance:** Represents a severe fault condition (e.g., total string failure, tripped breaker, or heavy array shading).
- **ORANGE Alert (Chronic Underperformance):**
  - **Trigger:** Monthly production drops below **40% of the expected baseline** for **3 consecutive months**.
  - **Significance:** Indicates sustained degradation, uncleaned panels, or partial component failure accumulating over an extended operational window.

---

## 3. Auto-Ignore Mechanism & Alert Fatigue Prevention

When a plant records **0 kWh production for an entire month (1 month)**, the detection engine automatically flags it as ignored in state tracking and suppresses further notification dispatching.

### Operational Rationale

- **Prevents Alert Fatigue:** If a system is decommissioned, switched off for structural repairs, or experiencing a prolonged grid disconnection, repeating daily or 4-hourly alerts provides zero added value to operations and quickly causes technicians to overlook critical notices.
- **Reduces Noise & CI Cost:** Suppressing continuous notifications keeps system logs clean, minimizes unnecessary SMTP traffic, and protects email sending domain reputations.

---

## 4. The 3-Send Rule & State Tracking Mechanics

The 3-send rule controls hardware and system device alarm notifications to prevent inbox flooding while maintaining operational visibility.

- **Mechanism:** When a hardware warning or error code is retrieved from an inverter API, the system dispatches the warning up to **3 distinct times** spaced at **4-hour intervals**.
- **Fourth Send Prevention:** Before attempting any dispatch, the script checks the persisted state file. If the recorded `send_count` attribute for that specific alarm key is equal to or greater than `3`, the email delivery module drops the message from the outgoing queue.
- **State Persistence File:** `state/device_alarms_state.json`
- **Human-Readable Schema:** Stores contextual tracking metadata per alarm instance, including:
  - `customer`: Account or owner identifier (e.g., `"Gayan-IMH"`).
  - `plant`: Specific installation name (e.g., `"imbulgoda 3kw"`).
  - `message`: Raw alarm description string (e.g., `"Low battery"`).
  - `send_count`: Numerical incrementer tracking historical deliveries.
  - `last_sent`: ISO-8601 timestamp of the last successful email transmission.

---

## 5. End-to-End Alert Execution Trace

Below is the execution path when a solar installation underperforms on a Tuesday:

### End-to-End Alert Execution Trace

1. **GitHub Actions Trigger**
   - File: `.github/workflows/trigger-shinemonitor.yml`
   - Runs on schedule 6x daily (or via manual/push event)

2. **Data Fetching**
   - Script: `check_shinemonitor_monthly.sh`
   - Authenticates with ShineMonitor API and retrieves monthly yield metrics

3. **Local Data Persistence**
   - File: `data/{plant}-YYYY-MM.csv`
   - Appends/updates time-series daily yield values (date, kwh, baseline_kwh)

4. **Anomaly Detection**
   - Script: `check_anomaly.py`
   - Evaluates CSV data against RED (<20% for 3 days) and ORANGE thresholds

5. **Report & JSON Generation**
   - Files: `alerts/alerts.json`, `alerts/customer_alerts.json`
   - Exports active anomaly structures and mapped customer alert entries

6. **State Tracking & Auto-Ignore Update**
   - File: `state/alerts_state.json`
   - Increments alert counters and marks 0-production plants as auto-ignored

7. **Customer Email Assembly**
   - Script: `send_customer_emails.py`
   - Matches alerts to customer accounts and formats HTML emails

8. **SMTP Email Transmission**
   - Script: `send_email.py`
   - Connects to Gmail SMTP server using repository secrets and dispatches alerts

9. **Inbox Delivery**
   - Customer Email Inbox
   - Personalized alert notification received by customer

---
