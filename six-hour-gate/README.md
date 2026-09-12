# Six-Hour Gate — alarm triage for the IoT platform

Decides which device alarms are worth a human's attention. One rule: an alarm has to
**still be wrong six hours later**. Everything the fleet shouts about in between is
recorded in state and never mailed.

```
  ShineMonitor          check_*_alarms.sh
  DessMonitor    -->    parse_alarm_files()   -->   [ SIX-HOUR GATE ]   -->  filter_alarms_to_send()
  SolisCloud                                         normalize()             (3x / 4h — unchanged)
  SolaX (gap)                                        open?      R3                  |
                                                     classify() R5 R6               v
                                                     dwell()    R1 R2         admin + customer mail
                                                          |
                                              held / suppressed
                                                          v
                                          state + suppressed_reason
                                                          |
                                                 re-tested each poll
```

The gate is a **pre-filter, not a rewrite**. The three-sends-then-ignore behaviour stays
exactly as it is — an alarm that never qualifies simply never spends one of its three sends.

## Status

| | |
|---|---|
| **Design** | Complete — [full design doc](https://claude.ai/code/artifact/378f7353-aa57-455e-aae0-a28f5030b484) |
| **Code** | Not written. Nothing in `src/` has been changed yet |
| **Config** | [`config/alarm_triage.json`](config/alarm_triage.json) — class map, dwell hours, matchers |
| **Blocking** | R3 (status filter) must ship before or with the gate — see below |
| **Uncovered** | SolaX — web login only, no API key |

## Why

Read from `state/device_alarms_state.json` + `state/dessmonitor_device_alarms_state.json`
(9 Sep 2026), across 25 accounts and 21 plants:

| | |
|---|---|
| **142** | alarms tracked |
| **105** | of them battery-voltage — 57 "too low", 26 "Low battery", 18 "too high", 4 charger-stops |
| **121** | already mailed 3× and permanently silenced, fixed or not |

A battery crossing a voltage threshold is what a battery on an off-grid or grid-scarce
system does **every night**. The five records saying *"Battery connection is open"* — a
genuinely dead pack — arrived in the same inbox, same format, same priority as the 57
saying the battery got low after dark.

SolisCloud says the same from the other side: 44 plants, 33 online, 4 in alarm — three of
those four are `1015 NO-Grid`, a CEB outage that clears itself before anyone reads the mail.

## The rules

| | Rule | Why it matters |
|---|---|---|
| **R1** | Dwell is `now − alarm_start` from the **vendor's** timestamp, not our `first_seen`. Absence from the feed = cleared | An alarm already 12 h old at first poll counts as 12 h old. `first_seen` is fallback only |
| **R2** | Convert `gts` from **plant-local** (UTC+5:30) before comparing to a UTC runner | A naive `datetime.now() - gts` under-reports age by exactly 5.5 h — the "6-hour" rule would silently behave as **11.5 h**, invisibly. Use `queryPlants -> address.timezone`, same as `check_ph1000.py:486` |
| **R3** | Count only alarms that are **actually open** — `status` false/0, Solis `state == 1` | **Ship first.** The fetch scripts are still in "TESTING MODE" (`date=`, no status filter), so *resolved* alarms come back. Bolting a dwell gate onto that feed turns every stale fixed alarm into a fresh call-out — worse than today |
| ~~**R4**~~ | ~~Offline-plant suppression + weekly offline digest~~ — **withdrawn 13 Sep 2026** | Offline is already detected downstream: a dark logger writes `0.0000` rows, so a sustained outage trips the **3-day production-loss RED alert** on the CSV side. The gate needs no digest and no offline-plant rule of its own. *`COMMS` alarms are still never mailed* — that lives in the class map, not as a rule. Number kept vacant so R5/R6 references stay stable |
| **R5** | `SOC` alarms are measured in **daylight hours** — must survive 09:00–15:00 local still open | 74% of volume. "Battery too low" at 22:00 clears at 07:00 = 9 h; a wall-clock gate would escalate it nightly on every plant forever. A healthy system recharges in that window; one that doesn't has a real fault |
| **R6** | Grid loss is on the clock like everything else — silent under 6 h | A 2–4 h CEB cut never reaches an inbox. What outlives 6 h is usually a tripped AC input breaker or blown input fuse — our call-out, not the utility's |

**R5 cheaper variant:** if the daylight clock is more machinery than you want in v1, set
`classes.SOC.gate` to `"clock"` with `dwell_hours: 14`. One config line, most of the same
benefit, at the cost of alerting on a genuinely bad December night.

## Feature flags

Every outbound email is gated by
[`config/features.json`](../src/main/java/org/ktronics/config/features.json), read by
`features.py` (Python), `feature_enabled()` in `common_config.sh` (bash), and a
`Load feature flags` step in each workflow. Turning a channel off needs a one-line
config change, not a code deletion.

**Current profile — critical only** (set 13 Sep 2026):

| Channel | State |
|---|---|
| `emails.weekly_customer_reports` | on |
| `emails.weekly_admin_summary` | **off** |
| `emails.device_alarms_admin` / `_customer` | on |
| `emails.production_alerts_admin` / `_customer` | on |
| `emails.build_verification`, `emails.test_regression` | on |
| `alerts.production_red_3day` | on |
| `alerts.production_orange_3month` | **off** |

Override for a single run without a commit — the env var is `KT_FEATURE_` plus the
dotted path, upper-cased:

```bash
# one-off: send this week's customer reports even though the flag is off
KT_FEATURE_EMAILS_WEEKLY_CUSTOMER_REPORTS=true \
  python3 src/main/java/org/ktronics/scripts/send_customer_emails.py --reports-dir reports

python3 src/main/java/org/ktronics/scripts/features.py     # print the live flag state
```

Both weekly workflows short-circuit at **job** level: with both weekly flags off they
no-op instead of generating reports nobody receives, so no CI minutes are spent.

Everything **fails open** — a missing or corrupt `features.json`, or an unknown flag
name, reads as *on*. A broken config must never silence alerting. Build-failure mail
additionally uses `!= 'false'` rather than `== 'true'`, so a crash before the flags
step still notifies.

### What this profile costs — reviewed 13 Sep 2026

Every flag here was checked against the use cases it overrides. Three were resolved,
one was accepted with eyes open, one remains.

| | Concern | Outcome |
|---|---|---|
| ✅ | Offline plants had no reporting channel with the digest off | **Resolved by removing R4.** A dark logger writes `0.0000` rows, so a sustained outage trips the 3-day production-loss alert. One alert path, not two |
| ✅ | UC2 (Customer Weekly Reports) was dark | **Re-enabled.** Customers get their Sunday summary again |
| ✅ | UC8 was dead logic; UC4 lost its canary | **Both restored by re-enabling UC2.** UC8's `event_name == 'schedule'` condition is live again, and because the job no longer short-circuits, report generation runs on every push — so a regression in `generate_weekly_report.py` still fails the build immediately |
| ⚠️ | ORANGE (3-month) is off | **Accepted.** See below |
| ⚠️ | `weekly_admin_summary` is off | No admin-side weekly fleet view. Not raised as a concern; flip the flag if you want it back |

**The accepted cost — ORANGE.** RED only fires below 20% of baseline for 3 consecutive
days: a plant that has essentially stopped. A plant sitting at 45% for three months —
soiling, a dead string, a derating inverter, a tree that grew — never trips RED. ORANGE
was the only rule catching quiet revenue loss, and per year it is plausibly worth more
than RED. It is off by decision, not by accident. `--orange` forces it on for a manual
run, and the UC1 tests pin it so the logic stays covered and green for whenever it
comes back.

## Class map

Six classes, three gates. Counts are today's state files. `COMMS` is the "ignore offline"
rule from the original brief: never mailed, never digested — if a logger stays dark it
shows up as a 3-day production-loss alert instead.

| Class | How our fleet words it | Gate | Today |
|---|---|---|---|
| `COMMS` | offline · datalogger lost · device disconnected | **never mail** | — |
| `SOC` | battery voltage too low (57) · Low battery (26) · too high (18) · charger stops, low battery (4) | **daylight** 09–15 | 105 |
| `HARDWARE` | bus voltage too low (7) · battery connection open (5) · bus soft start failed (3) · sensor error · voltage class error | **6 h clock** | 17 |
| `LOAD` | over current protection (9) · overload time out (2) · charger stops, over load (2) · overload | **6 h clock** | 14 |
| `THERMAL` | radiator over temperature · fan locked (on) · fan locked (off) | **6 h clock** | 3 |
| `GRID` | `1015 NO-Grid` (Solis) · grid under frequency · utility loss | **6 h clock** | 1 |
| `PV` | charger stops, high PV voltage | **6 h clock** | 1 |

Unrecognised messages fall to `UNKNOWN` and take the 6-hour clock — the safe default is to
let a novel fault through, then add it to the map. Every `UNKNOWN` is logged so the map
grows from real traffic, not guesswork.

## Vendor coverage

Each vendor gets a thin adapter producing the same record —
`{plant, device, vendor_code, message, class, started_at_utc, open, customer}` — and the
gate never learns a vendor's name.

| Inverter | Portal | Alarm source | Start time | Open flag | State |
|---|---|---|---|---|---|
| Voltronic / PowMr | ShineMonitor | `webQueryPlantsWarning` | `gts` · plant-local | `status = false` | built — add gate |
| Deye | DessMonitor | same shape, `dessmonitor-*` | `gts` · plant-local | `status = false` | built — add gate |
| Solis | SolisCloud | `POST /v1/api/alarmList` | `alarmBeginTime` · epoch ms | `state = 1` | adapter to write |
| SolaX | SolaX Cloud | — portal login only | — | — | **not covered** |

**Solis is cheap:** `solis_common.py` already signs and posts arbitrary resources, so
`alarmList` is a method plus a field map — and Ktronics AB holds distributor keys on that
account.

**SolaX is the weak link — say so out loud.** We have a web login and no API key, so SolaX
plants are currently unmonitored by anything automated. Note that even the public SolaX
Cloud token API returns `getRealtimeInfo` with an `inverterStatus` code and live values —
**not** an alarm list with start times. A token alone would not satisfy R1; we'd have to
derive alarms from `inverterStatus` transitions and time the dwell from our own
`first_seen`, which is the weaker fallback R1 allows. Until then SolaX stays on manual
portal review. Note there is no digest to flag them in — SolaX plants are not in `data/`
either, so the 3-day production-loss path does not cover them the way it covers offline
loggers elsewhere. **SolaX is genuinely unmonitored, and silence from it means nothing.**

## What changes

| File | Change |
|---|---|
| `six-hour-gate/alarm_triage.py` | **new** — `normalize()`, `classify()`, `is_actionable()`. Pure functions, no I/O |
| `six-hour-gate/config/alarm_triage.json` | **new** — dwell hours per class, COMMS list, daylight window |
| `generate_device_alarms.py` | one call inserted between `parse_alarm_files()` (L423) and `filter_alarms_to_send()` (L428). Rest untouched |
| `check_device_alarms.sh`<br>`check_dessmonitor_device_alarms.sh` | drop TESTING MODE, filter to unhandled (R3), and drop the raw-response `DEBUG` dump currently printing every alarm payload into public workflow logs |
| `state/*_device_alarms_state.json` | add `class`, `started_at_utc`, `suppressed_reason` — so *"why wasn't I told about this?"* is answerable from state alone |
| `trigger-shinemonitor.yml`<br>`trigger-dessmonitor.yml` | alarm poll to hourly; leave the CSV data pull at 4 h |

## Tests

The existing 35 UC3 tests must keep passing untouched — the gate is additive. Roughly ten
new ones, named for the rule they defend:

- **R1** — escalates at 6 h 01 m, silent at 5 h 59 m; an alarm absent from the feed clears
- **R2** — plant-local `gts` against a UTC runner yields the true age *(the regression that would otherwise hide for months)*
- **R3** — a handled alarm never escalates, however old
- **COMMS** — an offline/comms alarm is never mailed, at any age
- **R5** — an SOC alarm spanning 22:00→07:00 does not escalate; one spanning 09:00→15:00 does
- **R6** — a 3-hour grid cut is silent; a 7-hour one escalates

## The CSV side

Six hours doesn't map to a once-a-day CSV, but the same question does. `check_anomaly.py`
already has a persistence bar (RED = under 20% of baseline for 3 consecutive days); what's
missing is *is this actually ours?*

1. **Offline detection rides on the 3-day rule — deliberately.** A dark datalogger writes
   `0.0000` rows, which *is* the RED condition, so a logger that stays down for three days
   raises a production alert by itself. **This is the design, not a bug** (decision of
   13 Sep 2026, replacing R4): it means no separate offline monitor, no digest, and one
   alert path instead of two.

   The cost is that the CSV alone cannot tell *no data* from *no production* — a dead
   router and a dead inverter look identical. So the RED escalation text must say so:
   *"no data or no production for 3 days — check the datalogger is online before
   dispatching."* Without that line a technician drives out to a plant that was working
   fine. `--ignore-zero-months` still ignores plants dark for a full month, which is the
   right long-tail behaviour.
2. **Gate on the fleet, not the plant's own baseline.** A dull week drags every plant down
   together and should mail nobody. [`fleet_benchmark.py`](../src/main/java/org/ktronics/scripts/fleet_benchmark.py)
   already builds an irradiance proxy from the customer's own neighbours — wire it in so a
   RED alert mails only if the plant underperformed *the fleet*.
3. **Don't mail the same fault twice.** If a plant already has an escalated device alarm
   explaining the shortfall, the production alert becomes a line inside that email.

## Order of work

The first item is not optional and not reorderable.

1. **R3 — the status filter.** Stop pulling resolved alarms. Must land before or with the gate
2. **R2 + R1 — the gate itself.** Timezone conversion, six-hour dwell, and the `COMMS` never-mail class. The core commit
3. **R5 — the daylight clock for SOC.** The volume win: 105 of 141 alarms stop shouting nightly
4. **Hourly alarm poll.** Makes the six hours literal instead of six-to-ten
5. **Solis adapter.** `alarmList` against the existing signed client — brings 44 plants under the same rule
6. **CSV gating.** The fleet-benchmark gate, plus the "check the logger is online" line in the RED escalation text
7. **SolaX API token.** Request in parallel — a procurement wait, not engineering work

## Expected effect

**Today** — 142 alarms, each mailed three times regardless of merit; 121 already permanently
silenced whether or not the fault was ever fixed.

**After** — the 105 SOC alarms collapse to only those still open through midday; on a healthy
fleet, close to none. Comms faults never mail. The 36 hardware, load, thermal, grid and PV
alarms escalate on merit, six hours in, with a plant name and a first thing to check. On
Solis, four open alarms become three `NO-Grid` on a clock most CEB cuts never reach, plus one
battery alarm judged on whether it survives the sun.

The number that matters is not how many emails we send. It's that when one arrives, someone
gets in the van.
