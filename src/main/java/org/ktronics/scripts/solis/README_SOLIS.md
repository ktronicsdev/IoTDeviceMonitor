# Solis Inverter Switch Watchdog

Detects when a **SolisCloud** inverter is switched **OFF** and (optionally) turns
it back **ON** automatically, emailing the admin. Runs every 2 hours via GitHub
Actions (`.github/workflows/trigger-solis-switch.yml`).

Built after **Ktronics Imbulgoda** latched OFF for **4 days** following repeated
`Uac-Unstable` grid faults and only recovered when manually powered on in the app.

> SolisCloud is a **separate platform** from ShineMonitor/DessMonitor. It does
> **not** use the web username/password — it uses an **API Key ID + Secret** with
> HMAC-SHA1 request signing.

## Files

| File | Purpose |
|------|---------|
| `solis_common.py` | SolisCloud signed-request client (`SolisClient`) |
| `check_solis_switch.py` | Watchdog: detect OFF → enable → email |
| `state/solis_switch_state.json` | Per-inverter state (last check, throttle timers) |
| `.github/workflows/trigger-solis-switch.yml` | 2-hourly schedule |
| `src/test/.../integration/test_solis_switch.py` | 31 tests (no network) |

## Setup

### 1. Get a SolisCloud API key
SolisCloud → **Service → API Management** → request/create an API key. You get a
**Key ID** and **Key Secret**. For automatic switch-on you also need the
**Control API** permission enabled (ask your Solis distributor — it's gated).

### 2. Fill in the `solis` block of `credentials.json`
```jsonc
"solis": {
  "api_base": "https://www.soliscloud.com:13333",
  "key_id": "<YOUR_KEY_ID>",
  "key_secret": "<YOUR_KEY_SECRET>",
  "admin_email": "ktronicssolar@gmail.com",
  "tz_offset_minutes": 330,          // Sri Lanka UTC+5:30
  "core_daylight_start_hour": 9,     // heuristic only acts 09:00–15:00 local
  "core_daylight_end_hour": 15,
  "onoff_cid": null,                 // set to enable AUTO switch-on (see step 4)
  "on_value": null,
  "off_value": null,
  "inverters": [
    { "label": "Ktronics Imbulgoda",
      "inverter_id": "1308675217949897483",
      "sn": "1031720254230285",
      "station_id": "1298491919450225535" }
  ]
}
```
The inverter is already pre-filled from the dashboard URL/alarm SN. Add more
inverters to the array as needed (`--list` prints ids/sn for the account).

### 3. Mirror it into the GitHub secret
The workflow rebuilds `credentials.json` from the **`SHINEMONITOR_CREDENTIALS_JSON`**
repo secret, so add the same `solis` block there. SMTP secrets
(`SMTP_USER`, `SMTP_PASS`, …) are already used by the other workflows.

### 4. (Optional) Enable automatic switch-on
Detection works **without** Control permission (daylight + zero-output heuristic),
but to let it actually flip the switch you must supply the on/off control register:

```bash
python check_solis_switch.py --discover 1308675217949897483
```
Find the on/off entry in the dump, then set `onoff_cid`, `on_value`, `off_value`
in `credentials.json`. Until these are set, the watchdog **detects** an OFF
inverter and **emails you to switch it on manually** — it never guesses a control
command (a wrong `cid` could change an unrelated setting).

## How detection works

1. **Authoritative** — if `onoff_cid` is set, read the on/off register via the
   Control API (`atRead`). `off_value` ⇒ OFF. Day/night independent.
2. **Heuristic** (default) — only during the core daylight window (09:00–15:00
   local), if telemetry is **fresh** and `pac == 0`, the inverter is producing
   nothing → treated as OFF. Outside daylight or on stale data it does nothing,
   so it won't false-trigger at night.

When OFF:
- Auto-control configured + permitted → send ON command, email *"auto re-enabled"*.
- Otherwise → email *"switch it on manually"*, throttled to once / 12 h.

## CLI

```bash
python check_solis_switch.py              # check + act + email (what the cron runs)
python check_solis_switch.py --dry-run    # detect + email, never send control
python check_solis_switch.py --status     # print live state, no action
python check_solis_switch.py --list       # list inverters on the account
python check_solis_switch.py --discover <INVERTER_ID>   # find the on/off cid
python check_solis_switch.py --detail     # per-MPPT DC volts/amps/watts
python check_solis_switch.py --detail --raw   # raw inverterDetail JSON
```

## Tests
```bash
py -m pytest src/test/java/org/ktronics/scripts/integration/test_solis_switch.py -q
# 40 passed
```

## Array health (`--detail`)

`--detail` prints live **per-MPPT** DC voltage, current and power, so you can tell
an array fault from bad weather without reading charts:

```
=== Surath 5KV ===
  DC            Volt     Curr     Power
  MPPT1       159.5V    10.1A     1611W
  MPPT2       160.2V     9.7A     1554W
  DC total                        3165W
  balance  : 4% current spread (balanced)
```

How to read it:

* **Current tracks irradiance; voltage barely moves.** Low current on *every*
  string with normal voltage = cloud, not a fault.
* **Balanced (<15% spread) = array is fine.** A string at zero or half the
  others is a blown DC fuse, a disconnected string, or shading on one roof face.
* **Volts far below the inverter's rated MPPT voltage** (330 V on the S6-EH1P5K)
  means short series strings — it works, but costs a little conversion
  efficiency and shortens the useful day at both ends.

It only needs the read API, not the Control permission, and works from an
`sn` alone — an inverter entry with a blank `inverter_id` is still queryable
with `--detail`, it is just skipped by the watchdog run.
