# Assignment 1: Repository Walkthrough — IoTDeviceMonitor

**Author:** Ishira Perera  
**Date:** September 21, 2026  
**Course / Context:** Industrial Training / KTronics IoT Device Monitoring System  

---

## 1. Supported Platforms & Their Data Pipeline Processes

The monitoring system continuously interacts with three distinct solar cloud platforms. Each platform follows a dedicated process to authenticate, extract operational metrics, and manage hardware state:

* **ShineMonitor (Production)**
  * **Core Scripts:** `shinemonitor_common.sh`, `check_shinemonitor_monthly.sh`, `check_shinemonitor_yearly.sh`
  * **Process:** Handles user authentication via standard ShineMonitor web credentials, executes periodic API calls to fetch time-series generation data, and parses daily/monthly yield records directly into plant-specific CSV storage files in the `data/` directory.

* **DessMonitor (Production - UC10)**
  * **Core Scripts:** `dessmonitor_common.sh`, `check_dessmonitor_monthly.sh`
  * **Process:** Operates in parallel with ShineMonitor using a separate cron schedule and credentials file (`dessmonitor_credentials.json`). It utilizes a dual-authentication fallback mechanism to maintain resilient session tokens before fetching monthly energy data, outputting state and CSV metrics prefixed with `dessmonitor-`.

* **SolisCloud (Production - UC13)**
  * **Core Scripts:** `solis_common.py`, `check_solis_switch.py`
  * **Process:** Serves as an automated watchdog for Solis inverters. Instead of simple web logins, it generates HMAC-SHA1 signed API requests via API Key ID and Secret. The process checks inverter state, detects non-producing instances during core daylight hours (or via control registers), automatically issues `/v2/api/control` switch-ON commands if latched OFF, and logs watchdog state to `state/solis_switch_state.json`.

  