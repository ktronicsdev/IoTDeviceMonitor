# Multi-Cloud IoT Device Monitoring System

This platform watches the solar plants of KTronics customers on ShineMonitor, DessMonitor and SolisCloud, checks their production against what each plant should make, and sends alerts, device alarms and weekly reports by email. It is built and run by [KTronics (Pvt) Ltd](https://ktronics.lk/), a solar and electrical engineering company in Kelaniya, Sri Lanka. The monitoring service as offered to customers is described at <https://ktronics.lk/iot-solutions/>.

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
| `push_data_commit.sh` | The only way anything publishes to `main`: fetch, rebase, retry |
| `merge_alarm_state.py` | Merges a conflicting cumulative `state/*.json` instead of picking a side |
| `fleet_benchmark.py` | Weather-adjusted benchmark for production disputes |
| `check_solis_switch.py`, `solis_common.py` | UC13 SolisCloud watchdog |
| `check_ph1000*.py` | UC12 PH1000 dashboard and its PACEEX BMS decode |
| `check_ph1800.py` | UC15 generic pass-through dashboard, any inverter |
| `bms-module/scripts/ingest_bms_reading.py` | UC16 ESP32 reading: CSV, thresholds, alert |

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

**No cron fires on :00 or :30 (2026-10-22).** GitHub's own docs name the start
of the hour as its peak load window — "Scheduled events can be delayed during
periods of high loads... High load times include the start of every hour. If
the load is sufficiently high enough, some queued jobs may be dropped" — and
ten of our twenty-two crons were sitting exactly there, including **both**
hourly polls on the same minute. Delivery was running at roughly 40% (#31).
Every cron has been moved off :00 and :30; the two weekly reports, which used
to collide at 04:00 precisely, are now ten minutes apart.

This is an experiment with a measurable answer: if delivery does not improve
over a day or two, the cause is not load placement and #31 needs a different
remedy — a self-hosted runner, an external scheduler calling
`workflow_dispatch`, or a self-chaining loop like PH1800 already uses. Moving
to another repository would not help: the scheduler is shared, and this
repository is already public, so there is no minutes quota to escape.

Changing an hourly cron means changing every `github.event.schedule !=` guard
with it. `test_hourly_poll_scope.py` now derives each workflow's hourly cron
rather than hardcoding one, so a half-done change fails a test instead of
silently disabling the guards.

Display names follow one scheme — `<platform> — <what it does> (<cadence>)` —
because the old ones had drifted from the work. "ShineMonitor Daily Monitor"
ran hourly plus six times a day and also ran the test suite; "Weekly Customer
Reports" was the only one without a platform in its name and was ShineMonitor
only; the "Live Dashboard" pair do not serve a dashboard, they publish data
that GitHub Pages serves.

| Workflow | Display name | Runs | Does |
|---|---|---|---|
| `trigger-shinemonitor.yml` | ShineMonitor — collect, alert, test | Hourly at **:13**, **plus** 6x daily at **:37** past 02/06/10/14/18/22 UTC | Hourly: device alarms only. 4-hourly: collect, connectivity, anomalies, admin email |
| `trigger-dessmonitor.yml` | DessMonitor — collect, alert | Hourly at **:41**, **plus** 6x daily at **:51** past 03/07/11/15/19/23 UTC | The same for DessMonitor (UC10) |
| `trigger-customer-reports.yml` | ShineMonitor — weekly customer reports | Sunday, **04:19** UTC | UC2 weekly reports and the admin summary |
| `trigger-dessmonitor-weekly-reports.yml` | DessMonitor — weekly customer reports | Sunday, **04:29** UTC | UC2 for DessMonitor |
| `trigger-ph1000.yml` | PH1000 — publish live data | Every 2 h, restarting a self-chaining loop | UC12 live data to the `ph1000-live` branch |
| `trigger-ph1800.yml` | PH1800 — publish live data, any inverter | Every 2 h, restarting a self-chaining loop | UC15 live data to the `ph1800-live` branch |
| `trigger-ph1000-hist.yml` | PH1000 — publish 30-day history | Hourly | UC12 30-day window on `ph1000-hist`, decoupled from the live cycle |
| `trigger-ph1800-hist.yml` | PH1800 — publish 30-day history | Hourly | UC15 30-day window on `ph1800-hist`, decoupled from the live cycle |
| `trigger-solis-switch.yml` | Solis — re-enable a switched-off inverter | Every 2 h at **:15** | UC13 watchdog. The Control API key went live on 16 Oct 2026, so it can now re-enable, not just report |
| `trigger-bms-ingest.yml` | ESP32 BMS — ingest a pushed reading | On a device push (`repository_dispatch`), ~every 15 min | UC16 ESP32 BMS reading |
| `trigger-battery-health.yml` | Solis — battery health report | 1st of the month, **06:23** UTC | **UC17** battery state of health and yearly fade rate per battery; read-only, no state kept |
| `tests.yml` | Tests — pull requests only (no email) | On every pull request to `main` | The full suite, silent by design. **Not** the push test: a push to `main` is tested by the `tests` job inside `trigger-shinemonitor.yml`, which is what sends every "TESTS FAILED" email |

`pages-build-deployment` also appears in the Actions list. It is **GitHub's
own** workflow, not a file in this repository, and it republishes GitHub Pages
whenever `main` moves — which is what serves `site/ph1000/` and
`site/ph1800/<label>/`. It cannot be renamed or edited.

### Which workflow serves which use case

Derived from the scripts each workflow actually runs, not from its name.

| Workflow | Use cases |
|---|---|
| `trigger-shinemonitor.yml` | **UC1** production alerts, **UC3–UC8** device alarms, **UC9** admin email cadence, **UC14** connectivity |
| `trigger-dessmonitor.yml` | **UC10**, which re-implements UC1, UC3 and UC14 for DessMonitor |
| `trigger-customer-reports.yml` | **UC2** weekly reports, **UC8** schedule filter |
| `trigger-dessmonitor-weekly-reports.yml` | **UC2 + UC10** |
| `trigger-ph1000.yml` | **UC12** live data |
| `trigger-ph1000-hist.yml` | **UC12** 30-day history |
| `trigger-ph1800.yml` | **UC15** live data |
| `trigger-ph1800-hist.yml` | **UC15** 30-day history |
| `trigger-solis-switch.yml` | **UC13** |
| `trigger-bms-ingest.yml` | **UC16** |
| `trigger-battery-health.yml` | **UC17** |
| `tests.yml` | cross-cutting; owns no use case |

Four things this makes visible that the lists above do not:

- **UC11 (Plant ROI) has no workflow.** `check_plant_roi.py` has a UC section
  and 19 tests, and nothing schedules it. It is manual-only, and nothing says
  whether that is deliberate.
- **UC4–UC8 have no workflow of their own.** They are behaviours inside UC3's
  scripts, which is why their tests live in `test_device_alarms.py` rather
  than in files of their own.
- **UC12 and UC15 each span two workflows** — live and history, on separate
  branches and separate schedules. That split is the point, not an accident:
  fetching the 30-day window on the live cycle is what made the dashboards go
  stale in August 2026.
- **`trigger-shinemonitor.yml` carries eight use cases end to end.** That is
  why it runs for 80–90 minutes and why a push waits behind it. If the Actions
  feedback loop needs to get shorter, splitting this workflow buys far more
  than moving crons does.

**Not part of the pipeline**, though they live beside it:
`refresh_bms_creds.py` re-bootstraps the BMS credentials from the running
PACEEX app and needs BlueStacks plus frida on a workstation; `solis_mcp.py` is
a stdio MCP server that exposes SolisCloud to Claude; `ccv_heatpump_mcp.py` is
a **read-only** stdio MCP server for the Valla house's ground-source heat pump
(JEFF CCV Pro controller) — it reads temperatures, relays, alarms and counters
over Modbus TCP from the heat pump's touch display on the home LAN, finds the
display itself when DHCP moves it (cached address, then the MAC in the ARP
table, then a port-502 search of the /24 with a clock/version fingerprint), and
can send no write function code; and
`check_shinemonitor_query-plants.sh` is referenced by no workflow and no test —
a manual query helper, which is worth knowing before anyone relies on it.

Customer-facing email goes out **only on scheduled runs**; admin email goes out
on every trigger, including a developer's push (UC8). The test customer
Gayan-IMH is the exception and receives on all triggers (UC4), which is how a
change is proven end to end without mailing the customer base.

The hourly run exists so the six-hour gate's dwell is literal rather than
six-to-ten. It polls alarms and **nothing else**: it does not fetch CSVs, so it
must not analyse them either. `check_anomaly.py` and `check_connectivity.py`
carry the same hourly-skip guard as the fetch steps, and
`test_hourly_poll_scope.py` keeps it that way — running them on stale data gave
two alternating fleet views that overwrote each other's state and mailed the
admin several times a day.

"ALL SYSTEMS OPERATIONAL" goes out **once per ISO week**, on the first
scheduled run of that week. No cron is named, and a push or manual run with
nothing wrong says nothing at all. Alerts still send whenever the content
changes, on any trigger; a clean bill of health is a weekly thing.

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

**Alert discipline:** the same as device alarms and UC14 — **3 sends per alert,
at least 4 hours apart**, then quiet. The 3-**day** auto-ignore
(`auto_ignore_days`) applies independently; whichever limit is reached first
stops the mail. Until 2026-10-16 `send_count` was incremented every run and
never read, so the day rule was the only brake there was: at hourly polling
that is ~90 emails for one fault.

The filter that applies this runs **before** `alerts.json` and `alerts.txt` are
written, so the report, the UC9 hash, the email subject and the exit code all
describe the same set of alerts. It used to run after them, which meant the
report listed alerts the exit code had already filtered out.

**Email Recipient:** <ktronicssolar@gmail.com>

**Test Coverage:** 19/19 PASSED — `test_admin_alerts.py` (15),
`test_admin_alert_send_cap.py` (4)

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
- Six-hour gate: an alarm is mailed only if it is still open 6 hours after it started; comms/offline alarms are never mailed ([design](six-hour-gate/README.md))
- 3-send rule: once an alarm passes the gate it is sent 3 times, at least 4 hours apart, then auto-ignored
- State persistence with human-readable fields (customer, plant, message)

**Test Coverage:** 40/40 PASSED — `test_device_alarms.py`, which also
carries the UC4, UC5, UC6, UC7 and UC8 tests

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

Reduces admin email noise by sending the system-wide status email only when the alert state changes.

**Scope:** the ShineMonitor production status email (UC1). Device alarm emails are not covered; they are limited by the six-hour gate and the 3-send rule (UC3). The DessMonitor workflow does not have this check.

**Implementation:**

Hash-based state detection in the `Send system-wide status email to admin` step of the GitHub Actions workflow:

- **Calculate a SHA256 hash of the alert content** in `alerts/alerts.json`: the `alerts`, `suppressed` and `ignored` arrays only. The `generated_at` timestamp is left out, because it changes on every run
- **Compare with previous hash** from `state/admin_email_state.txt`
- **Send email when:**
  - Alert content changed (hash mismatch)
  - Alerts appeared (empty → hash)
  - Alerts cleared (hash → different hash)
  - Push/manual trigger (build verification)
- **Skip email when:**
  - Scheduled run AND hash unchanged (same state)

The step also sits behind the `emails.production_alerts_admin` feature flag.

**The weekly all-clear (2026-10-16):** an "ALL SYSTEMS OPERATIONAL" email used
to arrive daily, and some days more than once, although the fleet was quiet.
Two causes, both now fixed:

- The hourly run analysed **stale** CSVs and so produced a different
  `alerts.json` from the 4-hourly run. The hash alternated between two fleet
  views and every alternation read as "the state changed". The hourly run no
  longer analyses CSVs at all (see Bug Fixes → *The October 2026 mail
  flood*).
- Nothing said how often an all-clear was wanted.

**The weekly all-clear, second attempt (2026-10-20).** The first version asked
for `github.event.schedule == '30 6 * * *'` on a Monday. It never fired. Two
reasons, and both are worth keeping in mind for anything else scheduled here:

- **GitHub delivers about 40% of the scheduled runs these crons ask for** —
  11 to 13 a day against ~30, every day from 14 to 19 October. The Monday
  06:30 run simply did not exist on 2026-10-19, so every Monday run logged
  "Skipping all-clear" and nothing was sent. Pinning a weekly duty to one
  nameable run makes that run's delivery a single point of failure.
- **The gate could only subtract.** It only ever set `send_email=false`, and
  the hash check above it already says false whenever the fleet is unchanged.
  So a fleet that merely stayed healthy produced *no* status email at all, for
  as long as it stayed healthy. The "weekly" all-clear was never weekly; it
  only rode along on the run where alerts happened to clear.

Together those made silence meaningless: a healthy fleet and a stopped
pipeline looked identical from the inbox, which is worse than the daily noise
the gate replaced — the noise at least proved the pipeline was alive.

**The weekly all-clear, third attempt (2026-10-22).** The second version had
an exception: if the hash moved and nothing was RED, treat it as "alerts
cleared" and send at once. That exception swallowed the rule. The hash covers
`alerts + suppressed + ignored`, and with `alerts=0, suppressed=1, ignored=6`
every plant moving in or out of the ignored list moves it — which is what the
DATA LINK DOWN traffic is. So nearly every scheduled run took the exception:
five "ALL SYSTEMS OPERATIONAL" emails on 21–22 October, inside one ISO week
whose marker was already written and never consulted.

There is no honest signal to rebuild that exception on, either:
`current_hash = "empty"` means the `alerts.json` **file is missing**, not that
the fleet is clear, so the `ALERTS CLEARED` label has never meant what it says.

The rule is now **one all-clear per ISO week, sent by the first scheduled run
of that week**, with no exceptions. A push or manual run reports only when
something is wrong: build verification used to send unconditionally, which put
an empty status email in the inbox on every developer push — noise in UC1,
whose content reports no event, and in UC9, whose cadence it ignored. Alerts
(`exit_code == 2`) never enter the gate on any trigger.
The week is recorded only *after* the mail goes out, so one SMTP failure
cannot silently consume the week.

**State Files:** `state/admin_email_state.txt` (the UC9 hash),
`state/admin_all_clear_state.txt` (the ISO week last confirmed)

**Email Reduction:** the workflow has 30 scheduled runs a day (hourly, plus six
data-collection runs), and each would otherwise send a status email. With the
hash check and the Monday gate, a quiet fleet produces **one** admin status
email a week; a fleet with alerts produces one per change of content, and UC1's
send cap holds that to three per alert.

**Implementation File:** [trigger-shinemonitor.yml:390-492](/.github/workflows/trigger-shinemonitor.yml#L390-L492)

**How It Works:**

1. Generate `alerts/alerts.json` with current alerts
2. Calculate the SHA256 hash of the alert content (`jq -cS '{alerts, suppressed, ignored}'`), not of the whole file
3. Load previous hash from state file
4. Compare hashes to detect changes
5. Send email only if changed or push/manual trigger
6. Save current hash for next run

Step 5 also applies the weekly all-clear gate described above.

**Implementation Status:** ✅ **IMPLEMENTED** (Session 13); all-clear gate
added 2026-10-16

**Test Coverage:** 24/24 PASSED — `test_uc9_admin_email.py` (9),
`test_all_clear_cadence.py` (15)

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

**Test Coverage:** 19/19 PASSED — `test_uc11_plant_roi.py`. The DessMonitor
side has its own 19 in `test_dessmonitor_uc11_plant_roi.py`, listed under UC10.

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

**Test Coverage:** 53/53 PASSED — `test_ph1000.py` (39),
`test_bms_cell_frame.py` (9), `test_bms_carry_forward.py` (5).

Not UC12, despite the name: `test_bms_ingest.py` (16) covers the **ESP32
JK-BMS** module — a separate DIY `repository_dispatch` path, not the
PH1000/PACEEX BMS. That is UC16. `test_dashboard_clock.py` and `test_live_loop_budget.py`
are shared with PH1800. Both groups are listed under *Live inverters + BMS*
in the test tables.

---

### UC13: SolisCloud Inverter ON/OFF Watchdog

Watchdog for **SolisCloud** inverters that detects when an inverter has been switched **OFF** and (when the Control API is configured) automatically switches it back **ON**, emailing the admin.

**Implementation Status:** ✅ **IMPLEMENTED** (Session 17). Detection has been live since then, and the SolisCloud key gained **Control** permission on **16 Oct 2026** — so auto switch-on is armed now, not pending.

**Motivation:** The **Ktronics Imbulgoda** plant latched **OFF for 4 days** after repeated `Uac-Unstable` (code 1019) grid faults, recovering only when manually powered on in the SolisCloud app. This watchdog catches that within ~2 hours instead of days.

**How it works:**

1. Runs every 2 hours ([trigger-solis-switch.yml](.github/workflows/trigger-solis-switch.yml)).
2. Reads each inverter's live state via the HMAC-SHA1-signed SolisCloud API.
3. Decides if it's OFF — two modes:
   - **Authoritative:** if `onoff_cid` is configured, reads the on/off control register (`/v2/api/atRead`).
   - **Heuristic (default, no Control permission needed):** during the core daylight window (09:00–15:00 local), fresh telemetry showing `pac == 0` ⇒ not producing. Skips at night / on stale data to avoid false triggers.
4. If OFF: auto-enable via `/v2/api/control` and email *"auto re-enabled"*; otherwise email *"switch on manually"* (throttled to once / 12 h).

**Platform note:** SolisCloud uses an **API Key ID + Secret** (SolisCloud → Service → API Management), **not** the web login. Remote on/off needs the **Control API** permission, which Solis grants on request through a support ticket — being their distributor does not let you switch it on yourself.

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

**Test Coverage:** 44/44 PASSED — `test_solis_switch.py`

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

Only a plant **positively seen reporting** is cleared. Until 2026-10-16 any plant merely absent from the run's fleet was cleared too, so when the hourly run looked at a smaller fleet than the 4-hourly one it wiped the send counts of everything it could not see, and the next run alerted those plants as new — three sends per plant, indefinitely.

**A true link-down is only as good as the data under it (2026-10-22).** The
3-send rule held for the eight genuinely dead plants, yet three healthy ones —
`crdesilva-home`, `kosala-koshala-plant`, `thusharasameera-mendis-house` —
were mailed LINK DOWN repeatedly while each had a reading dated that same day.

The fault was upstream, in the fetch (#33). A fetch truncated a plant's CSV
before it knew the API had answered, so an empty response left a bare header,
which `connectivity.py` reads as `no_rows` — "the portal has nothing for this
plant". The plant then flips: dead on the bad run, reporting on the next, so
`clear_recovered()` wipes its state each time it reappears and the counter
restarts at send #1 for ever. 11 dead links at 08:59, 8 at 09:51, same morning.

Two things follow, and both are worth remembering before trusting a count
here. A link-down verdict is a statement about **our data**, not about the
plant — a gap in the CSV and a dead dongle look identical from here. And a
3-send rule cannot hold against an input that keeps resetting it; the rule was
never broken, the data under it was.

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

**Test Coverage:** 65/65 PASSED — `test_connectivity.py` (62),
`test_connectivity_state_retention.py` (3)

---

### UC15: PH1800 Live Dashboard (generic, all inverters)

**Implementation Status:** ✅ **IMPLEMENTED**

A live per-plant dashboard for any ShineMonitor inverter, as opposed to UC12's
PH1000, which is a bespoke workaround for one plant's broken `devcode-697`
feed. [`check_ph1800.py`](src/main/java/org/ktronics/scripts/check_ph1800.py)
is a **pure pass-through**: for every account flagged `"ph1800": true` it walks
the account's plants, auto-detects each device's devcode, and emits exactly
what ShineMonitor returned — live fields, raw history, plant profile. No
estimates, no energy integration, no register decode, no BMS, **no computed
fields at all**. That is the point: when a number on the dashboard looks wrong,
it is the vendor's number, so there is no local arithmetic to suspect first.

**Privacy, because the repository is public.** One JSON per account, named
`sha256(username:password).json`, so a plant's data file cannot be enumerated
by someone who does not already have its credentials. The page carries the
username; the viewer types the password; the browser derives the same filename.
Four accounts are flagged today.

**Pages:** `site/ph1800/<label>/index.html`, each a byte-for-byte copy of
`site/ph1800/_app.html`. A page that differs from the template is re-stamped,
so a template change propagates to every plant on the next run. `_app.html`
is the hand-authored source and the only file here a human edits — which is
why a merge conflict under `site/` is deliberately **not** auto-resolved (see
#29 in Bug Fixes).

**Two workflows, decoupled on purpose:**

| Workflow | Cron | Publishes to | Window |
| -------- | ---- | ------------ | ------ |
| [`trigger-ph1800.yml`](.github/workflows/trigger-ph1800.yml) | `23 */2 * * *` | `ph1800-live` (force-push) | 7 days |
| [`trigger-ph1800-hist.yml`](.github/workflows/trigger-ph1800-hist.yml) | `17 * * * *` | `ph1800-hist` (force-push) | 30 days |

The live run is a long one: a `while :` loop publishing about every 10 minutes
against a 300-minute budget inside a 330-minute job timeout, with the next run
pre-queued as a hot standby and `cancel-in-progress: false` so a healthy loop
is never killed. The budget is wall-clock, not an iteration count, because a
fifth plant once pushed a fixed 26-iteration loop past the timeout and the tail
iterations never published.

The 30-day history is fetched on its **own** hourly schedule and never on the
live cycle — fetching it there is what made the dashboards go stale in August
2026. `test_live_loop_budget.py` guards both halves of that.

Neither branch is a branch off `main`: each is a single orphan commit holding
only the current payload, which is why they read as "1 ahead, thousands
behind".

**Test Coverage:** 49/49 PASSED — `test_ph1800.py` (41),
`test_ph1800_battery_tab.py` (8). `test_dashboard_clock.py` (16) and
`test_live_loop_budget.py` (16) cover this and UC12 jointly and are counted
under *Live inverters + BMS* in the test tables.

---

### UC16: ESP32 BMS Module (DIY battery monitoring)

**Implementation Status:** 🧪 **PROOF OF CONCEPT** — one trial site

A self-built alternative to a commercial battery monitor: an **ESP32** running
ESPHome reads a **JK-compatible BMS over BLE** and pushes readings to this
repository. There is no server to host, because it reuses the pattern the rest
of the platform already runs on — GitHub Actions, a CSV history, an email
alert.

```
  JK-BMS --BLE--> ESP32 (ESPHome) --WiFi/HTTPS--> GitHub API
                       |                               |
                       | live, every 10s               | repository_dispatch, every ~15 min
                       v                               v
               LAN dashboard at             trigger-bms-ingest.yml
               http://<device-ip>           -> append bms-module/data/<device>.csv
                                            -> thresholds -> email admin
```

The split matters: the **10-second** data stays on the LAN and never leaves the
site, and only a **15-minute** sample goes to the cloud for history and alerts.
That keeps roughly 96 workflow runs per device per day rather than one every
ten seconds.

**Trigger:** `repository_dispatch` of type `bms_reading` — **device-driven, not
cron**. The reading arrives as `client_payload` and is read from the
`BMS_PAYLOAD` environment variable, so nothing is polled and nothing is
scheduled.

**[`ingest_bms_reading.py`](bms-module/scripts/ingest_bms_reading.py)** appends
to a per-device CSV, keeps a readable snapshot in
`bms-module/state/bms_latest.json`, and emails the admin when a threshold is
**newly** crossed, with a 4-hour re-alert cooldown so a 15-minute push cadence
cannot spam. It reuses `email_utils.send_email_smtp` from the ShineMonitor
scripts rather than carrying its own SMTP stack.

**Thresholds** (each overridable by environment variable):

| Condition | Default | Variable |
| --------- | ------- | -------- |
| Low state of charge | 15 % | `BMS_SOC_MIN` |
| Pack undervoltage | 48 V | `BMS_VOLT_MIN` |
| Pack overvoltage | 58.4 V | `BMS_VOLT_MAX` |
| Over-temperature | 50 °C | `BMS_TEMP_MAX` |
| Over-current (absolute) | 100 A | `BMS_CURRENT_MAX` |
| Cell imbalance | 0.1 V | `BMS_DELTA_CELL_MAX` |

The voltage pair assumes a 16S LFP pack.

**Append-only, and treated as such.** The commit step is the only caller of
`push_data_commit.sh --fail-on-conflict` rather than `--resolve-conflicts`: a
reading is an observation that happened, so dropping one to resolve a race is
never acceptable, and a conflict here must fail loudly instead. `.gitattributes`
also gives `bms-module/data/*.csv` `merge=union`, so two readings that arrive
together are both kept.

**Firmware is built locally, never in CI.** `firmware/jk-bms-monitor.yaml` plus
a `secrets.yaml` the installer creates from `secrets.example.yaml`. The
compiled `.bin` **embeds the GitHub token**, so
[`build_firmware.sh`](bms-module/scripts/build_firmware.sh) is a workstation
script — building it in a workflow on a public repository would publish the
token in an artifact.

**Test Coverage:** 16/16 PASSED — `test_bms_ingest.py`. Despite the name it is
not UC12: this is the ESP32 `repository_dispatch` path, not the PH1000/PACEEX
BMS that UC12 decodes.

---

### UC17: SolisCloud Battery Health Report

A monthly report on how fast each SolisCloud battery is wearing out, so a pack
that is fading abnormally is found while it is still under warranty rather than
when it stops holding charge.

**Implementation Status:** ✅ **IMPLEMENTED** (landed 17 Oct 2026) — run once
by hand that day and it sent its report. The **first scheduled run is 1 Nov
2026**, so the cron path itself is not yet proven in production.

**How it works:**

1. Runs on the **1st of each month at 06:23 UTC** ([trigger-battery-health.yml](.github/workflows/trigger-battery-health.yml)).
2. For every inverter on the account that reports a battery, reads **state of health (SOH)** and lifetime throughput over the same signed SolisCloud API that UC13 uses, pausing 1 s between calls to stay under the per-key rate limit.
3. Works out the yearly fade rate as `(100 - SOH) / years in service`. **No state file is needed:** SOH is already cumulative, so nothing has to be remembered between runs.
4. Flags a battery fading faster than **3 %/yr** (`--max-fade`) or already below **85 % SOH** (`--min-soh`). LFP packs (BYD, Pylontech) normally fade about 1-2 %/yr.
5. A battery under **3 months** in service is listed but **not judged** — over so short a window the computed rate is noise, not wear.
6. Emails the admin one table, warnings first.

**Read-only by design.** It sends no control command and keeps no state, so
unlike UC13 it cannot act on a plant by mistake.

**Scripts:**

| Script | Purpose |
|--------|---------|
| `solis_common.py` | SolisCloud signed-request client (`SolisClient`) — shared with UC13 |
| `check_battery_health.py` | Read SOH per battery → classify against the thresholds → email |

**Config:** the same `solis` block in `credentials.json` as UC13 — one key
serves both. Email channel: `emails.battery_health_admin`.

**State File:** none, by design.

**CLI:**

```bash
py check_battery_health.py                             # report + email the admin (cron entrypoint)
py check_battery_health.py --dry-run                   # print the report, send no email
py check_battery_health.py --max-fade 2 --min-soh 90   # stricter thresholds
```

**Test Coverage:** 10/10 PASSED — `test_battery_health.py`

---

## Bug Fixes

### The October 2026 mail flood — four bugs, one inbox

Between 11 and 16 October the admin inbox filled with repeats: "ALL SYSTEMS
OPERATIONAL" several times a day, one RED alert four times in nine hours, and
`sohan-must-pv1800-3024-pro` reporting a dead link every few hours at
"Send #1/3" without the counter ever advancing. It looked like one bug. It was four,
and the first diagnosis — a push race — was wrong.

**What gave it away:** every "wrong" commit was *byte-identical*. A race
produces varied content; two alternating identical values mean two
deterministic code paths.

**1. The hourly run analysed CSVs it never fetched.** #10 added an hourly cron
and guarded the fetch and cleanup steps to skip it — but not `check_anomaly.py`
or `check_connectivity.py`. So the hourly run computed one fleet view from
committed data and the 4-hourly run computed another from fresh data, each
overwriting the other's state. Any UC9 hash change reads as news, hence the
mail. Fixed by giving those two steps the same guard; pinned by
`test_hourly_poll_scope.py`, which also asserts the alarm poll still runs
hourly so nobody "fixes" it by guarding everything and reverting #10.

**2. A production alert had no send limit.** `send_count` was incremented every
run and never read; the only brake was the 3-**day** auto-ignore. At six runs a
day that was survivable, at thirty it is ~90 emails for one fault. Now three
sends, four hours apart, matching the device-alarm rule.

The substantive part of that fix was **moving the send filter above the output
writes**. It used to run after, so `alerts.json` and `alerts.txt` always listed
every alert while only the exit code reflected the filtering — a cap bolted on
there would have produced an "ALL SYSTEMS OPERATIONAL" subject over a body
still reporting RED. The JSON, the text, the hash, the subject and the exit
code now describe one set.

**3. Absence from a poll was treated as recovery.** `clear_recovered()` dropped
the state of every plant not in `fleet.link_down`, which also matches a plant
that was not classified at all that run — so its `send_count` reset to 1, never
reached 3, never auto-ignored. Now only a plant positively seen in
`fleet.reporting` is cleared. A fetch failure or a vendor timeout no longer
resets anyone's counter.

**4. Concurrent runs resolved state conflicts by discarding one side.** See
below.

**And one thing that was never a bug:** nothing had ever said how often a
clean bill of health was wanted. Fixing #1 stops the all-clear flapping, but
a correct hash on a quiet fleet still sends on the first run after the last
alert clears. So the all-clear is now gated to the **Monday 06:30 UTC** run,
once a week; anything with an actual alert in it is never gated. Pinned by
`test_all_clear_cadence.py`, which lifts the gate out of the workflow YAML
and runs it against a faked weekday rather than asserting on the text.

### A fetch that destroyed the data it was fetching (2026-10-22)

Every fetch script emptied a plant's CSV **before** it knew the API had
returned anything:

```bash
out="data/<plant>-<YYYY-MM>.csv"
: > "$out"                      # truncate first, unconditionally
echo "date,kwh" >> "$out"
... parse the response ...
if no rows: echo "(No daily rows returned)"   # the file stays empty
```

The empty file was then committed. Read off `main` for `crdesilva-home`:
**31 rows → 0 → 31 → 0 → 0 → 31** across 20–22 October.

One line, three symptoms that had looked unrelated — which is why the mail
took three attempts to stop:

- **False DATA LINK DOWN.** `connectivity.py` reads a header-only file as
  `no_rows`, "the portal has nothing for this plant", so a plant that reported
  that morning is mailed as a dead link.
- **The 3-send rule never bit.** Those plants flip between dead and reporting
  run to run, `clear_recovered()` correctly wipes their state each time they
  reappear, and the counter restarts at #1 — for ever.
- **The all-clear flood.** Plants entering and leaving the dead-link set move
  the UC9 content hash, which is what kept defeating the weekly gate.

The yearly scripts had the same fault in a different costume: a failed call
writes `kwh=0` "to keep shape", so a year of real production becomes twelve
zeros.

All four fetches now stage to a temporary file and `mv` it into place **only**
when rows were parsed; otherwise they keep what is committed and say
`WARNING: ... keeping existing`. The workflow's `grep` allow-list was widened
to let `WARNING` and `AUTH FAIL` through — it had been stripping both, which
is how this ran unseen.

`test_fetch_never_destroys_data.py` pins it, including a test that runs the
**old** idiom to prove it really does destroy the file. If that ever stops
being true the premise has changed and the guard can be revisited; until then
it is what makes the other test meaningful.

### The vendor's third mood (2026-10-22)

The 21 October fix made a refused `authEmail` and a timeout skip instead of
fail. It was not enough: the scheduled runs at 18:47 and 22:04 that evening
still went red with one failure each.

```
test_check_monthly_script     SKIPPED   <- the fix caught this
test_query_plants_api         FAILED    <- it did not
    json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

ShineMonitor has a third mood: it exits **0** and hands back an empty or
non-JSON body. The previous fix only looked for a non-zero exit with
`"empty response"` on stderr, so this sailed past it and the tests parsed the
empty string themselves. Two shapes:

- `json.loads(result.stdout.strip())` on an empty body — `JSONDecodeError`.
- the pid pipeline, `queryPlants | grep -o '"pid":[0-9]*'`, under
  `set -o pipefail`: an empty body means grep matches nothing, exits 1, and
  takes the pipeline down with **nothing on stderr** to explain it.

Both now route through `parse_api_response()` and `plant_id_or_skip()`, which
skip. What keeps that safe is the opposite case: stderr carrying `command not
found`, `No such file`, `unbound variable`, `syntax error`, `Permission
denied` or `cannot open` means **our** script is broken, and those still fail.
Skipping them would hide a real regression behind a green build.

`TestVendorConditionHandling` tests that decision directly — no bash, no
network, no credentials, so it runs on every machine instead of only when the
vendor misbehaves. It deliberately does not use `pytest.raises`: `Skipped` and
`Failed` both derive from `BaseException`, so a test expecting a `Failed` that
receives a `Skipped` does not go red — the skip propagates and the test
quietly joins the skip count. The outcome is captured as a string and asserted
on instead. That exact flaw was caught by mutation testing, not by reading.

Two further tests check the **call sites** rather than the helper, because a
single reverted site reintroduces the whole failure while the helper stays
correct and still passes its own tests.

### A collection error that hid the whole suite (2026-10-21)

`test_tuya_mcp.py` imported `tuya_mcp`, which imports `requests`. CI installs
`pytest pyyaml` and nothing else, because tuya_mcp is a workstation tool that
talks to the author's own home devices and nothing in the pipeline runs it.
pytest answered:

```
collected 661 items / 1 error
!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!
```

Not one failing test — **no** tests. Every scheduled ShineMonitor run went red
with nothing run, so the red meant only "something is broken somewhere", which
is the least useful thing a suite can say. Data collection was unaffected: the
`tests` and `run` jobs have no `needs:` between them, which is why that
separation exists.

Two defences, because a suite that one typo can silence deserves both:

- The import is guarded with `pytest.importorskip(..., exc_type=ImportError)`,
  so the module skips as a unit on a machine without it.
- Both suite runners (`trigger-shinemonitor.yml`, `tests.yml`) now pass
  `--continue-on-collection-errors`. A bad file then costs that file; the rest
  still report, and the run still fails as it should.

`test_suite_visibility.py` pins both: a test module may not import a package
CI does not install unless it guards it, and a workflow may not run the suite
without that flag. Each was mutation-checked by reintroducing the exact bug.

### The weekly all-clear that never arrived (2026-10-20)

The 2026-10-16 gate asked for `github.event.schedule == '30 6 * * *'` on a
Monday. On Monday 2026-10-19 that run did not happen, so no all-clear was sent
and the admin had no weekly confirmation at all.

**GitHub delivers ~40% of the scheduled runs this repository asks for** — 11 to
13 a day against ~30, measured across 14-19 October. Any design that depends on
one *named* scheduled run existing is therefore unreliable, and that applies
beyond this gate: the hourly alarm poll #10 added, which is what makes the
six-hour gate's "six hours" literal, is mostly not running either.

The gate had a second fault that the first made invisible: it could only set
`send_email=false`, and the hash check above it already says false for an
unchanged fleet. A fleet that merely stayed healthy therefore produced no
status email for as long as it stayed healthy. The "weekly" all-clear only ever
rode along on the run where alerts happened to clear.

Between them, silence carried no information — a healthy fleet and a stopped
pipeline looked the same from the inbox, which is worse than the daily noise
the gate was added to remove.

Now: one all-clear per **ISO week**, sent by the first eligible scheduled run of
that week, no cron named; the gate both forces and suppresses; the week is
recorded only after the mail is actually sent, so a failed send cannot consume
it. `test_all_clear_cadence.py` runs the real step out of the YAML with `date`
and `python3` stubbed, and each of the three failure modes above fails a test
named after it.

### #4: customer data in world-readable workflow logs

The repository is public, so every workflow log is world-readable. Four places
wrote customer data into one. Three only on a failure; the fourth ran on every
push.

- Both alarm fetch scripts echoed the **whole response body** when a fetch
  failed. An error response still carries plant names, device serials and
  sometimes a session token. They now print the error code and the response
  size.
- `generate_device_alarms.py` printed the plant name and the alarm text in
  **test mode**, which runs on every push and manual run — so this was
  continuous, not fault-triggered. It now prints the alarm key, which indexes
  a state file that is not public.
- `trigger-dessmonitor.yml` printed the first 100 characters of the
  credentials file when it would not parse. GitHub masks a whole secret, but a
  cut-off fragment need not match the mask, so masking does not help here. It
  now prints the file size and jq's position only — and the position is matched
  against a whitelisted shape first, because some jq builds append the
  offending token to a parse error, which on a credentials file is the
  credential.

Already fixed earlier and confirmed gone: the account username both alarm
scripts used to echo.

Still printed, deliberately, pending a decision: both workflows log
`Checking alarms for: $label`, i.e. the customer name, once per account per
run. It is the main thing that makes a failing run diagnosable; the
alternative is a position (`account 7/23`) and worse logs.

`test_no_payloads_in_public_logs.py` is the guard. Each of the four has its own
named test, and one test asserts the failure path still reports the error code
so nobody satisfies the others by printing nothing.

### #29: four workflows still pushed to main bare

#28 gave the two data workflows a safe publish. Four others kept a plain
`git push`: `trigger-customer-reports`, `trigger-dessmonitor-weekly-reports`,
`trigger-solis-switch` and the PH1800 pages step. A bare push does not fetch,
so when it loses a race the push is rejected non-fast-forward; it is the last
command in its step, so the step fails and the commit dies with the runner,
with nothing to retry it. Three of the four carried a `state/*.json`, so the
lost commit meant send counters never reached `main` and the next run mailed
the same fault as new — the flood above, by a different route.

The two weekly workflows both fire at `0 04 * * 0`, the same minute every
Sunday, so one of them lost by construction. Per-workflow `concurrency` groups
do not help: a group only serialises a workflow against itself.

All four now call `push_data_commit.sh --resolve-conflicts`.

Two things that looked like part of the fix and were not:

- **The conflict allowlist did not need widening.** `resolve_regenerated_conflicts()`
  deliberately refuses anything outside `state/`, `data/` and `alerts/`. The
  archive tarballs these workflows appear to commit are matched by `*.tar.gz`
  in `.gitignore`, so `git add reports/archive/*` has always staged nothing —
  the only thing they really commit is the state file, which the merger already
  handles. The PH1800 pages are generated copies of `_app.html`, written by one
  workflow that is serialised against itself; `_app.html` itself is
  hand-authored, so allowlisting `site/` would have let a bot revert a human's
  template. A conflict there should still fail loudly.
- **The PH1800 step no longer hardcodes `main`.** It pushed `HEAD:main` from
  whatever ref the run started on, so a "Run workflow" from a topic branch
  would rebase that branch onto main and push it there. It now publishes to the
  ref it ran from, which is what `test_a_manual_run_from_a_branch_never_writes_to_main`
  already required of every other publisher.

`test_no_bare_push_to_main.py` is the guard. It allows exactly one exemption:
the force pushes to `ph1000-live`, `ph1000-hist`, `ph1800-live` and
`ph1800-hist`, which have a single writer each and are decoupled from `main`
on purpose.

### #18/#28: whole-file resolution of a cumulative map

The data-commit step resolved a conflicting `state/*.json` with
`git checkout --ours`. A state file is one key → record map and two runs each
add and update different keys, so whole-file resolution throws away everything
the losing side recorded — not just the conflicting part.

Reproduced in a sandbox: main at `{A:2, B:1}`, our run at `{A:1, B:2, C:1}`;
after resolution main was `{A:2, B:1}`, the script printed "Published data
commit to main." and exited 0. B's second send and alarm C were gone, so the
next run mails them as new, for ever.

`merge_alarm_state.py` merges instead: union of keys, higher `send_count` per
key, `ignored` latches true, `first_seen` earliest and `last_sent` latest.
Fixed alongside it: the unchecked `git checkout` return (which silently staged
the opposite side), an unbounded `else` branch that would have resolved a
`src/` conflict in the bot's favour, and a failed `git stash pop` turning an
already-successful push into the false "production CSVs are NOT being updated"
email.

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
│   ├── trigger-shinemonitor.yml       # ShineMonitor monitoring (hourly + 6x daily) - UC1, UC3-UC9, UC14
│   ├── trigger-dessmonitor.yml        # DessMonitor monitoring (hourly + 6x daily) - UC10
│   ├── trigger-customer-reports.yml   # ShineMonitor weekly customer reports (Sunday) - UC2, UC8
│   ├── trigger-dessmonitor-weekly-reports.yml  # DessMonitor weekly customer reports (Sunday) - UC2 + UC10
│   ├── trigger-ph1000.yml             # PH1000 live dashboard + BMS (~10 min loop) - UC12
│   ├── trigger-ph1000-hist.yml        # PH1000 30-day history (hourly) - UC12
│   ├── trigger-ph1800.yml             # PH1800 live dashboard, all inverters (~10 min loop) - UC15
│   ├── trigger-ph1800-hist.yml        # PH1800 30-day history (hourly) - UC15
│   ├── trigger-solis-switch.yml       # SolisCloud ON/OFF watchdog (2h) - UC13
│   ├── trigger-battery-health.yml     # SolisCloud battery health (monthly) - UC17
│   ├── trigger-bms-ingest.yml         # ESP32 BMS reading ingest (on device push) - UC16
│   └── tests.yml                      # Full test suite, pull requests only - no UC
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
│       ├── push_data_commit.sh        # Commit + push with retry/merge; used by 7 workflows (#28, #29)
│       ├── merge_alarm_state.py       # Merges cumulative state files on a push conflict
│       ├── check_shinemonitor_monthly.sh  # ShineMonitor monthly data
│       ├── check_shinemonitor_yearly.sh   # ShineMonitor yearly data
│       ├── check_dessmonitor_monthly.sh   # DessMonitor monthly data (UC10)
│       ├── check_dessmonitor_yearly.sh    # DessMonitor yearly data (UC10)
│       ├── check_device_alarms.sh     # Fetch device alarms
│       ├── check_dessmonitor_device_alarms.sh  # DessMonitor device alarms (UC10)
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
│       ├── email_utils.py             # Shared email formatting and sending
│       ├── check_ph1000.py            # PH1000 inverter telemetry (UC12)
│       ├── check_ph1000_bms.py        # PH1000 battery BMS, PACEEX packs (UC12)
│       ├── check_ph1000_weather.py    # Weather + solar-harvest impact, Open-Meteo (UC12)
│       ├── check_ph1800.py            # Inverter telemetry, all inverters (UC15)
│       ├── solis_common.py            # SolisCloud signed-request client (UC13, UC17)
│       ├── check_solis_switch.py      # SolisCloud ON/OFF watchdog (UC13)
│       ├── check_battery_health.py    # SolisCloud battery health report (UC17)
│       ├── solis/README_SOLIS.md      # SolisCloud setup + Control API guide (UC13)
│       │
│       │   # Run by hand or on a workstation - no workflow calls these:
│       ├── check_plant_roi.py         # ROI verification (UC11)
│       ├── analyze_plant_roi.py       # ROI report generator (UC11)
│       ├── check_shinemonitor_daily.sh    # Today's energy per plant, printed
│       ├── check_shinemonitor_query-plants.sh  # Auth + raw plant list, for debugging
│       ├── refresh_bms_creds.py       # Re-bootstrap BMS credentials (BlueStacks + frida)
│       ├── solis_mcp.py               # SolisCloud MCP server
│       └── tuya_mcp.py                # Tuya Cloud MCP server
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

`test_readme_tree.py` keeps this tree complete: a workflow or script that
reaches `main` without its line here fails the suite, and so does a line for
a file that is gone (#35).

## Testing

### Integration Test Suite

The project includes comprehensive integration tests covering all business logic.

#### Test Coverage: 778 tests — 722 passed, 56 skipped on Windows (bash and API tests run in CI)

In CI the two workstation-only MCP modules (Tuya, CCV heat pump) each skip as
a unit — they need `requests` / the `mcp` server package, which CI does not
install — so CI reports fewer passes and one extra skip per module. See *A
collection error that hid the whole suite* in Bug Fixes.

**Live inverters + BMS (150 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC12 (PH1000) | 39 | `test_ph1000.py` |
| UC12 (BMS cell frames) | 9 | `test_bms_cell_frame.py` |
| UC12 (BMS carry-forward) | 5 | `test_bms_carry_forward.py` |
| UC15 (PH1800) | 41 | `test_ph1800.py` |
| UC15 (PH1800 battery tab) | 8 | `test_ph1800_battery_tab.py` |
| UC12 + UC15 (dashboard clock) | 16 | `test_dashboard_clock.py` |
| UC12 + UC15 (live loop budget) | 16 | `test_live_loop_budget.py` |
| UC16 (ESP32 BMS ingest) | 16 | `test_bms_ingest.py` |

**SolisCloud (54 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC13 (Solis ON/OFF Watchdog) | 44 | `test_solis_switch.py` |
| UC17 (Solis battery health) | 10 | `test_battery_health.py` |

**Connectivity, exclusions and flags (127 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC14 (Connectivity + Exclusions) | 62 | `test_connectivity.py` |
| Feature flags | 34 | `test_feature_flags.py` |
| Fleet benchmark | 28 | `test_fleet_benchmark.py` |
| UC14 (state retention) | 3 | `test_connectivity_state_retention.py` |

**Six-hour gate (22 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| Gate rules (R1-R6, classes) | 14 | `test_alarm_triage.py` |
| Gate wiring + feature mode | 8 | `test_alarm_gate_wiring.py` |

**Alerting discipline (31 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC1 (send cap + interval) | 4 | `test_admin_alert_send_cap.py` |
| Hourly poll scope | 9 | `test_hourly_poll_scope.py` |
| UC9 (weekly all-clear cadence) | 18 | `test_all_clear_cadence.py` |

**Fetch safety (15 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| A fetch never destroys committed data | 15 | `test_fetch_never_destroys_data.py` |

**Public-log hygiene (7 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| No payloads in public logs | 7 | `test_no_payloads_in_public_logs.py` |

**Suite visibility (44 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| One bad import must not hide the suite | 44 | `test_suite_visibility.py` |

That suite parametrizes over every `test_*.py`, so its count rises by one
with each new test file — including an untracked one sitting in the
directory. If the total here looks one or two short, check
`git status` before assuming the table is stale.

**README accuracy (5 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| Project Structure tree matches the repo (#35) | 5 | `test_readme_tree.py` |

The first test that reads this README: every workflow and script on `main`
must have its line in the tree, and every line must name a file that exists.

**Tuya (28 tests, workstation-only):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| Tuya Cloud MCP (no UC yet) | 28 | `test_tuya_mcp.py` |

**Heat pump (11 tests, workstation-only — NOT YET ON MAIN, so excluded from the total above):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| CCV heat pump MCP (no UC yet) | 11 | `test_ccv_heatpump_mcp.py` |

No network: register decoding against a real snapshot from the display, the
compressor-relay running flag, the DHCP discovery path (fingerprint, ARP
parsing, subnet fallback, cache) and that only read function codes can be sent.

**Data-commit push (45 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| Retry, abort and conflict policy | 27 | `test_data_commit_push.py` |
| Cumulative state merge | 8 | `test_merge_alarm_state.py` |
| No bare push to main | 10 | `test_no_bare_push_to_main.py` |

**ShineMonitor (138 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC1 (Admin Alerts) | 15 | `test_admin_alerts.py` |
| UC2 (Customer Weekly) | 16 | `test_customer_weekly.py` |
| UC3-UC8 (Device Alarms) | 40 | `test_device_alarms.py` |
| UC9 (Email Optimization) | 9 | `test_uc9_admin_email.py` |
| UC11 (Plant ROI) | 19 | `test_uc11_plant_roi.py` |
| BVT (Centralized Config) | 14 | `test_centralized_config.py` |
| API Tests + vendor-condition handling | 25 | `test_shinemonitor_api.py` |

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
py -m pytest integration/test_all_clear_cadence.py -v  # UC9: Weekly all-clear gate
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
