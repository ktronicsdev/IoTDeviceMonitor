# PH1000 Inverter Monitoring (`check_ph1000.py`)

Live + 7-day telemetry for the **MUST PH1000** (PV18/PH18-family) hybrid inverter on the
**Mifanza** account, feeding a GitHub Pages dashboard.

## 🔗 Live dashboard

**https://ktronicsdev.github.io/IoTDeviceMonitor/site/ph1000/**

- Served from `main` (Pages) at `site/ph1000/index.html`; auto-refreshes every 60 s.
- Live data published to the **`ph1000-live`** branch every ~5 min by
  [`trigger-ph1000.yml`](../../../../../../.github/workflows/trigger-ph1000.yml); the page
  fetches `ph1000_live.json` from that branch via `raw.githubusercontent.com`
  (so the main Pages site is untouched and no 5-min commits hit `main`).
- Freshness ≈ 5 min (workflow cadence) + GitHub raw CDN cache.

- Device: `pn=D70000210150180785`, `devcode=697`, `devaddr=4`, `sn=08B40001`, serial `060030222800001`
- Inverter family: **PH1000 / PH3000 / PH1800** (Modbus reg `20001`); MPPT charger: **PC1600**
- Battery: **Hystorix** lithium pack with a **PACEEX/Hystorix BMS**

---

## TL;DR — what's trustworthy

ShineMonitor's cloud telemetry for this inverter (devcode 697) is **largely mis-parsed**. Verified
live against the device and the SolarPowerMonitor PC tool:

| Field | Cloud status | Used as |
|---|---|---|
| Battery Voltage | ✅ correct | `battery_v` |
| Charger Current | ✅ correct (signed: + charge / − discharge) | `battery_a` (real battery current) |
| Charger Power | ✅ correct (`= Charger Current × Battery V`) | `battery_w` (real battery power) |
| PInverter | ✅ correct | `pinverter_w` |
| work state / online / last-update | ✅ correct | health badge |
| **Batt Current** | ❌ garbage (stuck at 100; real SOC was 37 %) | ignored |
| **PV Voltage / Inverter Voltage / Grid Voltage** | ❌ nonsense (0.2 / 2055 / 1259) | ignored |
| **PLoad** | ❌ 0 / broken | derived instead (see below) |
| **Accumulated PV / Load / Sell / Self-Use (kWh)** | ❌ frozen (0/0/2/0 all day) | ignored |
| **SOC** | ❌ not exposed correctly anywhere in cloud | ✅ now read **direct from the BMS** ([README_BMS.md](README_BMS.md)) |

> True PV power and load power still only exist at the inverter's **RS485 Modbus** port (a cloud
> Action can't reach it — see "Data sources"), so those stay estimated. **SOC is now live**: the
> Hystorix/PACEEX BMS cloud API was cracked ([README_BMS.md](README_BMS.md)) and feeds true
> per-pack SOC/V/A/cells via [`check_ph1000_bms.py`](check_ph1000_bms.py).

---

## Derived fields (energy balance — flagged `estimated`)

Because the cloud's own PV/PLoad columns are broken, PV and load are estimated from the two
reliable power fields (signed Charger/Battery Power: `+` charging, `−` discharging):

```
pv_power_w   = PInverter                               # PInverter = PV power (confirmed)
load_power_w = max(0, PInverter + ChargerPower - 40)   # ChargerPower<0=charging; 40W self-use
                                                       # e.g. 974 + (-542) - 40 = 392 W
```

Signed ChargerPower: `< 0` = charging (power into the battery), `> 0` = discharging.
- `PInverter > 0` (PV on): PV charges the battery and feeds the load →
  `Load = PV − charge = PInverter + ChargerPower` (974 + (−542) = 432).
- `PInverter = 0` (night): battery discharges to the load → `Load = ChargerPower`.

Rationale (confirmed against real rows):
- **PInverter = 0** → battery discharges to the house → `load ≈ |ChargerPower|`.
- **PInverter > 0** → PV powers the house, split between battery + load → `load = PInverter − ChargerPower`, `PV ≈ PInverter`.

| Real row (2026-06-27) | ChargerPower | PInverter | → PV est | → Load est |
|---|---|---|---|---|
| 12:41 (midday, discharging) | −1806 W | 2534 W | 2534 W | 4340 W |
| 16:11 (afternoon) | −707 W | 1008 W | 1008 W | 1715 W |
| 18:31 (evening, grid charge) | +388 W | 0 W | 0 W | 0 W |

These are **estimates**, not measured; the dashboard labels them as such. They ignore self-use /
conversion losses.

---

## Data sources (and why we use the cloud)

| Source | Gives | Reachable from GitHub Actions? |
|---|---|---|
| **ShineMonitor cloud** (`queryDeviceLastData`, `queryDeviceDataOneDayPaging`) | the reliable fields above (+ broken ones) | ✅ yes — **this module** |
| **Inverter RS485 Modbus** (protocol below) | true PV/load/grid/SOC/energies, all registers | ❌ needs on-site ESP/Pi (e.g. [esphome-must-inverter](https://github.com/vladyspavlov/esphome-must-inverter)) |
| **Hystorix/PACEEX BMS** (PACEEX WiFi cloud) | accurate **SOC, battery V, charge/discharge A, cells, cycles, temps** | ✅ **cracked & live** — [`check_ph1000_bms.py`](check_ph1000_bms.py), see [README_BMS.md](README_BMS.md) |
| **Open-Meteo** (open weather/solar API) | sky condition, cloud %, rain, ambient temp, **solar irradiance (GHI)** + harvest impact | ✅ **live, no API key** — [`check_ph1000_weather.py`](check_ph1000_weather.py), see "Weather" below |

Endpoints probed (Mifanza): `querySPDeviceLastData` returns almost nothing (PV voltage + work
state only); `queryDeviceCtrlField` is settings-only; `querySPDeviceParEs` / energy-flow actions
don't exist. There is **no raw Modbus-register read** via the public cloud API.

---

## Usage

```bash
# Discover device + dump raw endpoints/titles (locks the field map)
python check_ph1000.py --customer Mifanza --discover

# Fetch live snapshot + 7-day history -> dashboard JSON + monthly CSV
python check_ph1000.py --customer Mifanza --json-out data/ph1000_live.json --history-days 7
```

- Account opt-in: only `credentials.json` accounts flagged `"ph1000": true` (or `--customer`).
- Output JSON: `{ device, latest, series7d, plant, fields_note, last_updated }`; the workflow then
  merges in a `bms` block ([`check_ph1000_bms.py`](check_ph1000_bms.py)) and a `weather` block
  ([`check_ph1000_weather.py`](check_ph1000_weather.py)). `plant` includes `lat`/`lon` (from
  `queryPlantInfo`) so the weather fetcher can locate the site.
- Output CSV: `data/ph1000-<label>-<device>-<YYYY-MM>.csv` (one row per measured poll, deduped).
- The live source is `queryDeviceLastData` (the `SP` variant is empty for this device).

---

## MUST PH Modbus RTU reference (ID == 4)

From `PH PV RS485 Modbus RTU communication Protocol 1.4.3` (in `ph1000/`). The ShineMonitor
collector reads these but the cloud mis-labels them for devcode 697. Useful if a local RS485
gateway is added later.

**Inverter display (read-only):**

| Reg | Field | Unit |
|---|---|---|
| 25201 | work state (0 PowerOn,1 SelfTest,2 OffGrid,3 Grid-Tie,4 ByPass,5 Stop) | — |
| 25205 | Battery voltage | 0.1 V |
| 25206 | Inverter voltage | 0.1 V |
| 25207 | Grid voltage | 0.1 V |
| 25210 | Inverter current | 0.1 A |
| 25212 | Load current | 0.1 A |
| 25213 | **PInverter** | 1 W |
| 25214 | **PGrid** | 1 W |
| 25215 | **PLoad** (true load power) | 1 W |
| 25216 | Load percent | 0.01 |
| 25245/46 | Accumulated charger power (hi/lo) | 1000 / 0.1 kWh |
| 25253/54 | Accumulated load power (hi/lo) | 1000 / 0.1 kWh |
| 25255/56 | Accumulated self-use power (hi/lo) | 1000 / 0.1 kWh |

**Charger / MPPT display (read-only):**

| Reg | Field | Unit |
|---|---|---|
| 15205 | PV voltage | 0.1 V |
| 15206 | Battery voltage | 0.1 V |
| 15207 | Charger current | 0.1 A |
| 15208 | **Charger power** (PV→battery) | 1 W |
| 15217/18 | Accumulated PV power (hi/lo) | 1000 / 0.1 kWh |

> Note: in the ShineMonitor "Data Details" table the "Charger Power" column behaves as **signed
> battery power** (negative on discharge), not the unidirectional MPPT reg 15208 — another sign the
> cloud's column template for this devcode is mis-mapped.

---

## Battery BMS (live) — true SOC

The Hystorix/PACEEX BMS cloud API was reverse-engineered (full write-up in
[README_BMS.md](README_BMS.md)) and is now part of the pipeline:

- [`check_ph1000_bms.py`](check_ph1000_bms.py) reads both packs (**B1 master**, **B2 slave**) —
  true SOC, voltage, signed current (charge/discharge), SOH, remaining/full capacity, cycle count,
  and cell/temperature data.
- [`trigger-ph1000.yml`](../../../../../../.github/workflows/trigger-ph1000.yml) merges a `bms`
  block into `ph1000_live.json`; the dashboard shows a **Battery Profile** pane and sets
  **Live SOC = average(B1, B2)**.
- **Graceful fallback:** if the BMS session token (`BMS_IOT_TOKEN` secret) expires, the fetcher
  emits `ok:false` → the dashboard hides the Battery Profile pane and reverts the Live SOC to the
  voltage-based estimate (`socFromV`). No stale battery data is ever shown.

**Remaining work:** the BMS `iotToken` is currently a session secret. Fully-automated re-login is
blocked by the **native** pace-power request signature (`libpace.so`) — cracking that (the next
task) makes the token self-renewing. Until then, refresh with `gh secret set BMS_IOT_TOKEN`.

## Weather & solar-harvest impact (live)

[`check_ph1000_weather.py`](check_ph1000_weather.py) adds a `weather` block so the dashboard can
show the live sky and — the useful part — quantify how much the weather is *costing* the harvest.

- **Source: [Open-Meteo](https://open-meteo.com/en/docs)** — free, **no API key** (works straight
  from GitHub Actions), and one call returns both the weather AND the solar irradiance (GHI) that
  physically drives PV output. Variables used: `temperature_2m`, `cloud_cover`, `precipitation`,
  `weather_code` (WMO → condition label/category), `shortwave_radiation` (GHI), and
  `terrestrial_radiation` (the clear-sky reference). Past 7 days + today, in the plant's timezone.
- **Harvest impact (estimate, flagged as such):** clear-sky surface GHI ≈ `0.75 ×` extraterrestrial
  radiation (standard clear-sky transmittance). Per day,
  `harvest_factor = actual_GHI / clear_sky_GHI` (1.0 = clear, lower = cloud/rain) and
  `loss_pct = 100 × (1 − harvest_factor)` (~harvest lost to weather) — e.g. a thunderstorm day →
  "harvest ~51% of clear-sky (weather cost ~49%)".
- **Dashboard:** a dedicated **Weather** tab (current condition + 7-day harvest-impact strip), the
  **Ambient temperature** / **Solar irradiance** cards on Plant Profile, and an **irradiance
  overlay** on the Live tab's Power Profile chart (the "sunlight envelope" behind the PV curve, so
  weather-driven dips are visible directly).
- **Location:** auto-detected from `plant.lat`/`plant.lon` in `ph1000_live.json`; ShineMonitor does
  not expose coordinates for this plant, so it falls back to the **Mifanza site**
  (Plus Code `WVR5+RC9` Colombo → `6.94205, 79.85856`). Override with `--lat`/`--lon` or env
  `PH1000_LAT`/`PH1000_LON`.
- **Graceful fallback:** if Open-Meteo is unreachable the fetcher emits `ok:false` → the dashboard
  shows a placeholder in the Weather tab and the overlay/cards quietly skip. No stale data is shown.

```bash
# Fetch weather for the auto-detected (or default Mifanza) location
python check_ph1000_weather.py --live-json data/ph1000_live.json --out weather.json
python check_ph1000_weather.py --lat 6.94205 --lon 79.85856 --out weather.json   # explicit
```

## Future: accurate PV & load

True PV / load power (matching the SolarPowerMonitor PC tool) still needs an on-site **inverter**
RS485 reader (ESP32/ESP8266 running esphome-must-inverter, or a Pi polling the registers above);
push that to the `ph1000-live` branch and the dashboard can consume it alongside the cloud feed,
replacing the estimated PV/load.
