# IoT Platform Integration Tests

Test suite for ShineMonitor, DessMonitor, and PH1000 inverter/BMS IoT monitoring systems.

## Quick Start

```bash
cd src/test/java/org/ktronics/scripts
py -m pytest integration/ -v
```

## Test Summary

**Total: 409 tests** (353 passed on Windows, 56 skipped - bash tests run in CI)

## Use Cases

| UC   | Name                       | Tests   | File                             |
|------|----------------------------|---------|----------------------------------|
| UC1  | Admin Production Alerts    | 15      | `test_admin_alerts.py`           |
| UC2  | Customer Weekly Reports    | 15      | `test_customer_weekly.py`        |
| UC3  | Device Alarm Notifications | 38      | `test_device_alarms.py`          |
| UC4  | Test Customer Mode         | (in UC3)| `test_device_alarms.py`          |
| UC5  | Test Mode Filter           | (in UC3)| `test_device_alarms.py`          |
| UC6  | Log Summary Counts         | (in UC3)| `test_device_alarms.py`          |
| UC7  | State JSON Enhancement     | (in UC3)| `test_device_alarms.py`          |
| UC8  | Weekly Report Schedule     | (in UC3)| `test_device_alarms.py`          |
| UC9  | Admin Email Optimization   | 10      | `test_uc9_admin_email.py`        |
| UC10 | DessMonitor Multi-Platform | 50      | `test_dessmonitor_integration.py`|
| UC11 | Plant ROI Analysis         | 19      | `test_uc11_plant_roi.py`         |
| UC12 | PH1000 Inverter + BMS      | 21      | `test_ph1000.py`                 |
| BVT  | Dashboard Clocks (SL time) | 16      | `test_dashboard_clock.py`        |
| BVT  | Centralized Config         | 14      | `test_centralized_config.py`     |
| API  | ShineMonitor API           | 9       | `test_shinemonitor_api.py`       |
| API  | DessMonitor API            | 13      | `test_dessmonitor_api.py`        |

## UC Descriptions

### UC1: Admin Production Alerts

Detects production anomalies and sends alerts to admin:

- RED alert: < 20% baseline for 3 consecutive days
- ORANGE alert: < 40% baseline for 3 consecutive months
- Auto-ignore: 0 production for 1 month

### UC2: Customer Weekly Reports

Generates weekly production reports for customers:

- Weekly/monthly/yearly totals
- Previous period comparisons
- Multi-plant aggregation

### UC3-UC8: Device Alarms

Monitors device alarms and notifies admin/customers:

- 3-send rule (max 3 notifications per alarm)
- 4-hour interval between sends
- Test customer mode (ShineMonitor: Gayan-IMH, DessMonitor: Mifrazmarso)
- Customer-specific alarm mapping

### UC9: Admin Email Optimization

Hash-based duplicate email prevention:

- Only sends when alert state changes
- Ignores timestamp changes

### UC10: DessMonitor Multi-Platform

Full DessMonitor API integration:

- Device-level energy queries
- Yearly/monthly data collection
- Platform filtering

### UC11: Plant ROI Analysis

Calculates return on investment for solar plants:

- Off-grid energy consumption tracking
- Cost savings calculations

### UC12: PH1000 Inverter + BMS

Live MUST PH1000 inverter + Hystorix/PACEEX battery monitoring:

- Cloud field mapping (Charger Current/Power = real battery current/power; garbage columns ignored)
- Energy-balance derivation: `PV = PInverter`, `Load = max(0, PInverter + ChargerPower − 40 W)`
- Charge-state sign logic (ChargerPower < 0 = charging)
- BMS `WIFI_Band` hex-frame decode (SOC/V/A/SOH/capacity/cycles), validated against the live app
- CSV append/dedup, account opt-in (`"ph1000": true`), dashboard JSON shape

### BVT: Dashboard Clocks (SL time)

Guards the two times in the dashboard header - the browser-computed "Actual time (SL)"
and the plant-local "Last update" stamp - on both `site/ph1000/index.html` and
`site/ph1800/_app.html`. Sri Lanka is UTC+05:30 year-round, so the conversion is done
arithmetically rather than via the browser's IANA zone database.

- Static: the offset is the fixed `SL_MS`, ph1000's clock never touches `Intl`, ph1800
  probes `Intl` once (`TZ_OK`) and falls back, and every ph1800 plant page carries the fix
- Rendered (needs node): runs each page's real JS in a vm under a frozen clock and reads
  the header back - from five viewer timezones, and against a browser that ignores IANA
  zones (which is what showed 11:27:53 UTC beside a 16:53 SL device stamp on 2026-09-07)
- Staleness: a fresh device stamp must read a small *positive* age, so a stale plant can
  never keep a green "Online" dot when the page is opened outside Colombo
- Includes a guard-on-the-guard: the pre-fix page is rebuilt and the harness must fail on it

## Run Specific Tests

```bash
# Single UC
py -m pytest integration/test_admin_alerts.py -v

# Single test
py -m pytest integration/test_device_alarms.py::TestDeviceAlarmSystem::test_filter_alarms_first_send -v

# With coverage
py -m pytest integration/ --cov=../../../../../main/java/org/ktronics/scripts --cov-report=html
```

## Notes

- API tests (bash) skip on Windows, run in GitHub Actions
- All tests use temp directories and clean up after themselves
- Tests mock datetime for reproducibility
