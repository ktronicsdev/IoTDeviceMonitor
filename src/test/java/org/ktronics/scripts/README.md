# IoT Platform Integration Tests

Test suite for ShineMonitor, DessMonitor, and PH1000 inverter/BMS IoT monitoring systems.

## Quick Start

```bash
cd src/test/java/org/ktronics/scripts
py -m pytest integration/ -v
```

## Test Summary

**Total: 767 tests** (711 passed on Windows, 56 skipped - bash and API tests run in CI). CI reports 744: the Tuya module needs `requests`, which CI does not install, so it skips as a unit.

The count is what `py -m pytest integration/ -q` prints. If a change moves it,
update this file and the root `Readme.md` in the same commit.

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
| UC9  | Admin Email Optimization   | 9       | `test_uc9_admin_email.py`        |
| UC9  | Weekly all-clear cadence   | 12      | `test_all_clear_cadence.py`      |
| UC10 | DessMonitor Multi-Platform | 112     | `test_dessmonitor_*.py` (7 files)|
| UC11 | Plant ROI Analysis         | 19      | `test_uc11_plant_roi.py`         |
| UC12 | PH1000 Inverter + BMS      | 39      | `test_ph1000.py`                 |
| UC13 | Solis ON/OFF Watchdog      | 40      | `test_solis_switch.py`           |
| UC14 | Connectivity + Exclusions  | 62      | `test_connectivity.py`           |
| UC14 | Link-down state retention  | 3       | `test_connectivity_state_retention.py` |
| UC15 | PH1800 Live Dashboard      | 41      | `test_ph1800.py`                 |
| UC15 | PH1800 battery tab         | 8       | `test_ph1800_battery_tab.py`      |
| UC16 | ESP32 BMS module (DIY)     | 16      | `test_bms_ingest.py`             |
| BVT  | Dashboard Clocks (SL time) | 16      | `test_dashboard_clock.py`        |
| BVT  | Centralized Config         | 14      | `test_centralized_config.py`     |
| API  | ShineMonitor API           | 8       | `test_shinemonitor_api.py`       |
| API  | DessMonitor API            | 18      | `test_dessmonitor_api.py`        |

Not in the table above, because they pin workflow and push behaviour rather than
a single use case: `test_alarm_triage.py` (14) and `test_alarm_gate_wiring.py` (8)
for the six-hour gate, `test_admin_alert_send_cap.py` (4) and
`test_hourly_poll_scope.py` (5) for alerting cadence,
`test_data_commit_push.py` (27), `test_merge_alarm_state.py` (8) and
`test_no_bare_push_to_main.py` (10) for the data-commit push,
`test_no_payloads_in_public_logs.py` (6) for public-log hygiene, `test_feature_flags.py` (34), `test_fleet_benchmark.py` (28)
and the remaining BMS and dashboard suites. The root
[Readme.md](../../../../../../Readme.md) has the full breakdown.

## UC Descriptions

### UC1: Admin Production Alerts

Detects production anomalies and sends alerts to admin:

- RED alert: < 20% baseline for 3 consecutive days
- ORANGE alert: < 40% baseline for 3 consecutive months
- Auto-ignore: 0 production for 1 month
- 3 sends per alert, at least 4 hours apart (`test_admin_alert_send_cap.py`)

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
- "ALL SYSTEMS OPERATIONAL" only on the Monday 06:30 UTC run; a run with an
  actual alert in it is never gated (`test_all_clear_cadence.py`)

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

### UC14: Connectivity + Exclusions

Separates "the data link died" from "the system died", and makes the fleet count honest:

- A plant with no new reading for N days (`--stale-days`, default 3) raises a CONNECTIVITY
  alert saying the LINK is down — explicitly NOT a production fault, and never written off
  as a dead system. `check_anomaly.py` reclassifies such a plant instead of firing RED.
- Symptoms are distinguished: rows still arriving but all zero (dongle offline) vs no rows
  at all (the portal has nothing for us) vs never reported.
- Fleet-wide silence is reported once, as our own collector/API failure, not as N customer
  faults — so no customer's alert budget is burned on a fault at our end.
- Alerts follow the device-alarm discipline: 3 sends, 4 hours apart, then auto-ignored;
  state clears when a plant is positively seen reporting again. A plant merely absent from
  a run is NOT treated as recovered — that reset the send counter for ever
  (`test_connectivity_state_retention.py`).
- `config/excluded_plants.json` names plants deliberately off the platform. They are skipped
  by alarms, weekly reports and the summary counts, and every skip is logged with its reason.
- The admin summary states three counts plainly: reporting, dead link, excluded.

### UC15: PH1800 Live Dashboard (generic, all inverters)

A live per-plant dashboard for any ShineMonitor inverter — a **pure pass-through**, unlike
UC12's PH1000 which is a bespoke workaround for one broken feed. No computed fields at all,
so a wrong number on the dashboard is the vendor's number.

- One JSON per account at `sha256(username:password).json`: the repository is public, so a
  plant's data file must not be enumerable without its credentials.
- Pages are copies of `site/ph1800/_app.html`, re-stamped when they drift so a template
  change reaches every plant. `_app.html` is the hand-authored source.
- Live (7-day) and history (30-day) run on separate schedules and separate branches.
  Fetching the 30-day window on the live cycle is what made the dashboards go stale in
  August 2026; `test_live_loop_budget.py` guards both halves.
- The live loop runs to a wall-clock budget, not an iteration count: a fifth plant once
  pushed a fixed 26-iteration loop past the job timeout and the tail never published.

### UC16: ESP32 BMS Module (DIY battery monitoring)

An ESP32 reads a JK-compatible BMS over BLE and pushes readings here by
`repository_dispatch` - device-driven, not cron. Proof of concept, one trial site.

- The 10-second data stays on a LAN dashboard at the device; only a 15-minute sample goes
  to the cloud, which is what keeps this to ~96 workflow runs per device per day.
- Thresholds (SOC, pack under/overvoltage, temperature, current, cell imbalance) each have
  an environment override, and an alert re-fires at most every 4 hours.
- The only caller of `push_data_commit.sh --fail-on-conflict`: a reading is an observation
  that happened, so a race must fail loudly rather than drop one.
- `test_bms_ingest.py` is NOT a UC12 test despite the name - UC12 is the PH1000/PACEEX BMS
  decode, this is the ESP32 path.

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
