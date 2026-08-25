# PH1800 — generic multi-plant ShineMonitor dashboard

A **generic** solar dashboard for **any** ShineMonitor inverter (unlike the bespoke
[`site/ph1000/`](../ph1000/) which is hand-tuned for Mifanza's broken devcode-697 unit). It is a
**pure pass-through of ShineMonitor** — every value is read straight from the cloud, nothing is
estimated. It supports **many plants in one codebase**, each with its **own URL and login**.

Backend: [`check_ph1800.py`](../../src/main/java/org/ktronics/scripts/check_ph1800.py) ·
Workflow: [`trigger-ph1800.yml`](../../.github/workflows/trigger-ph1800.yml) ·
Tests: [`test_ph1800.py`](../../src/test/java/org/ktronics/scripts/integration/test_ph1800.py)

## 🔗 Per-plant URLs
Each plant is served at its own path:
- **https://ktronicsdev.github.io/IoTDeviceMonitor/site/ph1800/Gayan-IMH/** — `pro` (grid-tie MUST)
- **https://ktronicsdev.github.io/IoTDeviceMonitor/site/ph1800/CRDesilva/** — `vhm` (off-grid PV-1800)

Every `site/ph1800/<label>/index.html` is an identical copy of the canonical dashboard
`site/ph1800/_app.html` (CSS + JS are inline; the app is plant-agnostic — login + data drive it).

## Add a plant = a flag
1. In `credentials.json`, flag the account and (optionally) its physical specs the cloud doesn't
   expose:
   ```json
   {
     "label": "CRDesilva",
     "username": "…", "password": "…",
     "ph1800": true,
     "variant": "vhm",   // inverter family — "pro" (default) or "vhm"; see Model variants below
     "pv_kw": 0.5,        // PV array size  (optional)
     "batt_kw": 1.8       // battery size   (optional)
   }
   ```
2. Push that same content into the cloud secret so the workflow sees it:
   `gh secret set SHINEMONITOR_CREDENTIALS_JSON < …/credentials.json`

That's it. The workflow auto-creates `site/ph1800/<label>/` (copied from `_app.html`) and publishes
the plant's data. No code changes.

## Model variants
Different MUST inverter families report **grid power (`PGrid`) with opposite signs**, so each plant
declares a `variant` (default `"pro"`); the dashboard's `gridImp()` canonicalises the sign so
import/export are labelled correctly everywhere (Live Grid card, flow arrow, and the History bars).

| `variant` | Inverter | Grid import | Grid export |
|---|---|---|---|
| `pro` (default) | grid-tie MUST (e.g. Gayan-IMH) | `PGrid < 0` | `PGrid > 0` |
| `vhm` | off-grid PV-1800 (e.g. CRDesilva) | `PGrid > 0` | never exports |

Everything else (PV / battery / load mapping) is identical across variants — only the grid sign
differs. Add a new family by extending the `variant` switch in `gridImp()` (`_app.html`).

## Login & data files (soft privacy)
- The login takes a **username + password**; the data file is
  **`sha256(username:password).json`** on the **`ph1800-live`** branch — so a plant's file isn't
  discoverable without its credentials. On success the app fetches only that file.
- ⚠️ **Caveat:** `ph1800-live` is a *public* branch, so the JSON is technically fetchable by anyone
  who has the credential-derived filename. This is a **soft gate** (same level as the PH1000
  password gate) — not server-grade auth. Real privacy would need a private authenticated backend.

## Field mapping (this inverter family → dashboard)
Read as-is from ShineMonitor; no calculation except battery power = V × I:

| Dashboard | ← ShineMonitor field |
|---|---|
| **PV Power** | Charger Power |
| **Battery Power** | Battery Voltage × Batt Current |
| **Battery Current** | Batt Current |
| **Load Power** | PLoad |
| **Grid Power** | PGrid — import = red, export = green (sign per **variant**, see above); % of rated |
| **Battery V / Grid V / Inverter V** | as-is |
| **Rated power** | ShineMonitor "rated power" |
| **Daily / Monthly / Yearly energy** | `queryPlantEnergyDay/Month/Year` |
| **Total energy** | Accumulated-PV lifetime counter |
| **PV array / Battery capacity** | `credentials.json` (`pv_kw` / `batt_kw`) |

Devcode is **auto-detected** per plant (`webQueryDeviceEs?pn=…`) — no per-device config.

## Dashboard (same layout as PH1000)
Flow Graph (with the real Grid path), Live cards, **Power Profile** (D = intraday power lines;
M/Y/T = stacked energy bars), **Generation & Usage History** (Production / Discharge / Consumption /
Charge / Grid import / Grid export), **Plant Profile**, **Plant Analysis** (add-parameter chart),
and **Weather** (per-plant Open-Meteo, reusing [`check_ph1000_weather.py`](../../src/main/java/org/ktronics/scripts/check_ph1000_weather.py)).
BMS is not shown (these inverters report no per-pack BMS).

### History depth
The multi-component breakdown (production/battery/consumption/grid) is **integrated from the
intraday series**. The dashboard shows a rolling **~30 days**: the last 7 come from the live feed
and days 8–30 from the hourly history job (below). ShineMonitor's data older than that is
**generation only** (no per-day usage/battery/grid split), so the full breakdown is a ~30-day
window, not the whole year.

## Workflows — TWO CIs, on purpose (read the incident below before changing this)
Two independent cloud workflows, so the heavy history fetch can never stall the live feed:

| Workflow | Cadence | Fetches | Publishes to | Concurrency |
|---|---|---|---|---|
| **`trigger-ph1800.yml`** (live) | every ~10 min (self-loop + 2 h cron) | **7 days** (fast) | `ph1800-live` | `ph1800-live`, `cancel-in-progress:false` |
| **`trigger-ph1800-hist.yml`** (history) | **hourly** (cron only) | **30 days** (slow) | `ph1800-hist` (only `*.hist.json`) | `ph1800-hist`, `cancel-in-progress:true` |

- The **live** job also creates any missing plant page on `main`; `ensure_page()` **re-stamps**
  existing pages when `_app.html` changes so a dashboard edit propagates to every plant.
- The **dashboard** loads live from `ph1800-live`, background-loads history from `ph1800-hist`, and
  merges them (recent days from the live feed override the same days in history).
- PH1000 mirrors this exactly: `trigger-ph1000.yml` (live) + `trigger-ph1000-hist.yml`
  (`ph1000-hist` branch).

## ⚠️ Incident & the two-CI rule (2026-08) — don't repeat this
**What happened:** history was originally fetched **on every live cycle** (`--history-days 30` in
`trigger-ph1800.yml`). It worked at 1–2 plants. As plants grew to ~5, each 30-day fetch (~3–5 min
*per plant*) made a single live iteration take ~20–25 min. Combined with the self-loop + 2 h cron +
manual dispatches all contending for the **one** `ph1800-live` concurrency slot, runs kept getting
**cancelled before the publish step** → every site drifted to **stale/partial data** (Mifanza went
"Offline", plants stuck days behind). No code was broken — it simply **stopped scaling**.

**The rule:** anything **slow or that scales with plant count** must NOT run on the fast live cycle.
Put it in a **separate, low-frequency workflow with its own concurrency group** (like the history
job). Adding a plant then only lengthens an **hourly** job, never the every-10-min live feed.

**Do NOT** re-add `--history-days 30` to `trigger-ph1800.yml`/`trigger-ph1000.yml` — that reverts
the trap. History belongs in the `*-hist` workflows.

## Usage
```bash
# One plant (bypasses the flag) — dump its live fields:
python check_ph1800.py --customer Gayan-IMH --discover
# LIVE feed: recent window only (what trigger-ph1800.yml runs — keep it small/fast):
python check_ph1800.py --out-dir publish --history-days 7
# HISTORY: 30-day fetch (trigger-ph1800-hist.yml runs this HOURLY; publishes only *.hist.json):
python check_ph1800.py --out-dir publish --history-days 30 --live-days 7
# Ensure page shells exist for all flagged plants:
python check_ph1800.py --pages-dir site/ph1800
```
`--history-days` = full window fetched (goes to `<hash>.hist.json`); `--live-days` = the small recent
window kept in the polled `<hash>.json`. Run the 30-day fetch only in the hourly history job.
