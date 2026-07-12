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

### History depth — a real limitation
The multi-component breakdown (production/battery/consumption/grid) is **integrated from the
intraday series**, so it only covers the **published intraday days** (`--history-days`, default 7).
ShineMonitor's *historical* data (back to Jan) is **generation only** — no per-day usage/battery/
grid split. So you can have the full breakdown over a **short window** OR generation-only **back to
January**, not the full breakdown for the whole year. The backend also publishes
`plant.energy_series` (per-day/month/year generation from ShineMonitor, from Jan 1) — currently
unused by the UI, kept for a future generation-history view.

## Workflow
[`trigger-ph1800.yml`](../../.github/workflows/trigger-ph1800.yml) runs a cloud-only self-loop
(fetch → publish every ~10 min, re-launching itself; a 2 h cron restarts the chain). It writes each
account's `<hash>.json` to the `ph1800-live` branch and creates any missing plant page on `main` —
`ensure_page()` also **re-stamps** existing pages when `_app.html` changes, so a dashboard edit
propagates to every plant. No BMS; weather is per-plant. Nothing depends on a laptop.

## Usage
```bash
# One plant (bypasses the flag) — dump its live fields:
python check_ph1800.py --customer Gayan-IMH --discover
# All ph1800-flagged accounts -> per-account JSON:
python check_ph1800.py --out-dir publish --history-days 7
# Ensure page shells exist for all flagged plants:
python check_ph1800.py --pages-dir site/ph1800
```
