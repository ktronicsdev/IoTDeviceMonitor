# KT BMS Monitor — DIY Battery Monitoring Module

A self-built alternative to "Avrio Link": an **ESP32** reads a **JK-compatible BMS**
over **BLE** and pushes readings to the cloud using this repo's existing
**GitHub Actions + CSV + email-alert** pattern. No extra server to host.

```
  JK-BMS  --BLE-->  ESP32 (ESPHome)  --WiFi/HTTPS-->  GitHub API
                          |                                |
                          | live 10s data                  | repository_dispatch
                          v                                v
                  LAN web dashboard            .github/workflows/trigger-bms-ingest.yml
                  http://<device-ip>              -> append bms-module/data/<device>.csv
                                                  -> check thresholds -> email admin
```

## Status — PoC trial at gayan-imh

| | |
|---|---|
| **Trial site** | `gayan-imh` — Imbulgoda 3 kW, **JK BMS (BLE)** |
| **Device name / CSV key** | `kt-bms-gayan-imh` |
| **Install model** | Owner is abroad → a local technician flashes from a browser; no ESPHome on their machine ([INSTALL.md](INSTALL.md)) |
| **Roll-out** | Prove it at gayan-imh, then repeat per site — one build per site, `device_name` + `bms_mac` are the only things that change |

Nothing is deployed yet. The cloud half (workflow + ingest + alerts) is built and
tested; the hardware half is waiting on the on-site install.

## Why this design

- **ESP32** has both **BLE** (to the BMS) and **WiFi** (to the cloud) on one chip.
- **ESPHome** ships a community **JK-BMS BLE** component, so there's no C++ to write.
- The device can't be reached from GitHub (it's on the customer's LAN), so instead of
  an inbound server the device **pushes** to the GitHub API via `repository_dispatch`,
  which triggers a workflow — the same "process + email" flow already used for
  ShineMonitor/DessMonitor.
- **Live** 10-second data is served by ESPHome's built-in web dashboard on the LAN.
  GitHub gets a **periodic** reading (default every 15 min) for history + alerts.
- **No WiFi password is compiled in.** The installer enters the customer's WiFi on
  site, so one build works anywhere and no customer credential enters this repo.

## Hardware

| Item | Notes |
|------|-------|
| **ESP32-WROOM-32 dev board** | Has BLE + WiFi. ~1000–1500 LKR. (Avoid ESP32-**S2** — no Bluetooth.) |
| USB **data** cable | For the first flash only; later updates are OTA over WiFi |
| USB charger + socket | The device stays powered permanently |
| Mounting | Within a few metres of the battery (BLE range is the main constraint) |

## Deploying a site

### 1. Get the BMS BLE MAC from the site
The installer scans it with nRF Connect — Step 1 of [INSTALL.md](INSTALL.md).
It has to be known **before** the build, because it is compiled in.

### 2. Create secrets (once per builder machine)
```bash
cp firmware/secrets.example.yaml firmware/secrets.yaml   # then edit it
```
Fill in the hotspot password and a **GitHub fine-grained PAT** scoped to **only**
the `ktronicsdev/IoTDeviceMonitor` repo with **Contents: Read and write**.
Format: `Bearer github_pat_...`. `secrets.yaml` is gitignored.

### 3. Build the image
```bash
pip install esphome
scripts/build_firmware.sh kt-bms-gayan-imh C8:47:8C:AA:BB:CC
# -> bms-module/dist/kt-bms-gayan-imh-<date>.bin
```

> ⚠️ **This repo is public.** The `.bin` contains the GitHub token, so it must be
> built locally and sent to the installer **privately** — never committed, never
> attached to an issue/PR, and never produced as a GitHub Actions artifact (public
> repo artifacts are publicly downloadable). `dist/` is gitignored.
>
> The token also lives in device flash. Keep it **fine-grained** and limited to this
> one repo so a lost device can't do anything else. For a shipped product you'd put a
> tiny relay (e.g. a Cloudflare Worker) between the device and GitHub instead.

### 4. Flash + install on site
Hand the `.bin` and [INSTALL.md](INSTALL.md) to the technician. They flash at
**web.esphome.io** over USB, enter the WiFi, mount it near the battery, and confirm
live values on `http://<device-ip>`.

### 5. Repo secrets (already set)
The ingest workflow reuses the existing SMTP secrets: `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USER`, `SMTP_PASS` — already configured for the ShineMonitor workflows.

## Testing without hardware

Trigger the workflow manually with a fake reading:

**GitHub UI:** Actions → *BMS Reading Ingest* → *Run workflow* (edit the sample JSON).

**Simulate the device's real push** (what the ESP32 does):
```bash
gh api repos/ktronicsdev/IoTDeviceMonitor/dispatches -f event_type=bms_reading \
  -F 'client_payload[device]=kt-bms-test' -F 'client_payload[voltage]=52.4' \
  -F 'client_payload[soc]=78'
```

**Run the ingest locally** (no email is sent without SMTP env vars):
```bash
BMS_PAYLOAD='{"device":"kt-bms-test","voltage":47.0,"soc":9,"temp":55}' \
  python scripts/ingest_bms_reading.py     # `py` on Windows
```

## Files

| Path | Purpose |
|------|---------|
| [firmware/jk-bms-monitor.yaml](firmware/jk-bms-monitor.yaml) | ESPHome config (BLE, on-site WiFi provisioning, OTA, dashboard, cloud push) |
| [firmware/secrets.example.yaml](firmware/secrets.example.yaml) | Template for the hotspot password + GitHub token (copy to `secrets.yaml`) |
| [scripts/build_firmware.sh](scripts/build_firmware.sh) | Builds the per-site `.bin` the installer flashes |
| [scripts/ingest_bms_reading.py](scripts/ingest_bms_reading.py) | Appends CSV, checks thresholds, emails admin |
| [INSTALL.md](INSTALL.md) | Field guide for the on-site technician |
| [../.github/workflows/trigger-bms-ingest.yml](../.github/workflows/trigger-bms-ingest.yml) | `repository_dispatch` workflow |
| `data/<device>.csv` | Reading history (one file per device) |
| `state/bms_latest.json` | Latest snapshot per device |
| `state/bms_alerts_state.json` | Re-alert cooldown tracking |
| `dist/` | Built firmware images — gitignored, contains the token |

## Alert thresholds (defaults, override via workflow env)

| Env var | Default | Meaning |
|---------|---------|---------|
| `BMS_SOC_MIN` | 15 % | Low battery |
| `BMS_VOLT_MIN` / `BMS_VOLT_MAX` | 48 / 58.4 V | Pack under/over-voltage (tuned for 16S LFP) |
| `BMS_TEMP_MAX` | 50 °C | Over-temperature |
| `BMS_CURRENT_MAX` | 100 A | Over-current (absolute) |
| `BMS_DELTA_CELL_MAX` | 0.1 V | Cell imbalance |
| `BMS_REALERT_HOURS` | 4 h | Cooldown before re-alerting the same issue |

> Defaults assume a **16S LiFePO₄** pack (~51.2 V nominal). Confirm against the
> gayan-imh pack once the first readings land.
>
> An alert whose email **fails** to send does not start its cooldown — the run goes
> red and the alert is retried on the next reading, so an SMTP hiccup can't swallow it.

## Known gaps

- **No offline detection.** If the ESP32 or the site's WiFi dies, readings simply
  stop and nothing alerts. Needs a scheduled "last reading older than N hours" check.
- **No dashboard.** Data lands in CSV + email only; there's no `site/` page like
  PH1000/PH1800 have.
- **Untested against real hardware** — the BLE half has never run against a live JK BMS.

## Cost / scale notes

- BOM is ~$10–15 vs Avrio's 9000 LKR (~$30) — but Avrio includes warranty + polished app.
- Pushing every 15 min ≈ 96 workflow runs/device/day. Fine on a **public** repo (free
  Actions minutes); watch the quota if this repo ever goes private. Increase
  `push_interval` to reduce runs.
- Like the flyer, only the **latest** reading is kept as a snapshot; the CSV keeps history.
