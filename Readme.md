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
| SolisCloud   | `soliscloud.com:13333`  | Production (UC13)|

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

## Design

This section is the design of record. The use cases below say *what* the system
does; this says *how it is put together*, and which rules may not be broken.
**Keep it current in the same commit as the code** — a use case that changes its
state file, its flag or its schedule changes this section too.

### One run, five steps

Every scheduled run does the same five things in the same order:

```
  ShineMonitor API ─┐
  DessMonitor  API ─┼─► COLLECT   check_*_monthly.sh / _yearly.sh   (bash, per platform)
  SolisCloud   API ─┘             └─► data/{plant}-YYYY-MM.csv      date,kwh
                                      data/{plant}-YYYY.csv         month,kwh
                                                │
                                                ▼
                                  CLASSIFY  (python, in this order)
                                  1. exclusions.py    is this plant still ours?
                                  2. connectivity.py  is the data link alive?
                                  3. check_anomaly.py is production where it should be?
                                                │
                                                ▼
                                  DECIDE    features.py  — is this channel live?
                                            state/*.json — sent already, how often?
                                                │
                                                ▼
                                  NOTIFY    send_email.py           (admin)
                                            send_customer_emails.py (customers)
                                                │
                                                ▼
                                  REMEMBER  state/*.json, committed back by the
                                            workflow so the next run knows
```

### Why that order

Exclusions and connectivity run **before** any production judgement, and that is
the most important decision in the system.

A plant whose WiFi or monitoring dongle is offline records `0.0000` kWh every
day — byte for byte what a dead inverter looks like in the CSV. For months the
platform could not tell those apart and reported healthy systems as failed ones.
So:

- A plant with **no new reading for `--stale-days` days (default 3)** is
  classified **LINK DOWN** and reported as a *connectivity* problem. It is never
  turned into a RED production alert.
- A plant on the **exclusion list** is off the platform entirely: no alarms, no
  report, no place in the counts. Exclusion is a *business decision*, never a
  symptom.
- Only what survives both checks is judged on its production.

### Components

| File | Job |
|---|---|
| `check_shinemonitor_monthly.sh` / `_yearly.sh` / `_daily.sh` | Collect ShineMonitor production into `data/` |
| `check_dessmonitor_monthly.sh` / `_yearly.sh` | Collect DessMonitor production (UC10) |
| `check_device_alarms.sh`, `check_dessmonitor_device_alarms.sh` | Fetch device alarms into `alarms/` |
| `shinemonitor_common.sh`, `dessmonitor_common.sh`, `common_config.sh` | Signed API calls, shared paths |
| `exclusions.py` | Who is still on the platform. Reads `excluded_plants.json` |
| `connectivity.py` | Link alive or dead, and why. The fleet counts |
| `check_connectivity.py` | UC14 entry point: CONNECTIVITY alert and its state |
| `check_anomaly.py` | RED/ORANGE production alerts, per platform |
| `generate_device_alarms.py` | UC3 alarm processing, admin and customer emails |
| `generate_weekly_report.py` | UC2 customer weekly reports |
| `generate_admin_summary.py` | Admin roll-up, including the three coverage counts |
| `send_email.py`, `send_customer_emails.py`, `email_utils.py` | Delivery |
| `features.py` | Which channels are live |
| `config.py` | The one place `credentials.json` is located |
| `fleet_benchmark.py` | Weather-adjusted benchmark for production disputes |
| `check_solis_switch.py`, `solis_common.py` | UC13 SolisCloud watchdog |
| `check_ph1000*.py`, `check_ph1800.py` | UC12 live inverter and BMS dashboards |

### Where the truth lives

| File | Holds | If it is missing or broken |
|---|---|---|
| `config/credentials.json` | ShineMonitor accounts, plants, customer emails, the `solis` block | Gitignored. In CI it comes from `SHINEMONITOR_CREDENTIALS_JSON` |
| `config/dessmonitor_credentials.json` | DessMonitor accounts (UC10) | Gitignored. From `DESSMONITOR_CREDENTIALS_JSON` |
| `config/features.json` | Which emails and alerts are live | **Fails open** — an unknown flag reads as ON, so adding a channel never silently mutes an existing one. Override for one run with `KT_FEATURE_<DOTTED_PATH>=true` |
| `config/excluded_plants.json` | Plants deliberately dropped, each with a reason and a date | **Fails closed** — it excludes nothing. Losing the file must never silently drop a paying customer off the platform |

Matching in `exclusions.py`, and in the plant-to-customer mapping, is on a
normalised key — lower-cased with every non-alphanumeric character stripped — so
`Gayan-IMH`, `gayan imh` and `gayanimh` are the same thing.

### State, and the sending discipline

| File | Remembers |
|---|---|
| `state/alerts_state.json` | Production alerts sent, keyed `plant:severity` |
| `state/dessmonitor_alerts_state.json` | The same, for DessMonitor |
| `state/device_alarms_state.json` | Device alarms: `send_count`, `last_sent`, `ignored`, plus `customer`, `plant`, `message` |
| `state/connectivity_state.json` | Link-down alerts per plant, and the fleet alert |
| `state/solis_switch_state.json` | SolisCloud watchdog |
| `state/admin_email_state.txt` | UC9 content hash, so an unchanged admin email is not resent |

Every repeating alert channel obeys the same rule: **at most 3 sends, at least 4
hours apart, then auto-ignored.** A customer who never fixes their WiFi cannot
flood the inbox for ever. When the plant reports again its state is cleared, so
the next outage starts a fresh cycle.

### Data conventions

- `data/{plant}-YYYY-MM.csv` — daily rows, `date,kwh`
- `data/{plant}-YYYY.csv` — monthly rows, `month,kwh`
- `data/dessmonitor-{plant}-*.csv` — the prefix is the platform boundary. Every
  reader filters on it, so the two platforms never mix in a report or a count
- **Today is never analysed.** Collection runs mid-day, so the current date is a
  half-filled row that reads as a cloudy day that never happened

### Schedule

| Workflow | Runs | Does |
|---|---|---|
| `trigger-shinemonitor.yml` | 6x daily, 02:30 UTC and every 4 h after | Collect, connectivity, anomalies, device alarms, admin email |
| `trigger-dessmonitor.yml` | Daily, 03:00 UTC | The same for DessMonitor (UC10) |
| `trigger-customer-reports.yml` | Sunday, 04:00 UTC | UC2 weekly reports and the admin summary |
| `trigger-dessmonitor-weekly-reports.yml` | Sunday, 04:00 UTC | UC2 for DessMonitor |
| `trigger-ph1000.yml`, `trigger-ph1800.yml` | Every 2 h, restarting a self-chaining loop | UC12 live dashboards |
| `trigger-ph1000-hist.yml`, `trigger-ph1800-hist.yml` | Hourly | UC12 30-day history, on its own branch |
| `trigger-solis-switch.yml` | **Schedule commented out** — manual only until the Solis Control API key is added | UC13 watchdog |
| `trigger-bms-ingest.yml` | On demand | BMS cell data |

Customer-facing email goes out **only on scheduled runs**; admin email goes out
on every trigger, including a developer's push (UC8). The test customer
Gayan-IMH is the exception and receives on all triggers (UC4), which is how a
change is proven end to end without mailing the customer base.

### Rules that may not be broken

1. **A dead link is never a dead system.** Silence is a connectivity finding.
2. **An exclusion is a decision, not a symptom.** A quiet plant stays monitored
   and stays in the counts; only a plant the business has dropped is excluded.
3. **Feature flags fail open, exclusions fail closed.** Opposite directions, on
   purpose: in each case the failure that shows is preferred to the one that hides.
4. **Three sends, four hours apart, then quiet** — for every repeating alert.
5. **Platform data never mixes.** Filter on the `dessmonitor-` prefix.
6. **Credentials never enter the repo.** They arrive from GitHub secrets.
7. **Every skip is logged with its reason**, so nobody has to wonder where a
   plant went.

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

### UC12: PH1000 Inverter Live Dashboard + Battery BMS

Live GitHub Pages dashboard for the **MUST PH1000** hybrid inverter (Mifanza "Mifanza 5KW" plant),
with **true battery SOC** read directly from the cracked **Hystorix/PACEEX BMS** cloud API.

**Implementation Status:** ✅ **IMPLEMENTED** (Session 16)

**Live dashboard:** <https://ktronicsdev.github.io/IoTDeviceMonitor/site/ph1000/>
(dark theme, energy-flow animation, per-day/per-month charts, Battery Profile pane, login-gated).

**Architecture (isolated from main monitoring):**

- [`trigger-ph1000.yml`](.github/workflows/trigger-ph1000.yml) publishes `ph1000_live.json` to a
  dedicated **`ph1000-live` branch** (rolling force-push) — it **never commits to `main`**, so it
  never triggers the ShineMonitor/DessMonitor workflows (no email spam / wasted CI).
- The dashboard (served from `main` at `site/ph1000/`) fetches that JSON via
  `raw.githubusercontent.com` and self-refreshes every 60 s.
- Refresh cadence: a local **Windows Task Scheduler** job (`PH1000Refresh`, every 10 min) runs
  `gh workflow run` — reliable where GitHub's `*/5` cron is throttled.

**Inverter data ([`check_ph1000.py`](src/main/java/org/ktronics/scripts/check_ph1000.py)):**

- ShineMonitor mis-parses devcode 697, so only verified fields are used: Battery Voltage,
  Charger Current/Power (= **real** battery current/power), PInverter, work state.
- Energy balance is **estimated** (flagged): `PV = PInverter`,
  `Load = max(0, PInverter + ChargerPower − 40 W)` (40 W inverter self-use).

**Battery BMS ([`check_ph1000_bms.py`](src/main/java/org/ktronics/scripts/check_ph1000_bms.py)):**

- True per-pack telemetry (B1 master + B2 slave): SOC, voltage, signed current, SOH, capacity,
  cycles, cells, temps — via the reverse-engineered Aliyun IoT / PACEEX API
  (full write-up: [README_BMS.md](src/main/java/org/ktronics/scripts/README_BMS.md)).
- **Live SOC = average(B1, B2)**. If the BMS session token expires the fetcher fails soft
  (`ok:false`) → dashboard hides the Battery Profile pane and falls back to a voltage-based SOC.

**Module docs:**

- [README_PH1000.md](src/main/java/org/ktronics/scripts/README_PH1000.md) — inverter field map,
  derivation rules, MUST Modbus reference.
- [README_BMS.md](src/main/java/org/ktronics/scripts/README_BMS.md) — how the Hystorix BMS cloud
  API was cracked (ADB root, Frida SSL-unpinning, Aliyun APIGW signing, `WIFI_Band` decode).

**Test Coverage:** 21/21 PASSED (100%) — `test_ph1000.py`

---

### UC13: SolisCloud Inverter ON/OFF Watchdog

Watchdog for **SolisCloud** inverters that detects when an inverter has been switched **OFF** and (when the Control API is configured) automatically switches it back **ON**, emailing the admin.

**Implementation Status:** ✅ **IMPLEMENTED** (Session 17) — detection live; auto switch-on activates once the Solis API key is added.

**Motivation:** The **Ktronics Imbulgoda** plant latched **OFF for 4 days** after repeated `Uac-Unstable` (code 1019) grid faults, recovering only when manually powered on in the SolisCloud app. This watchdog catches that within ~2 hours instead of days.

**How it works:**

1. Runs every 2 hours ([trigger-solis-switch.yml](.github/workflows/trigger-solis-switch.yml)).
2. Reads each inverter's live state via the HMAC-SHA1-signed SolisCloud API.
3. Decides if it's OFF — two modes:
   - **Authoritative:** if `onoff_cid` is configured, reads the on/off control register (`/v2/api/atRead`).
   - **Heuristic (default, no Control permission needed):** during the core daylight window (09:00–15:00 local), fresh telemetry showing `pac == 0` ⇒ not producing. Skips at night / on stale data to avoid false triggers.
4. If OFF: auto-enable via `/v2/api/control` and email *"auto re-enabled"*; otherwise email *"switch on manually"* (throttled to once / 12 h).

**Platform note:** SolisCloud uses an **API Key ID + Secret** (SolisCloud → Service → API Management), **not** the web login. Remote on/off needs the **Control API** permission (Ktronics AB is the Solis distributor, so it can self-enable this).

**Scripts:**

| Script | Purpose |
|--------|---------|
| `solis_common.py` | SolisCloud signed-request client (`SolisClient`) |
| `check_solis_switch.py` | Detect OFF → enable → email watchdog |

**Config:** `solis` block in `credentials.json` (see [solis/README_SOLIS.md](src/main/java/org/ktronics/scripts/solis/README_SOLIS.md)).

**State File:** `state/solis_switch_state.json`

**CLI:**

```bash
py check_solis_switch.py            # check + act + email (cron entrypoint)
py check_solis_switch.py --dry-run  # detect + email, never send control
py check_solis_switch.py --status   # print live state, no action
py check_solis_switch.py --list     # list inverters on the account
py check_solis_switch.py --discover <INVERTER_ID>   # find the on/off cid
```

**Test Coverage:** 40/40 PASSED (100%) — `test_solis_switch.py`

---

### UC14: Connectivity (Link Down) + Plant Exclusions

Separates **"the data link died"** from **"the system died"**, and makes the fleet counts honest.

**Implementation Status:** ✅ **IMPLEMENTED** (Session 18)

**Motivation:** A plant whose WiFi or monitoring dongle is offline writes `0.0000` kWh every day, exactly like a plant whose inverter has failed. The platform could not tell them apart, so customers with nothing worse than a dead router were being counted — and nearly reported — as failed systems. Their solar is usually fine.

**How it works:**

1. `connectivity.py` reads the same CSV fleet the production checks read and classifies every plant:

   | Kind | Meaning |
   |------|---------|
   | `reporting` | Data is arriving normally |
   | `no_rows` | The portal has no data rows for this plant at all |
   | `zeros_only` | Rows still arrive, every reading is `0.0000` kWh |
   | `never_reported` | No non-zero reading in its whole history |

2. A plant with no new reading for `--stale-days` days (**default 3**, which is about six missed collection runs) is **LINK DOWN**.
3. `check_connectivity.py` writes `alerts/connectivity.txt` and exits **2**, which is what gates the admin email. The email says in as many words that this is a connectivity problem and **not** a production fault.
4. `check_anomaly.py` reclassifies a link-down plant instead of firing RED, so no customer's 3-alert budget is spent on a fault at our end.
5. `generate_admin_summary.py` prints the three counts the business is actually asked for: **plants reporting**, **plants with a dead link**, **plants excluded** — and monitored = reporting + dead link.

**Exclusions:** `config/excluded_plants.json` is the single list of plants deliberately taken off the platform, each with a `reason` and a `since` date. An excluded plant is skipped by device alarms, production alerts, weekly reports and the counts, and every skip is printed in the log. A plant that has merely gone quiet is **not** excluded — it stays monitored and shows as LINK DOWN. The list currently ships **empty**: no customer has been dropped.

**Alert discipline:** the same as device alarms — 3 sends per plant, at least 4 hours apart, then auto-ignored. A plant that starts reporting again has its state cleared, so the next outage alerts from scratch.

**Feature flags:** `emails.connectivity_admin`, `alerts.connectivity_link_down`.

**State File:** `state/connectivity_state.json`

**Scripts:**

| Script | Purpose |
|--------|---------|
| `connectivity.py` | Classify the fleet: link alive, dead, or excluded |
| `exclusions.py` | Read and match `excluded_plants.json` |
| `check_connectivity.py` | Entry point: alert, state, exit code 2 |

**CLI:**

```bash
python3 src/main/java/org/ktronics/scripts/check_connectivity.py \
  --data-dir data \
  --out-dir alerts \
  --state-file state/connectivity_state.json \
  --stale-days 3 \
  --platform shinemonitor
```

**Exit codes:** `0` nothing to send, `2` connectivity alerts to send.

**Test Coverage:** 62/62 PASSED (100%) — `test_connectivity.py`

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

- `SHINEMONITOR_CREDENTIALS_JSON`: ShineMonitor account credentials (also holds the `solis` block for UC13)
- `DESSMONITOR_CREDENTIALS_JSON`: DessMonitor account credentials (UC10)
- `BMS_IOT_TOKEN`: PH1000 battery BMS session token — Aliyun IoT (UC12; refresh when expired)
- `BMS_APPSECRET`: PH1000 BMS Aliyun API-Gateway app secret (UC12)
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
│   ├── trigger-ph1000.yml             # PH1000 live dashboard + BMS (~10 min) - UC12
│   ├── trigger-solis-switch.yml       # SolisCloud ON/OFF watchdog (2h) - UC13
│   └── trigger-customer-reports.yml   # Weekly reports (Sunday)
├── src/main/java/org/ktronics/
│   ├── config/
│   │   ├── credentials.json           # ShineMonitor credentials (gitignored)
│   │   ├── dessmonitor_credentials.json # DessMonitor credentials (gitignored)
│   │   ├── features.json              # Which emails and alerts are live
│   │   └── excluded_plants.json       # Plants deliberately off the platform (UC14)
│   └── scripts/
│       ├── config.py                  # Centralized Python config
│       ├── common_config.sh           # Centralized Bash config
│       ├── shinemonitor_common.sh     # ShineMonitor API utilities
│       ├── dessmonitor_common.sh      # DessMonitor API utilities (UC10)
│       ├── check_shinemonitor_monthly.sh  # ShineMonitor monthly data
│       ├── check_shinemonitor_yearly.sh   # ShineMonitor yearly data
│       ├── check_dessmonitor_monthly.sh   # DessMonitor monthly data (UC10)
│       ├── check_device_alarms.sh     # Fetch device alarms
│       ├── features.py                # Feature flags (fails open)
│       ├── exclusions.py              # Plant exclusions (fails closed) (UC14)
│       ├── connectivity.py            # Link-down classification + counts (UC14)
│       ├── check_connectivity.py      # CONNECTIVITY alert entry point (UC14)
│       ├── fleet_benchmark.py         # Weather-adjusted production benchmark
│       ├── check_anomaly.py           # Anomaly detection (multi-platform)
│       ├── generate_device_alarms.py  # Device alarm processing
│       ├── generate_weekly_report.py  # Weekly report generation
│       ├── generate_admin_summary.py  # Admin summary generation
│       ├── send_customer_emails.py    # Customer email sending
│       ├── send_email.py              # System-wide email notifications
│       ├── solis_common.py            # SolisCloud signed-request client (UC13)
│       ├── check_solis_switch.py      # SolisCloud ON/OFF watchdog (UC13)
│       └── solis/README_SOLIS.md      # SolisCloud setup + Control API guide (UC13)
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
    ├── device_alarms_state.json       # Device alarm state
    ├── connectivity_state.json        # Link-down alert state (UC14)
    └── solis_switch_state.json        # SolisCloud watchdog state (UC13)
```

## Testing

### Integration Test Suite

The project includes comprehensive integration tests covering all business logic.

#### Test Coverage: 545 tests — 489 passed, 56 skipped on Windows (bash and API tests run in CI)

**Live inverters + BMS (150 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC12 (PH1000) | 39 | `test_ph1000.py` |
| UC12 (PH1800) | 41 | `test_ph1800.py` |
| UC12 (PH1800 battery tab) | 8 | `test_ph1800_battery_tab.py` |
| UC12 (BMS ingest) | 16 | `test_bms_ingest.py` |
| UC12 (BMS cell frames) | 9 | `test_bms_cell_frame.py` |
| UC12 (BMS carry-forward) | 5 | `test_bms_carry_forward.py` |
| UC12 (dashboard clock) | 16 | `test_dashboard_clock.py` |
| UC12 (live loop budget) | 16 | `test_live_loop_budget.py` |

**SolisCloud Watchdog (40 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC13 (Solis ON/OFF Watchdog) | 40 | `test_solis_switch.py` |

**Connectivity, exclusions and flags (124 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC14 (Connectivity + Exclusions) | 62 | `test_connectivity.py` |
| Feature flags | 34 | `test_feature_flags.py` |
| Fleet benchmark | 28 | `test_fleet_benchmark.py` |

**ShineMonitor (119 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC1 (Admin Alerts) | 15 | `test_admin_alerts.py` |
| UC2 (Customer Weekly) | 16 | `test_customer_weekly.py` |
| UC3-UC8 (Device Alarms) | 38 | `test_device_alarms.py` |
| UC9 (Email Optimization) | 9 | `test_uc9_admin_email.py` |
| UC11 (Plant ROI) | 19 | `test_uc11_plant_roi.py` |
| BVT (Centralized Config) | 14 | `test_centralized_config.py` |
| API Tests | 8 | PASSED in CI |

**DessMonitor (112 tests):**

| Suite | Tests | Status |
| ----- | ----- | ------ |
| UC1 (Admin Alerts) | 19 | PASSED |
| UC2 (Data Collection) | 15 | PASSED |
| UC3-UC8 (Device Alarms) | 19 | PASSED |
| UC9 (Email Optimization) | 9 | PASSED |
| UC11 (Plant ROI) | 19 | Stub |
| BVT (Centralized Config) | 13 | PASSED |
| API Tests | 18 | PASSED in CI |

Run the lot with `py -m pytest integration/ -q`. The count above is what that
prints; if a change moves it, update this table in the same commit.

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
py -m pytest integration/test_connectivity.py -v       # UC14: Connectivity + exclusions
py -m pytest integration/test_feature_flags.py -v      # Feature flags

# Run specific test suite - DessMonitor
py -m pytest integration/test_dessmonitor_*.py -v     # All DessMonitor tests (112)
```

See [src/test/java/org/ktronics/scripts/README.md](src/test/java/org/ktronics/scripts/README.md) for detailed test documentation.

## Answering a Production Dispute

When a customer says "my system is underproducing" and you suspect weather, do
not claim it was cloudy - you cannot prove that, and if you are wrong it costs
you the argument and your credibility. Use the fleet instead.

`fleet_benchmark.py` treats every plant in `data/` as a weather station. They all
sit under the same Sri Lankan sky, so their collective daily output is an
irradiance proxy drawn from real meters - the customer's neighbours' own
systems, not a forecast.

```bash
# a plant already tracked in data/
python src/main/java/org/ktronics/scripts/fleet_benchmark.py     --start 2026-08-15 --end 2026-09-07 --target gayan-imh-imbulgoda-3kw

# a plant on another platform (SolisCloud, etc.) via the portal's CSV export
python src/main/java/org/ktronics/scripts/fleet_benchmark.py     --target-csv surath.csv --label "Surath 5KV" --html reports/surath.html
```

### Reading the output

| Signal | Meaning |
|---|---|
| **Fleet index** | 100% = the fleet's best day in the window. ~85-90% is a good day island-wide, ~60% a genuinely poor one |
| **Weather correlation (r)** | The number that settles it. Above ~0.7 the plant tracks the sky, so its variation is weather. Near zero it produces the same output regardless of conditions - that is a clamp, a limit or a fault, and it cannot be weather |
| **Shortfall** | Expected minus actual, weather-adjusted. Positive means lost production |

A high fleet index next to a large shortfall means the sky was fine and the loss
belongs to the plant.

### Two things to know before quoting a number

**A plant clamped for the whole window understates its own loss.** The
expectation is calibrated from the plant's own best days, so if it was throttled
throughout, it never demonstrated its real ceiling and the shortfall shown is a
*floor*. The low correlation warning flags this. Pass `--kwp` to benchmark
against the array's rated capability instead.

**Today is always excluded.** Collection runs mid-day, so the current date is a
half-filled row that reads as a cloudy day that never happened. Use
`--include-partial` only if you know the day is complete.

`--html` writes a standalone, offline, theme-aware page suitable for sending to a
customer.

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
