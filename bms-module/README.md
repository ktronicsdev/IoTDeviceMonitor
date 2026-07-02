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

## Why this design

- **ESP32** has both **BLE** (to the BMS) and **WiFi** (to the cloud) on one chip.
- **ESPHome** ships a community **JK-BMS BLE** component, so there's no C++ to write.
- The device can't be reached from GitHub (it's on the customer's LAN), so instead of
  an inbound server the device **pushes** to the GitHub API via `repository_dispatch`,
  which triggers a workflow — the same "process + email" flow you already use for
  ShineMonitor/DessMonitor.
- **Live** 10-second data is served by ESPHome's built-in web dashboard on the LAN.
  GitHub gets a **periodic** reading (default every 15 min) for history + alerts.

## Hardware

| Item | Notes |
|------|-------|
| **ESP32-WROOM-32** dev board | Has BLE + WiFi. ~1000–1500 LKR. (Avoid ESP32-**S2** — no Bluetooth.) |
| USB cable | For the first flash only; later updates are OTA over WiFi |
| Mounting | Within a few metres of the battery (BLE range is the main constraint) |

## One-time setup

### 1. Install ESPHome
```bash
pip install esphome
```

### 2. Find your JK-BMS BLE MAC address
Flash once with a placeholder, then check the logs for discovered BLE devices, or use a
phone BLE scanner app (nRF Connect). Put the MAC in `substitutions.bms_mac` in
[firmware/jk-bms-monitor.yaml](firmware/jk-bms-monitor.yaml).

### 3. Create secrets
```bash
cp firmware/secrets.example.yaml firmware/secrets.yaml   # then edit it
```
Fill in WiFi credentials and a **GitHub fine-grained PAT** scoped to **only** the
`ktronicsdev/IOT` repo with **Contents: Read and write**. Format: `Bearer github_pat_...`.
`secrets.yaml` is gitignored — it never gets committed.

> ⚠️ The token lives in device flash. Use a **fine-grained** token limited to this one
> repo so a lost device can't do anything else. For a shipped product you'd instead put a
> tiny relay (e.g. a Cloudflare Worker) between the device and GitHub so the token never
> leaves your server.

### 4. Flash
```bash
esphome run firmware/jk-bms-monitor.yaml       # first time over USB
```
After that, edits deploy over WiFi (OTA). If WiFi fails, the device raises a
**"KT-BMS Setup"** hotspot so anyone can enter WiFi from a phone — this is the
self-install "WiFi Setup Portal".

### 5. Add the GitHub secrets (once, in the repo)
The ingest workflow reuses the existing SMTP secrets: `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USER`, `SMTP_PASS`. These are already set for the ShineMonitor workflows.

## Testing without hardware

Trigger the workflow manually with a fake reading:

**GitHub UI:** Actions → *BMS Reading Ingest* → *Run workflow* (edit the sample JSON).

**Force an alert** by using a low SOC, e.g. `{"device":"kt-bms-test","voltage":47.0,"soc":9,"temp":55,...}`.

## Files

| Path | Purpose |
|------|---------|
| [firmware/jk-bms-monitor.yaml](firmware/jk-bms-monitor.yaml) | ESPHome config (BLE, WiFi, OTA, dashboard, cloud push) |
| [firmware/secrets.example.yaml](firmware/secrets.example.yaml) | Template for WiFi + GitHub token (copy to `secrets.yaml`) |
| [scripts/ingest_bms_reading.py](scripts/ingest_bms_reading.py) | Appends CSV, checks thresholds, emails admin |
| [../.github/workflows/trigger-bms-ingest.yml](../.github/workflows/trigger-bms-ingest.yml) | `repository_dispatch` workflow |
| `data/<device>.csv` | Reading history (one file per device) |
| `state/bms_latest.json` | Latest snapshot per device |
| `state/bms_alerts_state.json` | Re-alert cooldown tracking |

## Alert thresholds (defaults, override via workflow env)

| Env var | Default | Meaning |
|---------|---------|---------|
| `BMS_SOC_MIN` | 15 % | Low battery |
| `BMS_VOLT_MIN` / `BMS_VOLT_MAX` | 48 / 58.4 V | Pack under/over-voltage (tuned for 16S LFP) |
| `BMS_TEMP_MAX` | 50 °C | Over-temperature |
| `BMS_CURRENT_MAX` | 100 A | Over-current (absolute) |
| `BMS_DELTA_CELL_MAX` | 0.1 V | Cell imbalance |
| `BMS_REALERT_HOURS` | 4 h | Cooldown before re-alerting the same issue |

> Defaults assume a **16S LiFePO₄** pack (~51.2 V nominal). Adjust for your battery.

## Cost / scale notes

- BOM is ~$10–15 vs Avrio's 9000 LKR (~$30) — but Avrio includes warranty + polished app.
- Pushing every 15 min ≈ 96 workflow runs/device/day. Fine on a **public** repo (free
  Actions minutes); watch the quota if this repo ever goes private. Increase
  `push_interval` to reduce runs.
- Like the flyer, only the **latest** reading is kept as a snapshot; the CSV keeps history.
