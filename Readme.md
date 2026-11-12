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
| SolisCloud   | `soliscloud.com:13333`  | Production (UC13, UC17, UC18)|
| SolaX Cloud  | `openapi-eu.solaxcloud.com` | Production (UC21)|
| Deye Cloud   | `eu1-developer.deyecloud.com` | Production (UC19)|
| SOLARMAN Business | `globalpro.solarmanpv.com` | Production (UC22)|

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

### One run, two ways

A scheduled run takes one of two ways. Production data takes the **CSV way**,
the same five steps in the same order:

```
  ShineMonitor API ─┐
  DessMonitor  API ─┤
  SolisCloud   API ─┼─► COLLECT   check_*_monthly.sh / _yearly.sh   (bash, per platform)
  Deye Cloud   API ─┤             └─► data/{plant}-YYYY-MM.csv      date,kwh
  SolaX Cloud  API ─┤                 data/{plant}-YYYY.csv         month,kwh
  SOLARMAN     API ─┘             Deye Cloud, SolaX, SOLARMAN: no CSV; they take
                                  the six-hour-gate way only (below)
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

Device alarms take the **six-hour-gate way**. Every platform's alarms, or the
alarms made from its readings, go through one gate before anyone is mailed:

```
  ShineMonitor  warning list ─┐
  DessMonitor   warning list ─┤
  SolisCloud    alarm list   ─┤
  Deye Cloud    alert list   ─┼─► READ      check_*_alarms.py, check_*device_alarms.sh
  SolaX Cloud   alarm list   ─┤             one record per alarm: plant, device, message, start
  MUST          readings     ─┤             (MUST and SOLARMAN: alarms made from readings)
  SOLARMAN      readings     ─┘
                                                │
                                                ▼
                                  GATE      six-hour-gate/alarm_triage.py
                                            still open 6 h after it started?
                                            the COMMS class is never mailed
                                                │
                                                ▼
                                  DECIDE    3 sends, 4 h apart (filter_alarms_to_send)
                                            features.py — is this channel live?
                                                │
                                                ▼
                                  NOTIFY    admin mail; UC3 also mails customers
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
| `mask_credentials.sh` | Masks every secret in a credentials file in the job's logs; run wherever a workflow writes one |
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
| `check_solis_alarms.py` | UC18 SolisCloud alarms through the six-hour gate |
| `check_deye_alarms.py`, `deye_cloud.py` | UC19 Deye Cloud alarms through the six-hour gate, and its read-only client |
| `check_solax_alarms.py`, `solax_cloud.py` | UC21 SolaX Cloud alarms through the six-hour gate, and its read-only client |
| `check_must_telemetry.py` | UC20 MUST grid-loss and backup-failed alarms, made from the inverters' own readings, through the six-hour gate |
| `check_solarman_alarms.py`, `solarman_cloud.py` | UC22 SOLARMAN alarms made from the inverters' readings, status and silence, through the six-hour gate, and its read-only client, which renews its own sign-in |
| `check_ph1000*.py` | UC12 PH1000 dashboard and its PACEEX BMS decode |
| `check_ph1800.py` | UC15 generic pass-through dashboard, any inverter |
| `bms-module/scripts/ingest_bms_reading.py` | UC16 ESP32 reading: CSV, thresholds, alert |

### Where the truth lives

| File | Holds | If it is missing or broken |
|---|---|---|
| `config/credentials.json` | ShineMonitor accounts, plants, customer emails, the `solis` and `deye` blocks | Gitignored. In CI it comes from `SHINEMONITOR_CREDENTIALS_JSON` |
| `solarman_tokens.json` (CI only) | UC22's SOLARMAN token pair | Gitignored. Written from `SOLARMAN_TOKENS_JSON` for the run and deleted after it; a renewal is written back to that secret |
| `config/dessmonitor_credentials.json` | DessMonitor accounts (UC10) | Gitignored. From `DESSMONITOR_CREDENTIALS_JSON` |
| `config/features.json` | Which emails and alerts are live | **Fails open** — an unknown flag reads as ON, so adding a channel never silently mutes an existing one. Override for one run with `KT_FEATURE_<DOTTED_PATH>=true` |
| `config/excluded_plants.json` | Plants deliberately dropped, each with a reason and a date | **Fails closed** — it excludes nothing. Losing the file must never silently drop a paying customer off the platform |
| `config/must_telemetry.json` | UC20 thresholds (grid, flat battery 22.5 V / 45.5 V, freshness) and `no_grid_expected`, the plants built without an AC input | Committed. The list ships **empty**: every plant is expected to have grid |
| `config/solarman_alarms.json` | UC22 thresholds, the SOLARMAN series names per inverter type, product ids, and rules switched off per plant | Committed. `rules_off` ships **empty** |

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
| `state/solis_alarms_state.json` | SolisCloud alarms (UC18): the same fields as device alarms, open alarms only |
| `state/deye_alarms_state.json` | Deye Cloud alarms (UC19): the same, for Deye |
| `state/solax_alarms_state.json` | SolaX Cloud alarms (UC21): the same, for SolaX |
| `state/must_telemetry_state.json` | MUST telemetry (UC20): the same alarm fields, plus each inverter's last reading, open outage and backup episode under `device:pid:pn` |
| `state/solarman_alarms_state.json` | SOLARMAN alarms (UC22): the same, plus each inverter's open episodes and status under `device:station:sn`, and the sign-in's own alarm under `solarman:signin:<kind>` |
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

This was an experiment with a measurable answer, and the answer came back on
2026-10-23: **it did not help.** In the 20 hours after the move (12:00 UTC on
the 22nd to 08:00 on the 23rd) GitHub created 9 of the ~25 scheduled
ShineMonitor runs asked for, 8 of ~25 DessMonitor, 4 of 10 Solis, and 5 of
~20 for each history job — the same third as before. The minutes moved off
the top of the hour (:41, :47, :51) delivered no better than the ones still in
its first twenty minutes (:13, :15, :17), so where in the hour a cron sits is
not what decides whether it fires, and moving crons again is not worth a
commit. The remedy for #31 is one of the other three — a self-chaining loop
like PH1800 already uses, an external scheduler calling `workflow_dispatch`,
or a self-hosted runner. Moving to another repository would not help: the
scheduler is shared, and this repository is already public, so there is no
minutes quota to escape.

**A push runs the test suite only (2026-10-23).** Until then a push to `main`
also ran the full ShineMonitor collection, the DessMonitor collection and both
weekly report workflows, as "build verification". The ShineMonitor collection
takes 80–90 minutes and a push run first queued behind the scheduled run
holding its concurrency slot: on 2026-10-21 that was 28 minutes waiting and 89
running, about two hours from push to "Build OK". Nothing in those runs
differed from what the next scheduled run does anyway, and the weekly
workflows mailed two admin summaries per push. Now only
`trigger-shinemonitor.yml` listens to `push`, only its `tests` job runs on
one, and that job sends the "Build OK" or "TESTS FAILED" email in about five
minutes. A manual run (`workflow_dispatch`) still does everything, for
end-to-end verification on demand. `test_push_is_tests_only.py` keeps it so.

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
| `trigger-shinemonitor.yml` | ShineMonitor — collect, alert, test | Hourly at **:13**, **plus** 6x daily at **:37** past 02/06/10/14/18/22 UTC. On a push to `main`: the `tests` job only | Hourly: device alarms only. 4-hourly: collect, connectivity, anomalies, admin email. Push: tests and the "Build OK" / "TESTS FAILED" email |
| `trigger-dessmonitor.yml` | DessMonitor — collect, alert | Hourly at **:41**, **plus** 6x daily at **:51** past 03/07/11/15/19/23 UTC | The same for DessMonitor (UC10) |
| `trigger-customer-reports.yml` | ShineMonitor — weekly customer reports | Sunday, **04:19** UTC | UC2 weekly reports and the admin summary |
| `trigger-dessmonitor-weekly-reports.yml` | DessMonitor — weekly customer reports | Sunday, **04:29** UTC | UC2 for DessMonitor |
| `trigger-ph1000.yml` | PH1000 — publish live data | Every 2 h, restarting a self-chaining loop | UC12 live data to the `ph1000-live` branch |
| `trigger-ph1800.yml` | PH1800 — publish live data, any inverter | Every 2 h, restarting a self-chaining loop | UC15 live data to the `ph1800-live` branch |
| `trigger-ph1000-hist.yml` | PH1000 — publish 30-day history | Hourly | UC12 30-day window on `ph1000-hist`, decoupled from the live cycle |
| `trigger-ph1800-hist.yml` | PH1800 — publish 30-day history | Hourly | UC15 30-day window on `ph1800-hist`, decoupled from the live cycle |
| `trigger-solis-switch.yml` | Solis — re-enable a switched-off inverter | Every 2 h at **:15** | UC13 watchdog. The Control API key went live on 16 Oct 2026, so it can now re-enable, not just report |
| `trigger-solis-alarms.yml` | Solis — alarms open 6h+ (hourly) | Hourly at **:33** | **UC18** SolisCloud alarms on hybrid inverters through the six-hour gate; admin only |
| `trigger-deye-alarms.yml` | Deye — alarms open 6h+ (hourly) | Hourly at **:27** | **UC19** Deye Cloud alarms on hybrid and off-grid inverters through the six-hour gate; admin only |
| `trigger-solax-alarms.yml` | SolaX — alarms open 6h+ (hourly) | Hourly at **:21** | **UC21** SolaX Cloud alarms through the six-hour gate, checked 09:00–17:00 plant time only; admin only |
| `trigger-must-telemetry.yml` | MUST — grid and battery alarms from telemetry (hourly) | Hourly at **:43** | **UC20** grid loss and backup failed on the ShineMonitor MUST inverters, from their 5-minute readings, through the six-hour gate; admin only |
| `trigger-solarman-alarms.yml` | SOLARMAN — alarms open 6h+ (hourly) | Hourly at **:53** | **UC22** grid loss, backup, battery, output, load, fault and no-data alarms on the SOLARMAN plants, from their readings, through the six-hour gate; admin only. Does nothing until `SOLARMAN_TOKENS_JSON` exists |
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
| `trigger-solis-alarms.yml` | **UC18** |
| `trigger-deye-alarms.yml` | **UC19** |
| `trigger-solax-alarms.yml` | **UC21** |
| `trigger-must-telemetry.yml` | **UC20** |
| `trigger-solarman-alarms.yml` | **UC22** |
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
  why it runs for 80–90 minutes. A push no longer waits behind it — since
  2026-10-23 a push runs the `tests` job only — but a manual run still does,
  and if that loop needs to get shorter too, splitting this workflow buys far
  more than moving crons does.

**Not part of the pipeline**, though they live beside it:
`refresh_bms_creds.py` re-bootstraps the BMS credentials from the running
PACEEX app and needs BlueStacks plus frida on a workstation; and
`check_shinemonitor_query-plants.sh` is referenced by no workflow and no test —
a manual query helper, which is worth knowing before anyone relies on it.

**MCP servers are not in this repo.** The stdio servers that let Claude read
SolisCloud, Deye Cloud and the author's Tuya home devices are workstation
tools. They live, with their tests, in `scripts/mcp_servers/`, which is
gitignored, and the Valla heat-pump server is in a separate private repo.
`solis_mcp.py` and `tuya_mcp.py` sat beside the scripts here until 2026-11-04.

Customer-facing email goes out **only on scheduled runs**; admin email goes out
on scheduled and manual runs (UC8). A push sends only "Build OK" or "TESTS
FAILED": since 2026-10-23 a push runs the test suite and nothing else. The test
customer Gayan-IMH is the exception and also receives on manual runs (UC4),
which is how a change is proven end to end without mailing the customer base:
press *Run workflow*.

The hourly run exists so the six-hour gate's dwell is literal rather than
six-to-ten. It polls alarms and **nothing else**: it does not fetch CSVs, so it
must not analyse them either. `check_anomaly.py` and `check_connectivity.py`
carry the same hourly-skip guard as the fetch steps, and
`test_hourly_poll_scope.py` keeps it that way — running them on stale data gave
two alternating fleet views that overwrote each other's state and mailed the
admin several times a day.

"ALL SYSTEMS OPERATIONAL" goes out **once per ISO week**, on the first
scheduled run of that week. No cron is named, and a manual run with nothing
wrong says nothing at all (a push never reaches this step). Alerts still send whenever the content
changes, on any trigger; a clean bill of health is a weekly thing.

### Rules that may not be broken

1. **A dead link is never a dead system.** Silence is a connectivity finding.
2. **An exclusion is a decision, not a symptom.** A quiet plant stays monitored
   and stays in the counts; only a plant the business has dropped is excluded.
3. **Feature flags fail open, exclusions fail closed.** Opposite directions, on
   purpose: in each case the failure that shows is preferred to the one that hides.
4. **Three sends, four hours apart, then quiet** — for every repeating alert.
5. **Platform data never mixes.** Filter on the `dessmonitor-` prefix.
6. **Credentials never enter the repo, or a log.** They arrive from GitHub
   secrets, every value is masked as the job writes the file, and tests pass
   them through the environment, never in the text of a command.
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

**Schedule:** Every Sunday at 04:19 UTC (GitHub often starts it hours late)

**Platforms Supported:**
- **ShineMonitor:** [trigger-customer-reports.yml](.github/workflows/trigger-customer-reports.yml)
- **DessMonitor:** [trigger-dessmonitor-weekly-reports.yml](.github/workflows/trigger-dessmonitor-weekly-reports.yml)

**Report Contents:**

- Weekly production summary (last 7 days)
- Monthly progress (current month)
- Yearly totals (year-to-date)
- Plant count and combined statistics
- Active production alerts (if any)

**Where the yearly totals come from:** the report fetches nothing. It sums the
rows of the committed `{plant}-YYYY.csv` files, so a wrong row there is a wrong
number in a customer's inbox. A month's row changes only when the vendor answers
with a figure. A failed call or a "no data" answer keeps what is committed and
logs a `WARNING` naming the month (see *A failed call zeroed one month at a time*
in Bug Fixes).

**Email Recipients:** Customers with email in credentials.json (platform-specific)

**Platform Filtering:**
- ShineMonitor reports: Exclude `dessmonitor-*` CSV files
- DessMonitor reports: Only include `dessmonitor-*` CSV files
- Email subject: DessMonitor emails prefixed with `[DessMonitor]`

**Admin Summary:**
- Aggregates all customers per platform
- Sent to admin on scheduled and manual runs (a push no longer runs this workflow, since 2026-10-23)
- Platform-specific summaries (ShineMonitor vs DessMonitor)

**UC8 Schedule Filter:**
- **Admin emails:** sent on every run (scheduled and manual)
- **Customer emails:** ONLY on scheduled runs (Sunday)
- **Manual triggers:** Admin-only mode. A push does not run this workflow at all (a push runs the test suite only, since 2026-10-23)

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
- Vendor silence: a ShineMonitor login or call that gets no answer is tried once more, 3 s later. The first try gets 15 s and the second 25 s. This shares `shinemonitor_common.sh` with data collection. Since 2026-10-27 about four calls in ten from a GitHub runner hang, and without the retry each run missed alarms for 9-16 of the 23 accounts (see Bug Fixes, 2026-10-30)
- What UC3 cannot see: ShineMonitor's warning list never reports a MUST inverter losing the grid, and does not always report a flat battery. Those alarms are made from the inverters' readings by **UC20**, and pass through the same gate

**Test Coverage:** 40/40 PASSED — `test_device_alarms.py`, which also
carries the UC4, UC5, UC6, UC7 and UC8 tests

---

### UC4: Test Customer Email Control

Enables continuous testing of device alarm workflow using Gayan-IMH as test customer, bypassing normal 3-send limits on manual triggers. (Until 2026-10-23 a push did this too; a push now runs the test suite only.)

**Gayan-IMH is KTronics (Pvt) Ltd's own plant** (Imbulgoda 3 kW), so testing on
it mails nobody but us. Test mode bypasses its ignored flag and the 3-send cap,
so the `send_count` of its newest alarm in `state/device_alarms_state.json`
rises by one on every manual run and reads far above 3. That is this use case
working, not a stuck alarm: don't "fix" it, and anything that reads the state
file, the #19 daily report included, should expect it. Test mode finds an alarm
to send only because closed alarms still look open (#40); a fix there must keep
this test firing.

**Workflow Behavior:**

**Scheduled Runs (every 4 hours):**
- All customers: Normal alarm processing with 3-send limit
- Gayan-IMH: Treated same as other customers (receives alarms normally)

**Manual Runs (`workflow_dispatch`):**
- All customers: Alarm processing skipped for emails
- Gayan-IMH ONLY: Receives device alarm emails with test mode enabled
  - Bypasses ignored flag
  - Bypasses send count limit (3-send rule disabled)
  - Bypasses the 4-hour interval
  - Always includes most recent alarm

**Implementation:**

**Device Alarm Processing** (the `--test-mode` branch of `main()` in [generate_device_alarms.py](src/main/java/org/ktronics/scripts/generate_device_alarms.py)):
- `--test-mode` flag bypasses 3-send limit and ignored flag
- Used on manual triggers only (the device-alarm step of [trigger-shinemonitor.yml](.github/workflows/trigger-shinemonitor.yml))

**Customer Email Filtering** (`--test-customer-only` in [trigger-shinemonitor.yml](.github/workflows/trigger-shinemonitor.yml)):
- Scheduled runs: Send to ALL customers
- Manual runs: `--test-customer-only` filters to Gayan-IMH only

**Related Use Cases:**
- UC5 (Test Mode Filter) - Filters alarms to Gayan-IMH in test mode
- UC8 (Schedule Filter) - Controls customer email delivery based on trigger type

**Implementation Status:** ✅ **IMPLEMENTED** (Session 13)

**Test Coverage:** 3 tests in `TestTestModeRunsTheRealScript`
(`test_device_alarms.py`). They run the real `main()` with the manual run's
arguments. The newest Gayan-IMH alarm is mailed though ignored at send 3:
its state goes to 4 and an older one stays at 3. An alarm sent minutes ago
at send 2 is mailed anyway. And test mode adds nothing when real alarms are
already going out.

---

### UC5: Test Mode Filter (Part of UC3)

Test mode filters alarms to Gayan-IMH (test customer) only.

**Purpose:** Allows testing alarm workflow without sending to all customers

**Parent Use Case:** UC3 (Device Alarm Alerts)

**Test Coverage:** 2 tests in `TestTestModeRunsTheRealScript`
(`test_device_alarms.py`), through the real `main()`: another customer's
newer alarm is never picked, and with no Gayan-IMH alarm nothing is added.

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
| Manual | Yes | No |
| Push | Does not run | Does not run |

A push stopped running the weekly workflows on 2026-10-23 (a push runs the
test suite only). The `schedule` / `!= schedule` conditions stay, because
manual runs still need them.

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
  - Manual trigger with alerts (a push never runs this job, since 2026-10-23)
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
of that week**, with no exceptions. A manual run reports only when something
is wrong: build verification used to send unconditionally, which put an empty
status email in the inbox on every developer push — noise in UC1, whose
content reports no event, and in UC9, whose cadence it ignored. Since
2026-10-23 a push does not reach this step at all (a push runs the test suite
only). Alerts (`exit_code == 2`) never enter the gate on any trigger.
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
5. Send email only if the content changed, or a manual run found alerts (a push never runs this step)
6. Save current hash for next run

Step 5 also applies the weekly all-clear gate described above.

**Implementation Status:** ✅ **IMPLEMENTED** (Session 13); all-clear gate
added 2026-10-16

**Test Coverage:** 27/27 PASSED — `test_uc9_admin_email.py` (9),
`test_all_clear_cadence.py` (18)

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

**A month of zeros is not proof of silence either (2026-11-01).** A fetch no
longer lets a wholly zero month replace real readings, because a dead plant
sharing a filename with a live one produced exactly that (see *Two plants, one
file* in Bug Fixes). Before believing a LINK DOWN alert for `troy-troy-hybrid-3kw`,
remember that its account holds two plants. `test_committed_data_integrity.py`
now fails, and emails the admin, if a month's daily rows read zero while its
yearly row shows it produced.

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

### UC18: SolisCloud Alarms Through the Six-Hour Gate

Brings the SolisCloud fleet under the [six-hour gate](six-hour-gate/README.md)
that ShineMonitor and DessMonitor device alarms already pass through: the admin
is mailed about an alarm on a **hybrid** inverter only when it is **still open
6 hours after it started**: no grid, a battery alarm, an overload. A CEB cut
that lasts two hours never reaches the inbox.

**Implementation Status:** ✅ **IMPLEMENTED** (23 Oct 2026). This is step 5 of
the gate's order of work, the Solis adapter, parked until the SolisCloud API key
arrived (16 Oct 2026).

**Why hybrids matter here:** a hybrid with no grid is running the house off its
battery. Six hours of that, and the battery is heading for its discharge floor,
where the BMS can lock the pack out and it will not restart on its own.

**How it works:**

1. Runs **hourly at :33** ([trigger-solis-alarms.yml](.github/workflows/trigger-solis-alarms.yml)), so the six hours are literal, as on the ShineMonitor alarm poll.
2. One call, `POST /v1/api/alarmList`, for every alarm that began in the last 7 days, across all plants on the account.
3. Keeps alarms that are **open** (`state == "0"`), on a **hybrid** (`machine` reads `S6-EH…`, `RHI…` or `RAI…`), on a plant not in `excluded_plants.json`.
4. Hands them to the gate exactly as UC3 does: `triage_alarms()` (six-hour dwell, COMMS never mailed, the `alerts.six_hour_gate` off / dry_run / on mode), then `filter_alarms_to_send()` (3 sends, 4 h apart).
5. Emails the admin one list: plant, inverter, alarm, open since (plant-local time), hours open, send n/3, and the gate's first thing to check.

**Hybrids only, by decision (23 Oct 2026).** A grid-tie inverter shuts down at
dusk, so its alarm cannot clear until it wakes the next morning. Ruwani's data runs
06:33–18:13 and nothing at night. On 17 Oct Nadun, Tiqri and Malith read
NO-Grid for 16–18 h for a CEB cut that ended that evening. On a grid-tie plant
the six hours would be mostly darkness. A hybrid runs around the clock, so its
alarm times are real.

**What it would have sent, replayed over 16–23 Oct 2026:** nine alarms on six
plants. They were the 17 Oct CEB outage (NO-Grid 6.6–9.2 h at Hiran's three floors,
Hewune, Vajira and Harith), Gihan Home's overload (10.9 h), Harith's NO-Grid on
18 Oct (11.2 h) and Surath's overnight NO-Grid on 22 Oct (6.3 h). The dozens of
shorter cuts stayed silent.

**Found while wiring it — three gate faults that would have shipped:**

- **Every open Solis alarm read as resolved.** The gate tested `state == 1` for
  "open", written before there was an API key. The live alarm list says `"0"`
  (2 open alarms) and `"2"` (58 cleared). `"1"` is *processed*: someone handled it,
  and per R3 a handled alarm never escalates. Uncaught, the adapter would have run
  every hour and mailed nothing, ever.
- **A dead logger would have been mailed.** SolisCloud words it "Loss of
  internet connection" (code `1D4C2`), which no COMMS pattern matched. It fell
  to UNKNOWN, took the six-hour clock, and Kamal Kadawath's 3-day-old one would
  have gone out on the first run. Now mapped to COMMS by code and by text.
- **Alarms the code already placed were logged as unclassified.** `normalize()`
  classified the text first and applied the vendor code after. Every `UV-G-V`
  (code 1011, GRID) was logged as unknown, and that log is how the class map
  grows. Now the code is checked first.

The Solis vendor codes seen in the fleet are now in the class map: 1010–1013
and 1019 (grid voltage and frequency faults) and 1015 (NO-Grid) map to GRID,
1057 (Overload) to LOAD and `1D4C2` to COMMS. **No Solis battery alarm has been
seen yet**, so its exact wording is not in the map. When one comes it lands in
UNKNOWN, which takes the same six-hour clock and is logged, and its code goes
into the map then.

**State:** `state/solis_alarms_state.json` holds the device-alarm fields
(`send_count`, `last_sent`, `ignored`, `plant`, `message`, and the gate's
`class` and `suppressed_reason` for alarms it is holding). It is keyed
`stationId:inverterSN:code@startTime`, so one occurrence keeps one key however
SolisCloud numbers its records. An alarm that is no longer open is forgotten:
the same key never reopens, so nothing is lost, and the file does not grow with
every blip.

**Public logs:** the run prints counts and alarm keys only, never plant names
(#4). `--dry-run` prints the email body, and is meant for a workstation.

**Not covered: a dead Solis logger.** COMMS is never mailed by the gate, because
elsewhere a dark logger writes `0.0000` CSV rows and trips the 3-day
production-loss alert (R4). **Solis plants are not in that CSV pipeline**, so a
Solis logger that stays dark is not alerted by anything yet.

**Feature flags:** `emails.solis_alarms_admin` (the email), and
`alerts.six_hour_gate` (the gate itself, shared with UC3).

**Scripts:**

| Script | Purpose |
|--------|---------|
| `check_solis_alarms.py` | Fetch the alarm list → hybrids, open → six-hour gate → 3x/4h → email |
| `generate_device_alarms.py` | The gate and send rules it reuses (`triage_alarms`, `filter_alarms_to_send`) |
| `six-hour-gate/alarm_triage.py` | `normalize()` / `is_actionable()`, which already had a Solis path |

**CLI:**

```bash
py check_solis_alarms.py              # gate + email the admin + save state (cron entrypoint)
py check_solis_alarms.py --dry-run    # print what would be mailed; no email, no state
```

**Test Coverage:** 31/31 PASSED — `test_solis_alarms.py`; the gate fixes are
4 more in `test_alarm_triage.py`

---

### UC19: Deye Cloud Alarms Through the Six-Hour Gate

The Deye twin of UC18. The admin is mailed about an alarm on a Deye **hybrid
or off-grid** inverter only when it is **still open 6 hours after it
started**, then at most 3 times, 4 hours apart. A two-hour CEB cut never
reaches the inbox.

**Implementation Status:** ✅ **IMPLEMENTED** (5 Nov 2026). It needs the
`deye` block in the `SHINEMONITOR_CREDENTIALS_JSON` secret. Until that block
is there, every hourly run fails with "No 'deye' section in credentials.json".

**How it works:**

1. Runs **hourly at :27** ([trigger-deye-alarms.yml](.github/workflows/trigger-deye-alarms.yml)).
2. Makes about 40 calls, one a second:
   - `POST /station/list` for every plant on the company account (35 on 5 Nov 2026).
   - `/station/device`, ten plants per call, for each device's product id.
   - `/station/alertList` per plant for the last 7 days.
3. Keeps an alarm when all three hold:
   - it is **open**: `status` 1, or no end time when there is no usable status;
   - it is **not on a grid-tie** product (`0_5406_1`); an unknown product id is kept, because a gap in the feed must never silence alerting;
   - its plant is not in `excluded_plants.json`.
4. Hands them to the gate exactly as UC3 and UC18 do: `triage_alarms()`, then `filter_alarms_to_send()`.
5. Emails the admin one list. Each alarm shows:
   - plant, inverter type and serial;
   - alarm name with its protocol word and code, level and class;
   - open since (plant-local time) and hours open;
   - send n/3, and the gate's first thing to check.

**Grid-tie left out, as on Solis.** The fleet has one grid-tie unit,
Tharanga Prasad Ongrid 20 KW. Its AC_NoUtillity alarm (F35) of 17 Oct read
open from 12:44 until 05:48 the next morning.

**What it would have sent, replayed over 6 Oct–5 Nov 2026:** five alarms, nine
emails. All five ran from dusk to sunrise: they began between 17:35 and 22:17
and ended between 06:05 and 06:19.

| Plant | Alarms |
|---|---|
| Venura Kottawa | F46 Backup_Battery, F59, F47 |
| Hiran kesbewa | F47 |
| Umesh | F59 |

That is the grid-tie pattern again: an inverter asleep at night cannot clear
its alarm until the sun wakes it. Venura Kottawa has no battery by design but
is configured with one, which is also where its F46 comes from. These plants
are left in for now. Whether to drop no-battery hybrids too, as grid-tie units
are dropped, is a decision for the owner.

**Found while wiring it:**

- **Through the gate's generic reader, Deye's open and closed came out
  inverted.** The reader follows ShineMonitor, where a `status` of false or 0
  means *unhandled*, so open. Deye uses 1 for open and 0 for closed. Every
  closed Deye alarm would have read open, and every open one resolved. The
  adapter now marks each alarm `open` explicitly, and a test holds it.
- **`alertCode` does not identify a fault.** It is a bit number inside a
  protocol word: code 12 is F13 Grid_Mode_changed in ERR3 and F45 overvoltage
  in ERR5. So the key carries the protocol word, and the class map matches the
  name, never the code.
- **`/station/device` takes at most ten station ids** per call. More returns
  "list too long".
- **Alert names carry no-break spaces and Deye's own spellings**, for example
  "W3\xa0Grid_Phase_Wrong", "AC_NoUtillity" and "AC_WU_U nderVolt". The email
  shows them cleaned; the class map matches them as Deye writes them.

**Class map:**

| Class | Deye alarms |
|---|---|
| GRID | F13, F35, F42, F45, F47, F48, W3 Grid_Phase_Wrong |
| SOC | F56 DC_VoltLow, F46 Backup_Battery, F25 Active_Batt |
| HARDWARE | F26 BusUnbalance, F55 DC_VoltHigh |
| LOAD | F34 Overload (already mapped) |
| UNKNOWN | F59 AC_V_GridCurr_High, F57 AC_BackFeed, a bare "F13" |

UNKNOWN alarms take the same six-hour clock and are logged. Every class except
COMMS has the same dwell, so the class decides only the email's hint. Deye has
no comms alarm in its alert list.

**State:** `state/deye_alarms_state.json` holds the same fields as UC18, keyed
`stationId:deviceSN:protocol-code@startEpoch`. An alarm that is no longer
open is forgotten.

Two failures save no state:

- **A failed read.** A half-read fleet would prune the plants it missed, and
  their sent alarms would start again at Send 1/3.
- **A token that sees no plants at all** (`company_id` missing). This counts as
  a failure, not an all-clear.

**Public logs:** counts and alarm keys only, never plant names (#4).

**Not covered: a dead Deye logger.** Deye reports it as a device status, not
an alert, and Deye plants are not in the CSV pipeline. Nothing alerts on it
yet.

**The client, `deye_cloud.py`:**

- Standard library only.
- Read-only: an allowlist of the three paths above, so no `/order/*` control
  call can be sent from CI.
- Logs in again once if a token is refused.
- Retries once after a rate limit, a server error or a network error.

The workstation's Deye MCP has its own, fuller client. Like every MCP server,
it stays out of the repository.

**Feature flags:** `emails.deye_alarms_admin` (the email), and
`alerts.six_hour_gate` (the gate itself, shared with UC3 and UC18).

**Scripts:**

| Script | Purpose |
|--------|---------|
| `check_deye_alarms.py` | Plants → product ids → alert lists → open, not grid-tie → six-hour gate → 3x/4h → email |
| `deye_cloud.py` | Read-only Deye Cloud client: token, three read paths, one retry |
| `generate_device_alarms.py` | The gate and send rules it reuses; now names the vendor `Deye` |
| `six-hour-gate/config/alarm_triage.json` | The Deye alarm names in the class map |

**CLI:**

```bash
py check_deye_alarms.py              # gate + email the admin + save state (cron entrypoint)
py check_deye_alarms.py --dry-run    # print what would be mailed; no email, no state
```

**Test Coverage:** 58/58 PASSED — `test_deye_alarms.py`

---

### UC21: SolaX Cloud Alarms Through the Six-Hour Gate

The SolaX twin of UC18 and UC19. The admin is mailed about an alarm on any
SolaX inverter, grid loss included, only when it is **still open 6 hours after
it started**, then at most 3 times, 4 hours apart.

**Implementation Status:** ✅ **IMPLEMENTED** (11 Nov 2026). It needs the
`solax` block in the `SHINEMONITOR_CREDENTIALS_JSON` secret (`client_id`,
`client_secret`, `api_base`, `tz_offset_minutes`). Until it is there, every
hourly run fails with "No 'solax' section in credentials.json". Before this,
SolaX had no automated alerting at all.

**How it works:**

1. Runs **hourly at :21** ([trigger-solax-alarms.yml](.github/workflows/trigger-solax-alarms.yml)).
2. Fetches a fresh OAuth token every run (client-credentials; it lasts 30 days, so none is stored), then reads the 24 residential plants and each plant's **ongoing** alarms, one call every 0.75 s (the limit is 100 a minute). Closed history is never read.
3. Keeps an alarm when it is open, not stale (below) and its plant is not in `excluded_plants.json`.
4. Hands them to the gate as UC3, UC18 and UC19 do: `triage_alarms()`, then `filter_alarms_to_send()`.
5. Emails the admin one list: plant, model and serial, alarm name with code, level and class, open since (plant-local) and hours open, send n/3 and the first thing to check.

**Why SolaX needed its own rule.** All 24 plants are grid-tie, so UC18's and
UC19's answer (leave grid-tie inverters out) would leave nothing to watch. The
problem it answers is real, though: an inverter asleep at night cannot clear an
alarm that ended at dusk. SolaX's history shows it, with 35 of 540 closed
alarms spanning midnight and the longest at 100-168 hours. Two rules instead:

- **Daytime only.** The run evaluates alarms between **09:00 and 17:00 plant
  time** and otherwise does nothing, state untouched. By then every working
  inverter is awake and would have cleared a finished alarm, so what is still
  open is real. An alarm that began the evening before and is open at 09:00 is
  mailed as usual if it has passed 6 hours.
- **Stale cap, 14 days.** An older alarm is a record on a dead inverter, not a
  fault in progress. Pasindu 6kw had GridFreqFault and GridVoltFault open since
  18 Sep on an offline inverter, 53 days when this was built. They are counted
  and skipped.

**A real outage still gets through.** A grid cut of more than 6 hours in the
daytime leaves the alarm open and is mailed at the next run inside the window.
A cut that started overnight and is still open at 09:00 is mailed then.

**Known gap: an offline inverter.** Five of the 24 inverters were offline on
11 Nov 2026. UC21 does not report them: an offline unit is a device status in
SolaX, not an alarm. The 3-day production-loss rule is meant to catch them,
but SolaX plants have no CSV, so nothing does yet. This is open.

**Class map.** The alarm names the fleet has produced, all matched in
`alarm_triage.json` by name and by `vendor_codes.solax`:

| Class | SolaX alarms (errorCode) |
|---|---|
| GRID | GridLostFault (1), GridVoltFault (2), GridFreqFault (3), GridVolt10MFault and Ac10Mins_Voltage_Fault (7), GRID_LOSS (91), GRID_PHS_LOSS (102), GRID_UNBLC (103), GRID_UVP3 (108) |
| HARDWARE | ResidualOCP (11), leakage current |
| UNKNOWN | anything new; it takes the same six-hour clock and is still mailed |

**State:** `state/solax_alarms_state.json` holds the same fields as UC18,
keyed `plantId:deviceSN:errorCode@startTime`. An alarm that is no longer open
is forgotten. A failed read, or a token that sees no plants, saves no state:
a half-read fleet would prune the plants it missed and restart their sent
alarms at Send 1/3.

**Public logs:** counts and alarm keys only, never plant names (#4). The
`client_secret` and the access token are masked by `mask_credentials.sh`.

**The client, `solax_cloud.py`:** standard library only; read-only (an
allowlist of three paths, so no control or realtime call can be sent); logs in
again once if a token is refused; retries once after a rate limit, a server
error or a network error.

**Feature flags:** `emails.solax_alarms_admin` (the email) and
`alerts.six_hour_gate` (the gate itself, shared with UC3, UC18 and UC19).

**Scripts:**

| Script | Purpose |
|--------|---------|
| `check_solax_alarms.py` | Plants → ongoing alarms → open, not stale → six-hour gate → 3x/4h → email |
| `solax_cloud.py` | Read-only SolaX developer-API client: token, three read paths, one retry |
| `generate_device_alarms.py` | The gate and send rules it reuses; now names the vendor `SolaX` |
| `six-hour-gate/config/alarm_triage.json` | The SolaX alarm names and codes |

**CLI:**

```bash
py check_solax_alarms.py              # gate + email the admin + save state (cron entrypoint)
py check_solax_alarms.py --dry-run    # print what would be mailed; no email, no state
py check_solax_alarms.py --any-hour   # ignore the 09:00-17:00 window, for a manual look
```

**Test Coverage:** 28/28 PASSED — `test_solax_alarms.py`

---

### UC20: MUST Telemetry Alarms Through the Six-Hour Gate

The admin is mailed when a MUST inverter on ShineMonitor has had **no grid for
6 hours**, and when its **backup fails** in such an outage. Neither alarm comes
from ShineMonitor. Both are made here from the inverter's own 5-minute readings,
then go through the same six-hour gate and three-sends / four-hours rule as UC3,
UC18 and UC19.

**Implementation Status:** ✅ **IMPLEMENTED** (10 Nov 2026)

**Why it exists:** ShineMonitor's warning list does not report a MUST inverter
losing the grid. When one customer was without grid for 25 hours on 8–9 Nov
2026 and the inverter shut down twice on a flat battery, the gate had nothing
to hold (see *No alarm for a 25-hour MUST outage* in Bug Fixes).

**How it works:**

1. Runs **hourly at :43** ([trigger-must-telemetry.yml](.github/workflows/trigger-must-telemetry.yml)). It reads every account in the `accounts` block of the credentials, the same accounts UC3 polls, three at a time. Every account that fails then gets a second try, one at a time, within a 6-minute budget.
2. For each inverter it calls `queryDeviceDataOneDayPaging`, starting after the last reading it has already seen.
   - That is usually one call, because the newest rows come first.
   - A first look walks back a day at a time (at most 7) until a reading shows grid. An outage that began days ago is then timed from its real start.
   - An inverter whose logger is dark costs one call a run.
   - A feed with no grid or battery voltage is not watched.
3. It keeps a small state per inverter: its last two readings, any open outage and any open backup episode.
4. It makes at most two alarms per inverter and hands them to `triage_alarms()`, then `filter_alarms_to_send()`.
5. It emails the admin one list. Each alarm shows:
   - the plant and the inverter, what happened and since when;
   - the latest reading: grid, battery (and its lowest in this outage), load and output;
   - the hours on the clock, send n/3, and the gate's first thing to check.

**The rules:**

| Alarm | Class | Open when | Clock |
|---|---|---|---|
| Grid loss | GRID | an outage is open and the latest reading, under 30 minutes old, still shows no grid | the outage's start |
| Backup failed (shut down) | SOC | no grid, the battery's last reading is flat, and nothing has come for 20 minutes | the outage's start |
| Battery flat | SOC | no grid, two readings in a row flat | the outage's start |
| Backup failed (dead output) | SOC | no grid, two readings in a row with the AC output below 100 V | the outage's start |

How the rules decide:

- **An outage** opens on two readings in a row below 100 V on the AC input, and is timed from the first of them. A reading of 180 V or more closes it. A reading between the two (a brownout) neither opens nor closes one.
- **Flat** is 22.5 V on a 24 V battery and 45.5 V on a 48 V one, confirmed by the owner on 10 Nov 2026. The nominal voltage is read off the battery's own readings.
- **The three backup alarms share one episode and one key.** The episode ends when the grid is back. It also ends when the output works and the battery is 2.5 V clear of flat (5 V on 48 V).

**Decisions (10 Nov 2026):**

- **One clock.** The backup alarm runs on the outage's clock, not its own. Six hours into an outage, a battery that gives out goes at the next run. A battery that dies in a short outage is never mailed: a two-hour cut stays silent, whatever it did.
- **Every plant has grid**, unless `config/must_telemetry.json` lists it in `no_grid_expected`. A listed plant raises no grid-loss alarm. Its battery is still watched, on the backup episode's own clock. The list ships empty.
- **Admin only.**
- **Silence never confirms an outage.** A logger whose Wi-Fi dropped looks exactly like one whose grid is still off, so the grid alarm needs a fresh reading. Silence counts only straight after a flat-battery reading, because an inverter that cuts off takes its logger with it.
- **0 W is not a failure.** On three nights, 27–29 Oct, Jeremy's inverter showed 0 W for 7–11 hours, and the first design counted that as a dead backup. Its output held 229.8 V throughout: nothing was drawing on a working inverter. So the rule reads the output voltage, not the load.
- **The grid voltage, not the work state.** Kasun's and Muadh's inverters read OffGrid with 232–235 V on their input.

**What it would have sent.** The real code was replayed on the fleet's real readings from 27 Oct to 10 Nov 2026, one run an hour. It sent 28 mails on four plants:

| Plant | Mails | What |
|---|---|---|
| Ravi Botheju | 4 | Grid loss at 12:13, 16:13 and 20:13 on 8 Nov. "Battery flat at 22.2 V" at 04:13 on 9 Nov, half an hour before the inverter cut off |
| Jeremy | 4 | Grid loss on three nights, 27–29 Oct |
| Gayan-IMH | 3 | Grid loss since 23:27 on 5 Nov, still open on 10 Nov |
| SandamalSilva | 17 | Grid loss on most days. Until 29 Oct the grid went at about 10:00 each day; since then it has been gone for days at a time |

GitHub fires about one hourly run in three (#31). At that rate it sends 26 mails, and Ravi's backup mail comes at 05:13 from the shutdown rule instead ("no reading since 04:46").

The dead-output rule never fired. Only four readings in the 14 days showed a dead output, and never two in a row.

**The first run mails the two outages still open:** Gayan-IMH (since 5 Nov) and SandamalSilva (since 8 Nov).

**State:** `state/must_telemetry_state.json` is one flat map, like every state file here, so `merge_alarm_state.py` can union two runs.

- Alarm records live under `pid:pn:grid-loss@<start>` and `pid:pn:backup@<start>`, with the device-alarm fields.
- Each inverter's readings state lives under `device:pid:pn`.
- An alarm key lasts as long as its outage or episode, even while the alarm reads closed for a moment. A flicker therefore does not restart it at Send 1/3.
- An inverter that could not be read keeps its state and its keys.

**Found on its first CI run (10 Nov 2026): logins sent together are lost
together.** The first run read the accounts six at a time from a GitHub runner,
and 9 of the 23 logins went unanswered. Gayan-IMH and SandamalSilva were among
them, so the run reported no open outage while both were 46-105 hours into one.
UC3, which logs in one account at a time, lost 3 and 4 of 23 in the two runs
before it. Every account did log in from a workstation, six at a time. So the
fleet is now read three at a time, and every account that fails is tried once
more, one at a time, before the run gives up on it. The log says how many came
back on that second try.

**Found on the runs after it (10 Nov 2026): a first look that is cut short must
keep what it read.** Even with every login answered, the state file held 19 of
22 inverters and Gayan-IMH's and SandamalSilva's were never among them. They
were never read, not read randomly: an inverter with no grid for days is read
back until a reading shows grid, 15 or more calls for a five-day outage, and
one unanswered call threw all of them away, run after run. The second pass
repeated the same walk and lost it the same way. It was found by looking at
which devices the state file had a record for, not at the counts in the log.
A first look cut short now keeps the days it did read and times the outage from
the oldest of them, marked "or earlier". A read that has a cursor still fails
whole, so no reading is skipped.

**Public logs:** counts and alarm keys only, never names (#4). `--dry-run`
prints the email body, and is meant for a workstation.

**Not covered:**

- **An outage that also takes the logger's Wi-Fi down.** No readings arrive, so there is nothing to alarm on. UC14 reports the link down after 3 days.
- **DessMonitor plants (UC10).** Only the ShineMonitor accounts are read.

**Feature flags:** `emails.must_telemetry_admin` (the email), and
`alerts.six_hour_gate` (the gate itself, shared with UC3, UC18 and UC19).

**Scripts:**

| Script | Purpose |
|--------|---------|
| `check_must_telemetry.py` | Readings → outage and backup state → alarms → six-hour gate → 3x/4h → email |
| `config/must_telemetry.json` | The thresholds, and the plants built without grid |
| `six-hour-gate/config/alarm_triage.json` | "backup failed" and "battery flat" in the SOC class |

**CLI:**

```bash
py check_must_telemetry.py              # gate + email the admin + save state (cron entrypoint)
py check_must_telemetry.py --dry-run    # print what would be mailed; no email, no state
```

**Test Coverage:** 48/48 PASSED — `test_must_telemetry.py`

### UC22: SOLARMAN Alarms Through the Six-Hour Gate

The admin is mailed when an inverter on SOLARMAN Business has been in trouble
for **6 hours**:
- no grid, or a backup that failed in a cut;
- a lost battery;
- an output that is off;
- a house load that does not go through it;
- a fault;
- no data at all.

These are the four plants of the "Ktronics KH" organisation:

| Plant | Station | Inverter |
|---|---|---|
| Ktronics kelaniya | 64690226 | SRNE 3 kW (product `0_2421_1`) |
| Hasika Deye | 63371506 | Deye-type, single phase (`0_5407_1`) |
| Bandaragama | 63398238 | Deye-type, single phase (`0_5407_1`) |
| Mr. Dikkubura | 65430172 | 2 × Deye-type, three phase (`0_5411_1`) |

None of the alarms comes from SOLARMAN. They are made here from each inverter's
5-minute history, its live status and its silence. Then they go through the same
six-hour gate and three-sends / four-hours rule as UC3 and UC18–UC21.

**Implementation Status:** ✅ **IMPLEMENTED** (11 Nov 2026). It does nothing
until its sign-in is seeded (below).

**Why it exists:** these plants had no alarm path at all.
- None of them is in Deye Cloud, so UC19 does not see them.
- SOLARMAN's own Alerts tab returned nothing for any of them in Nov 2026. That
  includes Dikkubura's inverter 2408134651, which has sent nothing since 24 May.
- The readings carry no fault codes.

Meanwhile Kelaniya's battery cut out on most nights in September and October,
and its house sat on the manual changeover for weeks. Nobody was told.

**How it works:**

1. Runs **hourly at :53** ([trigger-solarman-alarms.yml](.github/workflows/trigger-solarman-alarms.yml)). It lists the organisation's plants, then for each plant its inverters and its open Alerts-tab items.
2. For each inverter with new data, it reads the day history (`/device-s/device/{id}/stats/day`).
   - It reads from the last reading it has already processed. On a first look it reads yesterday and today.
   - An inverter that has uploaded nothing new costs no history call.
   - Each type of inverter is mapped to its own series names (`profiles` in `config/solarman_alarms.json`): `srne`, `deye`, and `deye3`, which takes the lowest of the three phases.
3. For each inverter with fresh data it reads the live status (`/device-s/device/v3/detail`).
4. It keeps a small state per inverter: its outage and backup episode (UC20's own), its open episodes, and its fault status.
5. It hands the open alarms to `triage_alarms()`, then `filter_alarms_to_send()`, and emails the admin one list. Each alarm shows:
   - the plant and the inverter, what happened and since when;
   - the latest reading: grid, output, load, SoC, battery and DC bus, where the inverter reports them;
   - the hours on the clock, the class, send n/3, and the gate's first thing to check.

**The rules:**

| Alarm | Class | Opens | Closes | Clock |
|---|---|---|---|---|
| Grid loss | GRID | UC20's rule: two readings in a row under 100 V on the grid input, and a fresh reading (30 min) still shows no grid | one reading of 180 V or more | the outage's start |
| Backup failed / battery flat | SOC | UC20's rules, in a cut. The flat-battery checks need a battery voltage, which the SRNE history does not carry, so Kelaniya has the dead-output check only | the grid back, or the battery recharged | the outage's start |
| Battery connection open | HARDWARE | SRNE: SoC exactly 0 with the DC bus at 440 V or more, on two readings in a row. Deye-type: battery under 10 V, twice | SRNE: SoC above 0 and the bus under 430 V. Deye-type: battery 20 V or more | the first reading |
| Inverter output off | HARDWARE | output under 100 V on two readings in a row while the grid reads 180 V or more | output 180 V or more | the first reading |
| House load not on the inverter | LOAD | 25 W or less on two readings in a row, with the output alive (180 V or more) | one reading of 50 W or more, or the output dead | the first reading |
| Inverter fault | HARDWARE | a live status field that reads "fault" or "error" | the status normal again | first seen |
| No data | SILENT | the inverter's last upload is more than 30 minutes old | data again | the last upload; the outage's start if the last reading showed no grid |
| Alerts tab | by its text | any open alert | the alert closed | the alert's time |

How the rules decide:

- **Two readings in a row** open an episode, timed from the first of them. A reading that does neither keeps the episode as it was.
- **An episode found on a first look** may have begun earlier, so its message says "or earlier".
- **The SRNE's lost battery** reads like this: on 19 Sep the SoC fell 20 → 15 → 0 at 02:33 and the DC bus jumped from 366 V to 470 V. Both stayed there until the sun brought the battery back at 10:17. A healthy battery holds the bus at 366–416 V.
- **A house on the changeover** leaves the SRNE idling at 10–16 W, its own meter's noise. A house on the inverter never stays under 50 W for six hours: the fridge alone is more.
- **One episode keeps one key**, `station:sn:<kind>@<start>`. A flicker does not start it again at Send 1/3.
- **An alarm is claimed only from a fresh reading.** When an inverter goes quiet, its other alarms rest and the no-data alarm takes over.

**The sign-in.** SOLARMAN puts a Cloudflare captcha on password logins, so a
GitHub job cannot log in. Renewing a sign-in needs no captcha.

- The job's sign-in is a token pair kept in the `SOLARMAN_TOKENS_JSON` secret. SOLARMAN's access token lasts 60 days and its refresh token 180.
- **Renewing never outlives the browser login.** A renewal returns a new pair, but the new refresh token keeps the end date of the login it came from: on 11 Nov 2026 three renewals of a 9 Nov login all ended on 8 May 2027. So a browser login is needed every 180 days, for the job and for the workstation alike. 21 days before the end the admin is mailed (`solarman:signin:login-due`), with the date.
- When the access token has under 7 days left, the job renews the pair. It masks both new tokens in the log, then writes them back to the secret at once with `gh secret set`, reading them from stdin. The token it uses for that is `GH_PAT`, the same one the PH1000 loop saves its renewed BMS sign-in with.
- If a renewal is refused, if the write-back fails, if the sign-in ends within 21 days, or if no working sign-in is left, the admin is mailed **at once** (no six-hour wait). The mail goes at most 3 times, 4 hours apart. Its key is `solarman:signin:<kind>`, and it is forgotten when the sign-in works again.
- The token carries the account's full rights, Remote Control included. `solarman_cloud.py` is read-only by construction: it refuses any path outside five read paths before anything is sent, and nothing under `/order-s` is among them.

To seed or re-seed it, on a workstation:

1. Log in with the gitignored MCP helper, into a file of its own so the workstation's own session is not overwritten: `py solarman_mcp.py --login --token-file <file>`. A view-only member of the organisation is better than the main login: a token that cannot send Remote Control orders.
2. `gh secret set SOLARMAN_TOKENS_JSON < <file>`, then delete the file.
3. Nothing more to set up: the write-back uses the `GH_PAT` secret, which the PH1000 and PH1800 loops already need.
4. Check that the workstation's session still works (`py solarman_mcp.py --status`). Then dispatch the workflow once with `force_refresh` to prove the write-back: the secret's "updated" time changes, and the next run still signs in.

Two pairs renew side by side: renewing one leaves the other working (checked 11 Nov 2026). So step 1 can also be one renewal of the workstation's own session, which is how the job was first seeded on 11 Nov 2026. Its pair ends with the workstation's, on 8 May 2027.

**Decisions (11 Nov 2026):**

- **All four plants.**
- **Its own sign-in**: its own token pair, renewed by the job, not a copy of the workstation's file.
- **No data is mailed.** Other vendors' COMMS alarms are never mailed. Here silence gets its own class, SILENT, and is mailed, because on SOLARMAN silence is the only sign of an inverter that has died or been switched off. Its messages avoid every COMMS word, so the class cannot be lost to "offline".
- **Admin only.**
- **One grid rule for every vendor.** The grid and backup rules are UC20's code, with UC20's thresholds.
- **No `dry_run` input.** A dry run prints plant names, and this repository's logs are public.

**What it would have sent.** The real code was replayed on the plants' real
history from 1 Sep to 11 Nov 2026, one run an hour at :53. It sent **89 alarms
in 88 mails over 71 days**, about 1.2 a day:

| Plant | Mails | What |
|---|---|---|
| Ktronics kelaniya | 42 | The battery cut out 13 times, most of them in the small hours, 21 mails. The house load was not on the inverter in 8 episodes, 18 mails; the last has run since 27 Oct and is still open. The output was off from 23 to 27 Oct, 3 mails |
| Hasika Deye | 23 | No grid for 6 hours or more 8 times, 14 mails, the battery carrying the house throughout. Most began late morning or afternoon and ended late at night. No data for one to two days, three times, 9 mails |
| Mr. Dikkubura | 14 | The newer inverter read 0 V on all three grid phases for 6 days from 12 Oct and on three other days, 10 mails. No data once, 1 mail. The older inverter, silent since 24 May, 3 mails |
| Bandaragama | 10 | No data for 3 to 6 days, three times, 9 mails. A grid cut on 17 Oct, at the same time as Hasika's, 1 mail |

The gate held back 99 shorter episodes: 59 low-load dips, 19 grid cuts, 16
data gaps, 4 output stops and 1 battery cut-out.

**The first run mails two:** Dikkubura's older inverter (no data since 24 May)
and Kelaniya's house not on the inverter (open since 27 Oct; a first look reads back
only to the day before, so it says "or earlier").

**State:** `state/solarman_alarms_state.json` is one flat map, like every state file here, so `merge_alarm_state.py` can union two runs.

- Alarm records live under `station:sn:<kind>@<start>`, with the device-alarm fields. Alerts-tab items live under `station:station:alert-<id>`, so they are pruned even when SOLARMAN raised them on a logger.
- Each inverter's state lives under `device:station:sn`.
- A plant that could not be read keeps its state and its keys.

**Public logs:** counts and alarm keys only, never names (#4). `--dry-run`
prints the email body, and is meant for a workstation.

**Not covered:**

- **Fault codes.** SOLARMAN's readings carry none. The SRNE's `Flag Bit` is empty, and the Deye-types have no fault field.
- **A flat battery on the SRNE.** Its history has no battery voltage, so a flat battery in a cut shows only as a dead output, or as no data.
- **A fault shorter than an hour.** The live status is read once a run.
- **A lost phase on a three-phase plant** reads as grid loss: the lowest phase is the grid voltage.

**Feature flags:** `emails.solarman_alarms_admin` (the email), and
`alerts.six_hour_gate` (the gate itself, shared with UC3 and UC18–UC21).

**Scripts:**

| Script | Purpose |
|--------|---------|
| `check_solarman_alarms.py` | History → episodes → alarms → six-hour gate → 3x/4h → email; the sign-in's own alarm |
| `solarman_cloud.py` | Read-only SOLARMAN client: five read paths, one retry, renewal and write-back of the sign-in |
| `config/solarman_alarms.json` | Thresholds, series names per inverter type, product ids, rules off per plant |
| `six-hour-gate/config/alarm_triage.json` | The SILENT class, and UC22's words in LOAD and HARDWARE |
| `check_must_telemetry.py` | UC20's grid and backup rules, which now accept readings without a battery voltage |

**CLI:**

```bash
py check_solarman_alarms.py --tokens <file> --publish-secret   # gate + email + save state + write a renewal back (cron entrypoint)
py check_solarman_alarms.py --tokens <file> --dry-run          # workstation only: print what would be mailed; no email, no state
```

**Test Coverage:** 64/64 PASSED — `test_solarman_alarms.py`

---

## Bug Fixes

### A sign-in that could not renew itself (2026-11-11)

UC22 was built on the belief that each renewal of the SOLARMAN sign-in
returns a refresh token good for another 180 days, so the GitHub job would
keep itself signed in for ever. It does not: the new refresh token keeps the
end date of the browser login it came from. The code renewed whenever the
refresh token had under 21 days left, and a renewal cannot lift that. So from
about 17 Apr 2027 every hourly run would have renewed and rewritten the
`SOLARMAN_TOKENS_JSON` secret, and nothing would have warned anyone until
the sign-in ran out on 8 May 2027 and every SOLARMAN alarm stopped. Nothing
was lost, because it was caught before it ever ran. It was found on
2026-11-11 while seeding the job's sign-in, the day UC22 was pushed: three
renewals in a row all showed 178.1 days left. Now the job renews only for
the access token, and mails `solarman:signin:login-due` with the end date
21 days ahead. 2 tests: one replaces the test that expected the renewal,
and one is new.

### A green build mail with a red header (2026-11-10)

Since 2026-10-23 a push runs the tests only, and the mail it sends reads
`Status: TESTS PASSED` under the subject `Build OK`. The mail's header, a
red `Build Failed` banner, only looked for `Status: SUCCESS`, the word the
old full run used, so every push mail since has had a failure banner on a
passing build. It cost trust rather than data: a red banner on a good push is
what makes a real failure get skimmed past. It was found on 2026-11-10 from a
screenshot of the mail for 533c7661, whose own text said `TESTS PASSED`. The
header now follows the Status line: `SUCCESS` and `TESTS PASSED` are green,
`TESTS FAILING` and `FAILURE` are red (`email_utils.build_succeeded`). 4 tests
render the mail for each status.

### No alarm for a 25-hour MUST outage (2026-11-10)

**What it cost.** Ravi Botheju's MUST inverter lost the grid at 06:10 on 8 Nov
2026 and did not get it back until 07:40 the next morning. Overnight his 24 V
battery ran down to 21.9 V. The logger went silent at 04:46 and again at
06:08: the inverter was shutting itself off on a flat battery, and the house
was dark until the sun or the grid came back. The admin heard nothing.

**How it was found.** The owner asked whether an alarm had been registered,
then why the six-hour gate had not caught 25 hours. The Actions logs answered
it. Twelve alarm polls ran inside the outage, and eleven of them were told
"No device alarms found (err: 264)" for this account, including the two
during the shutdowns. The twelfth could not log in. Only 12 of the roughly 32
scheduled runs fired in those hours (#31), but more runs would have got the
same answer. The gate cannot hold what it is never given.

**Root cause.** ShineMonitor's warning list does not report a MUST inverter
losing the grid. Of the 157 alarms UC3 had tracked from it since January, not
one was a grid loss, while the inverters' own readings showed 32 grid-off
episodes in 14 days. It does sometimes report "battery voltage is too low" (58
times in that history), but not on this night, at 21.9 V. The gate's GRID class
had only ever been fed by Solis, Deye and DessMonitor.

**Fix.** UC20 makes the alarms from the readings. Replayed over this outage,
it mails grid loss at 12:13 on 8 Nov and a flat battery at 04:13 on 9 Nov,
half an hour before the first shutdown.

### Test-mode tests that tested a copy (2026-11-06)

**What it cost.** Nothing broke, but nothing guarded UC4 and UC5 either. The
five test-mode tests in `test_device_alarms.py` copied the test-mode branch of
`generate_device_alarms.py` into themselves and asserted on the copy. A change
that stopped the Gayan-IMH smoke test, or let test mode mail a real customer's
alarm, would have passed CI.

**How it was found.** An audit of use cases against test cases on 6 Nov 2026.
The copies disagreed with each other. Three said test mode bypasses the
ignored flag and the 3-send cap, which is what UC4 specifies and the script
does. Two said it respects both, which is the Session 12 fix that UC4 reversed
(*UC3: Test Mode 3-Send Limit Bug*, below). All five passed, because none of
them ran the script. The README repeated the contradiction: UC4 said test
mode bypasses the cap, the Session 12 entry said it respects it.

**The fix.** `TestTestModeRunsTheRealScript` replaces all five. Each test
writes alarm files and a state file, runs the real `main()` with the manual
run's arguments, and reads back the state and the email. Each was checked by
planting the regression it guards and watching it fail: the Session 12 rule
put back, the Gayan-IMH filter dropped, test mode running while real alarms
go out, and test mode honouring the 4-hour interval. The file still holds 40
tests.

### A password in public logs, and a vendor that stopped answering (2026-10-30)

**What it cost.** A customer account's ShineMonitor password sat in clear
text in 12 public workflow logs, from 19 to 29 October. Anyone who opened
one of those failed runs could read it. Separately, since 27 October about
11:00 UTC every scheduled run had missed device alarms for 9 to 16 of the 23
ShineMonitor accounts, and 30 of about 90 data-collection logins had failed,
while the `run` job still showed green.

**How it was found.** A "TESTS FAILED" email for the 29 Oct 22:55 run. The
failing test was `test_check_device_alarms_script`. The assertion message
under it printed the whole `bash -c` command the test had run, and the live
tests wrote the account's username, password and company key straight into
that command. GitHub did not mask it, because `SHINEMONITOR_CREDENTIALS_JSON`
is one secret holding the whole file and GitHub masks a secret only as a
whole. Scanning every failed run since August found the 12 logs. They were
deleted on 2026-10-30.

**Why the test failed.** Logging in worked, then `webQueryPlantsWarning` got
no answer inside curl's 25 s, so the script printed `err: )` with no code and
exited 1. Its sibling `test_query_plants_warning_api` made the same call 26 s
earlier and skipped, because it went through the vendor-silence helpers in
*The vendor's third mood* below. The end-to-end alarm test never did. That
was 4 of the 6 red builds on 28 and 29 Oct. The yearly fetch test caused a
fifth: the vendor refused the first accounts, the `grep` that filtered the
output found nothing, and that looks the same as a broken script. A
DessMonitor test hitting its 30 s budget caused the sixth.

**What the production logs showed.** A "no answer" was always a hang to
exactly 25.0 s. A good call took under 2 s. The hangs were spread over all
23 accounts, each of which worked in 2 to 7 of 8 runs, and they interleaved
with successes. Nothing in the repository changed on 27 October, so this
looks like ShineMonitor throttling GitHub's runners. The `authSource`
fallback did not rescue anything: it answers `err 8 ERR_FORBIDDEN` in under
a second.

**The fix.**

- The live tests in `test_shinemonitor_api.py` and `test_dessmonitor_api.py`
  pass credentials as `TEST_USERNAME`, `TEST_PASSWORD` and `TEST_COMPANY_KEY`
  in the environment. A failed assertion can still print the script, but the
  script no longer holds the secrets.
- `mask_credentials.sh` registers every password, secret and key in a
  credentials file with GitHub's log masker. All 14 places where a workflow
  writes such a file call it straight after the write, because a mask lasts
  only for the job that registered it.
- `alarms_fetched_or_skip()` skips the alarm test on `err: )`. A body with an
  error code (`err: 8`) still fails. `progress_or_skip()` reads the first 20
  lines of the monthly and yearly scripts instead of piping them through
  `grep`. `AUTH OK` or `DONE` passes, only `AUTH FAIL` skips, and anything
  else fails. The monthly test used to run the whole fleet, ran out of time
  and skipped in 18 of the 21 runs checked. It now reads 20 lines like the
  yearly one, so it actually tests something.
- `shinemonitor_auth_email` and `shinemonitor_api_call` try once more,
  signed afresh, 3 s after an empty answer. The first try gets 15 s instead
  of 25, so a hang the retry recovers costs about 19 s and one it does not
  costs 43 s, against 25 s and no data before. UC3 says this too.

`test_no_payloads_in_public_logs.py` fails if a test interpolates a password
into text, if a workflow writes a credentials file without masking it, or if
`mask_credentials.sh` misses a secret or masks a non-secret.
`TestRetryOnSilence` runs the real helpers against a stubbed `curl`, and it
fails against the old file. Each new guard was checked the same way, by
showing it fails on the code before the fix.

**Not fixed here.** The leaked password has not been changed yet; that needs
the customer. Whether the retry brings coverage back is for the next runs to
show: count `Failed to fetch alarms` and `Both authentication methods failed`
in the `run` job.

### The two-hour push (2026-10-23)

**What it cost.** Every push to `main` triggered four workflows as "build
verification": the ShineMonitor collection, the DessMonitor collection and both
weekly report workflows. The ShineMonitor one takes 80–90 minutes (21 plants,
fetched one after another) and its `run` job shares the `shinemonitor-commit`
concurrency slot with the scheduled runs, so a push first waited for whichever
scheduled run was in flight. Measured on 2026-10-21: the 16:19 push queued for
28 minutes, then ran for 89 — the "Build OK" email arrived at 18:17, two hours
after the push. Each push also produced a DessMonitor collection, two admin
weekly summaries and, through UC4's test mode, a device-alarm email to
Gayan-IMH. None of it told the developer anything the `tests` job had not said
in the first five minutes, and none of it did anything the next scheduled run
would not do.

**How it was found.** While working out whether the Valla home dashboard
should live in a repository of its own "to avoid the slowness", the run
history showed the slowness was not the number of workflows at all: the Solis
and DessMonitor jobs take seconds. It was one job, on one trigger.

**The fix.** Only `trigger-shinemonitor.yml` listens to `push` now, and its
`run` job carries `if: github.event_name != 'push'`. The "Build OK" email
moved into the `tests` job, which is the only thing a push runs. The three
other workflows lost their `push:` trigger. A manual run (`workflow_dispatch`)
behaves exactly as a push used to, so end-to-end verification is still one
click away — it is just no longer forced on every commit. UC2, UC4, UC8 and
UC9 above say "manual" where they used to say "push/manual".
`test_push_is_tests_only.py` fails if any of this is reverted.

**What it did not fix.** Scheduled delivery. The same history showed GitHub
creating about a third of the scheduled runs asked for, before and after the
2026-10-22 cron move — see *Schedule* above. A push being fast does not make
the crons fire.

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
zeros. The guard added here only caught all twelve at once, which is the next-but-one
entry below (*A failed call zeroed one month at a time*).

All four fetches now stage to a temporary file and `mv` it into place **only**
when rows were parsed; otherwise they keep what is committed and say
`WARNING: ... keeping existing`. The workflow's `grep` allow-list was widened
to let `WARNING` and `AUTH FAIL` through — it had been stripping both, which
is how this ran unseen.

`test_fetch_never_destroys_data.py` pins it, including a test that runs the
**old** idiom to prove it really does destroy the file. If that ever stops
being true the premise has changed and the guard can be revisited; until then
it is what makes the other test meaningful.

### Two plants, one file (2026-11-01)

A healthy plant was mailed DATA LINK DOWN **9 times between 20 and 31 October**.
The alert for `troy-troy-hybrid-3kw` said the last data was a month or two old
while the plant was producing every day. Its CSV on `main` looked fine by the time
anyone read it. Its git history did not: the month flipped between 31 real rows
and 31 rows of `0.0000`, run after run.

Troy's account holds two plants whose names sanitise to one filename:
`Troy Hybrid 3kw` (pid 1240025, last produced 2024-11) and `TROY HYBRID 3kw`
(pid 1282286, the live one). The dead plant is listed first and answers with a
full month of zeros. The live plant normally overwrites them. When the live
plant's daily call failed, the #33 guard "kept the existing file", which by then
held the dead plant's zeros. In the 2026-10-31 15:51 run the same plant's monthly
total came back as 341.1 kWh while its daily rows were being lost. Each restored
run clears the plant's state, so the 3-send rule never bit and no alert ever got
past `Send #2/3`.

A wholly zero month can no longer replace a file that holds real readings. The
dead plant stays on ShineMonitor, because it holds about 985 kWh of 2024 history
and removing it is not needed for the fix. No other account has a collision.

### A failed call zeroed one month at a time (2026-11-01)

Found while chasing the alert above: Troy's yearly row for September read 349.5,
then 0, then 349.5. `check_shinemonitor_yearly.sh` wrote `kwh=0` for any month
whose call failed, and the guard from *A fetch that destroyed the data it was
fetching* only catches all twelve months being zero. Since ShineMonitor began
hanging on GitHub runners, one hung call in a full run was enough.

On 2026-11-01 **54 months across 17 plants read 0 against a fresh vendor read:
9,132 kWh** (23 rows in 2026, 31 in 2025). All 6 customers with an email were
affected. The worst single totals were Chandika's 2025, short by 851 kWh, and
Namila-Waragoda's 2026, short by 706 kWh. The weekly report sums these rows, so
the "This Year" and "Last Year" totals customers receive depended on which calls
happened to hang.

A month's row now changes only when the vendor answers with a figure. A failed
call or an `err=12` keeps what is committed, 0 is written only when nothing is
held, and the log names any kept month in a `WARNING`. The damaged rows were
repaired from that vendor read in the same push.

**How the next one gets noticed.** Everything `test_fetch_never_destroys_data.py`
held at the time checked the shape of the code or a copy of its idiom, and all of
it stayed green throughout. It now also runs the **real** scripts against a stub
`curl` that answers from files, including the exact Troy run.
`test_committed_data_integrity.py` reads the real CSVs: each month is recorded by
two vendor calls, and it fails if one says the month produced and the other says
it did not. Months older than the daily files (before 2025-12) fail instead on a
zero between two producing months. The `tests` job runs on the schedule as well as on a push, so the
`🧪 TESTS FAILED` email arrives within hours, and it now names the failing tests.

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

A fifth leak, the credentials themselves, was found on 2026-10-30. A failing
live test printed the command it ran, and the command held a password. See
*A password in public logs, and a vendor that stopped answering* above. The
same file guards that one too.

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

**Reversed in Session 13.** UC4 made test mode skip both checks on purpose,
for the Gayan-IMH smoke test, so this fix no longer holds and a send count
above 3 on that plant is expected. The two tests that pinned this fix kept
passing until 2026-11-06 anyway; see *Test-mode tests that tested a copy*.

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

- **Weekly Reports**: Every Sunday at 04:19 UTC (DessMonitor 04:29)
  - Sends personalized reports to customers with email addresses
  - Admin receives summary on scheduled and manual runs (a push does not run it)
  - Customers receive reports only on scheduled runs

### Required Secrets

Configure these in GitHub Settings > Secrets:

- `SHINEMONITOR_CREDENTIALS_JSON`: ShineMonitor account credentials (also holds the `solis` block for UC13, UC17 and UC18, the `solax` block for UC21 (`client_id`, `client_secret`, `api_base`, `tz_offset_minutes`), and the `deye` block for UC19: `app_id`, `app_secret`, `email`, `password_sha256`, `company_id`, `data_center`)
- `DESSMONITOR_CREDENTIALS_JSON`: DessMonitor account credentials (UC10)
- `SOLARMAN_TOKENS_JSON`: UC22's SOLARMAN sign-in, `{"access_token", "refresh_token"}`, seeded from a browser login and renewed by the job itself (see UC22)
- `GH_PAT`: a GitHub token that can start workflows and write this repository's secrets. The PH1000 and PH1800 live loops use it to re-launch themselves (PH1000 also saves its renewed BMS sign-in with it), and UC22 writes its renewed sign-in back to `SOLARMAN_TOKENS_JSON` with it
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
│   ├── trigger-solis-alarms.yml       # SolisCloud alarms through the six-hour gate (hourly) - UC18
│   ├── trigger-deye-alarms.yml        # Deye Cloud alarms through the six-hour gate (hourly) - UC19
│   ├── trigger-solax-alarms.yml       # SolaX Cloud alarms through the six-hour gate (hourly) - UC21
│   ├── trigger-must-telemetry.yml     # MUST grid/battery alarms from telemetry (hourly) - UC20
│   ├── trigger-solarman-alarms.yml    # SOLARMAN alarms from readings, six-hour gate (hourly) - UC22
│   ├── trigger-battery-health.yml     # SolisCloud battery health (monthly) - UC17
│   ├── trigger-bms-ingest.yml         # ESP32 BMS reading ingest (on device push) - UC16
│   └── tests.yml                      # Full test suite, pull requests only - no UC
├── src/main/java/org/ktronics/
│   ├── config/
│   │   ├── credentials.json           # ShineMonitor credentials (gitignored)
│   │   ├── dessmonitor_credentials.json # DessMonitor credentials (gitignored)
│   │   ├── features.json              # Which emails and alerts are live
│   │   ├── must_telemetry.json        # UC20 thresholds and the plants built without grid
│   │   ├── solarman_alarms.json       # UC22 thresholds, series names, rules off per plant
│   │   └── excluded_plants.json       # Plants deliberately off the platform (UC14)
│   └── scripts/
│       ├── config.py                  # Centralized Python config
│       ├── common_config.sh           # Centralized Bash config
│       ├── shinemonitor_common.sh     # ShineMonitor API utilities
│       ├── dessmonitor_common.sh      # DessMonitor API utilities (UC10)
│       ├── mask_credentials.sh        # Mask every credential value in a job's logs; 16 workflows
│       ├── push_data_commit.sh        # Commit + push with retry/merge; used by 12 workflows (#28, #29)
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
│       ├── solis_common.py            # SolisCloud signed-request client (UC13, UC17, UC18)
│       ├── check_solis_switch.py      # SolisCloud ON/OFF watchdog (UC13)
│       ├── check_solis_alarms.py      # SolisCloud alarms through the six-hour gate (UC18)
│       ├── check_battery_health.py    # SolisCloud battery health report (UC17)
│       ├── deye_cloud.py              # Deye Cloud read-only client (UC19)
│       ├── check_deye_alarms.py       # Deye Cloud alarms through the six-hour gate (UC19)
│       ├── solax_cloud.py             # SolaX Cloud read-only client (UC21)
│       ├── check_solax_alarms.py      # SolaX Cloud alarms through the six-hour gate (UC21)
│       ├── check_must_telemetry.py    # MUST grid loss + backup failed from readings, six-hour gate (UC20)
│       ├── solarman_cloud.py          # SOLARMAN read-only client + sign-in renewal (UC22)
│       ├── check_solarman_alarms.py   # SOLARMAN alarms from readings, status, silence; six-hour gate (UC22)
│       ├── solis/README_SOLIS.md      # SolisCloud setup + Control API guide (UC13)
│       │
│       │   # Run by hand or on a workstation - no workflow calls these:
│       ├── check_plant_roi.py         # ROI verification (UC11)
│       ├── analyze_plant_roi.py       # ROI report generator (UC11)
│       ├── check_shinemonitor_daily.sh    # Today's energy per plant, printed
│       ├── check_shinemonitor_query-plants.sh  # Auth + raw plant list, for debugging
│       └── refresh_bms_creds.py       # Re-bootstrap BMS credentials (BlueStacks + frida)
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
    ├── solis_alarms_state.json        # SolisCloud alarms held by / sent through the gate (UC18)
    ├── deye_alarms_state.json         # Deye Cloud alarms held by / sent through the gate (UC19)
    ├── solax_alarms_state.json        # SolaX Cloud alarms held by / sent through the gate (UC21)
    ├── must_telemetry_state.json      # MUST outages, backup episodes and their alarms (UC20)
    ├── solarman_alarms_state.json     # SOLARMAN episodes and their alarms (UC22)
    └── solis_switch_state.json        # SolisCloud watchdog state (UC13)
```

`test_readme_tree.py` keeps this tree complete: a workflow or script that
reaches `main` without its line here fails the suite, and so does a line for
a file that is gone (#35).

## Testing

### Integration Test Suite

The project includes comprehensive integration tests covering all business logic.

#### Test Coverage: 1047 tests — 991 passed, 56 skipped on Windows (bash and API tests run in CI)

The MCP servers' tests are not counted here: they left the repo with the
servers on 2026-11-04 (see *Not part of the pipeline*). The 28 Tuya tests that
used to skip in CI went with them.

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

**SolisCloud (85 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC13 (Solis ON/OFF Watchdog) | 44 | `test_solis_switch.py` |
| UC17 (Solis battery health) | 10 | `test_battery_health.py` |
| UC18 (Solis alarms, six-hour gate) | 31 | `test_solis_alarms.py` |

**Deye Cloud (58 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC19 (Deye alarms, six-hour gate, read-only client) | 58 | `test_deye_alarms.py` |

**SolaX Cloud (28 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC21 (SolaX alarms, six-hour gate, daytime window, read-only client) | 28 | `test_solax_alarms.py` |

**MUST telemetry (48 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC20 (grid loss + backup failed from readings, six-hour gate, second pass, partial first look, no battery voltage) | 48 | `test_must_telemetry.py` |

**SOLARMAN (64 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC22 (read-only client, sign-in renewal and write-back, rules from readings, six-hour gate, wiring) | 64 | `test_solarman_alarms.py` |

**Connectivity, exclusions and flags (127 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC14 (Connectivity + Exclusions) | 62 | `test_connectivity.py` |
| Feature flags | 34 | `test_feature_flags.py` |
| Fleet benchmark | 28 | `test_fleet_benchmark.py` |
| UC14 (state retention) | 3 | `test_connectivity_state_retention.py` |

**Six-hour gate (26 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| Gate rules (R1-R6, classes) | 18 | `test_alarm_triage.py` |
| Gate wiring + feature mode | 8 | `test_alarm_gate_wiring.py` |

**Alerting discipline (41 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC1 (send cap + interval) | 4 | `test_admin_alert_send_cap.py` |
| Hourly poll scope | 19 | `test_hourly_poll_scope.py` |
| UC9 (weekly all-clear cadence) | 18 | `test_all_clear_cadence.py` |

**Fetch safety and data integrity (38 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| A fetch never destroys committed data | 26 | `test_fetch_never_destroys_data.py` |
| Committed data agrees with itself | 12 | `test_committed_data_integrity.py` |

The fake-vendor tests in the first file run the real fetch scripts, so they take
a couple of minutes under Git Bash on Windows and seconds in CI. The second file
reads the real CSVs on `main`: if it fails, data is damaged, so fix what wrote it
rather than editing the CSV.

**Public-log hygiene (10 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| No payloads or credentials in public logs | 10 | `test_no_payloads_in_public_logs.py` |

**Suite visibility (50 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| One bad import must not hide the suite | 50 | `test_suite_visibility.py` |

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

**Data-commit push (45 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| Retry, abort and conflict policy | 27 | `test_data_commit_push.py` |
| Cumulative state merge | 8 | `test_merge_alarm_state.py` |
| No bare push to main | 10 | `test_no_bare_push_to_main.py` |

**What a push runs (10 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| A push runs the test suite only, and its mail says so | 10 | `test_push_is_tests_only.py` |

**ShineMonitor (150 tests):**

| Suite | Tests | File |
| ----- | ----- | ---- |
| UC1 (Admin Alerts) | 15 | `test_admin_alerts.py` |
| UC2 (Customer Weekly) | 16 | `test_customer_weekly.py` |
| UC3-UC8 (Device Alarms) | 40 | `test_device_alarms.py` |
| UC9 (Email Optimization) | 9 | `test_uc9_admin_email.py` |
| UC11 (Plant ROI) | 19 | `test_uc11_plant_roi.py` |
| BVT (Centralized Config) | 14 | `test_centralized_config.py` |
| API Tests + vendor-condition handling + retry | 37 | `test_shinemonitor_api.py` |

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
