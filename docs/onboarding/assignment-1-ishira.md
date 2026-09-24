# Assignment 1: Repository Walkthrough — IoTDeviceMonitor

**Author:** Ishira Perera  
**Date:** September 21, 2026  
**Course / Context:** Industrial Training / KTronics IoT Device Monitoring System

---

## 1. Supported Monitoring Platforms & Associated Scripts

| Platform         | Primary API Utility / Client Script                         | Scheduled Workflow File                      | Key Responsibilities                                                                                      |
| :--------------- | :---------------------------------------------------------- | :------------------------------------------- | :-------------------------------------------------------------------------------------------------------- |
| **ShineMonitor** | `src/main/java/org/ktronics/scripts/shinemonitor_common.sh` | `.github/workflows/trigger-shinemonitor.yml` | API authentication, session key extraction, monthly/yearly yield collection via bash API calls.           |
| **DessMonitor**  | `src/main/java/org/ktronics/scripts/dessmonitor_common.sh`  | `.github/workflows/trigger-dessmonitor.yml`  | Secondary cloud client featuring a dual-authentication fallback mechanism for DessMonitor API endpoints (UC10). |
| **SolisCloud**   | `src/main/java/org/ktronics/scripts/solis_common.py`        | `.github/workflows/trigger-solis-switch.yml` | HMAC-SHA1 signed REST client designed for live status fetching and automated inverter control operations (UC13). Associated TCs: `src/test/java/org/ktronics/scripts/test_solis_switch.py`. |

---

## 2. Anomaly Detection & Alerting Rules (UC1)

The anomaly detection engine evaluates historical production metrics stored in local CSV files against predefined baseline thresholds, which are centralized inside `src/main/java/org/ktronics/scripts/config.py` and processed in `check_anomaly.py`.

- **RED Alert (Severe Underperformance - UC1):**
  - **Trigger:** Daily production drops below **20% of the calculated baseline** for **3 consecutive days**.
  - **Significance:** Represents a severe fault condition (e.g., total string failure, tripped breaker, or heavy array shading).
- **ORANGE Alert (Chronic Underperformance - UC1):**
  - **Trigger:** Monthly production drops below **40% of the expected baseline** for **3 consecutive months**.
  - **Significance:** Indicates sustained degradation, uncleaned panels, or partial component failure accumulating over an extended operational window.

---

## 3. Auto-Ignore Mechanism & Alert Fatigue Prevention (UC1)

When a plant records **0 kWh production for an entire month (1 month)**, the detection engine automatically flags it as ignored in state tracking (`state/alerts_state.json`) and suppresses further notification dispatching (**UC1**).

### Operational Rationale

- **Prevents Alert Fatigue:** If a system is decommissioned, switched off for structural repairs, or experiencing a prolonged grid disconnection, repeating daily or 4-hourly alerts provides zero added value to operations and quickly causes technicians to overlook critical notices.
- **Reduces Noise & CI Cost:** Suppressing continuous notifications keeps system logs clean, minimizes unnecessary SMTP traffic, and protects email sending domain reputations.

---

## 4. The 3-Send Rule & State Tracking Mechanics (UC3–UC8)

The 3-send rule controls hardware and system device alarm notifications to prevent inbox flooding while maintaining operational visibility (**UC3–UC8**).

- **Mechanism (UC3):** When a hardware warning or error code is retrieved from an inverter API, the system dispatches the warning up to **3 distinct times** spaced at **4-hour intervals**.
- **Fourth Send Prevention (UC3):** Before attempting any dispatch, the script checks the persisted state file. If the recorded `send_count` attribute for that specific alarm key is equal to or greater than `3`, the email delivery module drops the message from the outgoing queue.
- **Email Alert Scope:** Out of the 26 tracked plants, email alerts are configured strictly for plants mapped with customer email accounts inside `config/credentials.json`. Unmapped plants are monitored but do not send emails.
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

4. **Anomaly Detection (UC1)**
   - Script: `check_anomaly.py`
   - Evaluates CSV data against RED (<20% for 3 days) and ORANGE thresholds

5. **Report & JSON Generation**
   - Files: `alerts/alerts.json`, `alerts/customer_alerts.json`
   - Exports active anomaly structures and mapped customer alert entries

6. **State Tracking & Auto-Ignore Update (UC1)**
   - File: `state/alerts_state.json`
   - Increments alert counters and marks 0-production plants as auto-ignored

7. **Customer Email Assembly (UC2, UC3)**
   - Script: `send_customer_emails.py`
   - Matches alerts to customer accounts and formats HTML emails

8. **SMTP Email Transmission**
   - Script: `send_email.py`
   - Connects to Gmail SMTP server using repository secrets and dispatches alerts

9. **Inbox Delivery**
   - Customer Email Inbox
   - Personalized alert notification received by customer

---

## 6. Admin vs. Customer Communications (UC1, UC2, UC8, UC9)

Instead of sending the same updates to everyone, the system separates communications into two distinct roles to ensure admins get complete system oversight while customers receive only clean, relevant reports.

### 1. System Administrator (`ktronicssolar@gmail.com`)

- **Core Purpose:** System-wide monitoring, infrastructure debugging, and change detection across all 26 installations.
- **What They Receive:**
  - **Global Anomaly Alerts (UC1):** Combined reports covering all active RED and ORANGE faults across the entire fleet.
  - **System Health Logs:** Notifications about unmapped solar plants, missing customer credentials, or API connection errors.
  - **State Hash Updates (UC9):** Alerts triggered whenever the global system state changes (via SHA256 hash comparison).
- **Delivery Schedule:** **On EVERY run** — triggered across scheduled 6x daily cron runs, manual workflow triggers, and git push events (for build verification).

### 2. End Customers (Mapped in `credentials.json`)

- **Core Purpose:** Individual performance tracking and localized hardware warning notices.
- **What They Receive:**
  - **Personalized Yield Reports (UC2):** Weekly progress emails sent every Sunday morning containing YTD (year-to-date), monthly, and 7-day production figures.
  - **Specific Anomaly Alerts (UC1):** Notifications sent only when their specific plant drops below 20% baseline for 3 consecutive days.
  - **Filtered Device Alarms (UC3):** Hardware error notifications specific to their equipment, controlled by the 3-send rule.
- **Delivery Schedule:** **STRICTLY on Scheduled Runs (UC8)** — customers are completely shielded from push triggers, manual test runs, and system-wide debug logs (UC8 filter).

---

### Comparison Summary

| Attribute             | System Admin (`ktronicssolar@gmail.com`)  | Individual Customers                               |
| :-------------------- | :---------------------------------------- | :------------------------------------------------- |
| **Data Scope**        | Fleet-wide (All 26 solar plants)          | Restricted to owned plant(s) mapped in credentials |
| **Workflow Triggers** | All triggers (Schedule, Manual, Git Push) | Scheduled runs ONLY (Schedule filter - UC8)        |
| **Content Type**      | Error logs, state hashes, global faults   | Weekly reports (UC2), device alarms (UC3), alerts  |
| **Noise Protection**  | Receives all operational updates          | Protected by 3-send rule (UC3) & schedule filters  |

---

## 7. Dataset Structure & Schema Analysis

- **Total Tracked Plants in `data/`:** **26 distinct solar plants** across ShineMonitor and DessMonitor platforms.
- **Daily CSV Column Headers:** `date, kwh, baseline_kwh`
- **Granularity of One Row:** Represents **one complete calendar day of energy generation** for a specific solar installation.

---

## 8. Production Bug Analysis: UC9 SHA256 Hash Timestamp Bug

* **What Broke:** The admin email optimization logic (**UC9**) was built to stop spamming administrators by suppressing repetitive alerts whenever system anomaly states stayed unchanged. However, admins were still receiving up to 6 duplicate emails every single day. The root cause was that `alerts/alerts.json` contained a dynamic `generated_at` timestamp parameter at the top level. Because this timestamp updated on every execution, computing the raw SHA256 hash of `alerts.json` produced a brand-new hash every single run—causing the system to falsely assume the alert state had changed.

* **How It Was Found:** The issue was identified in production when administrators reported receiving up to 6 duplicate alert emails daily even though no active solar plant alerts changed or cleared. Inspection of `state/admin_email_state.txt` revealed that stored SHA256 hash strings were continuously changing on scheduled cron executions because the dynamic `generated_at` timestamp in `alerts.json` altered the payload input on every run.

* **How It Was Fixed & Prevented:** The GitHub Actions workflow (`trigger-shinemonitor.yml`) was updated to pipe `alerts.json` through `jq` before hashing, stripping out dynamic execution timestamps and hashing only the structural content keys (`alerts`, `suppressed`, `ignored`). To permanently prevent regression, the integration test `src/test/java/org/ktronics/scripts/integration/test_uc9_admin_email.py` was introduced to verify that hash generation remains strictly deterministic across consecutive executions regardless of timestamp variations.

---

## 9. Concepts Requiring Further Clarification

1. What are the main objectives of having several platforms as Dessmonitor?  
2. What files need to be run manually apart from automation?  
3. Why has a Java-based file structure been used?  
4. What tasks are handled by Azure Functions in this project?  
5. What files need to be kept under regular observation?  
6. What issues and problems occurred earlier from the developers who worked on this repo (apart from bugs)?

---