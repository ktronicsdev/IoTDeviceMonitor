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
| **SOC** | ❌ not exposed correctly anywhere in cloud | needs BMS (see below) |

> The correct full dataset (true PV power, load power, SOC) only exists at the inverter's **RS485
> Modbus** port and the **BMS**, which a cloud GitHub Action cannot reach. See "Data sources".

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
| **Hystorix/PACEEX BMS** (BLE or PACEEX WiFi cloud) | accurate **SOC, battery V, charge/discharge A, cycles** | ❌ BLE = on-site; PACEEX cloud = undocumented API + login |

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
- Output JSON: `{ device, latest, series7d, fields_note, last_updated }`.
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

## Future: accurate SOC & full data

To get true SOC / load / PV (matching the SolarPowerMonitor PC tool), add an on-site reader:
- **Inverter**: ESP32/ESP8266 running esphome-must-inverter on the RS485 port, or a Pi polling the
  registers above.
- **BMS**: PACEEX/Hystorix BMS over BLE, or its WiFi-cloud account (undocumented API).
Push that data to the `ph1000-live` branch and the dashboard can consume it alongside the cloud feed.
