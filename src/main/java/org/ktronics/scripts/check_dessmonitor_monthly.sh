#!/usr/bin/env bash
set -euo pipefail

# UC10: Multi-Cloud Platform Support - DessMonitor Monthly Data Fetcher
# Fetches monthly energy data from DessMonitor API and saves to CSV format
# compatible with existing check_anomaly.py

# Source common configuration and functions
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common_config.sh"
source "$SCRIPT_DIR/dessmonitor_common.sh"

# 1st arg = credentials.json (optional, defaults to central config)
CREDS="${1:-$CREDENTIALS_FILE}"

# 2nd arg = month (optional), default = LAST MONTH (UTC)
MONTH="${2:-$(date -u -d "$(date -u +%Y-%m-01) -1 day" +%Y-%m)}"

# ---- Requirements ----
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1"; exit 2; }; }
need curl sha1sum awk sed grep tr date mkdir jq

# ---- Check for dessmonitor platform in credentials ----
# Support both old format (accounts array at root) and new format (platforms.dessmonitor)
if jq -e '.platforms.dessmonitor' "$CREDS" > /dev/null 2>&1; then
  # New multi-platform format
  PLATFORM_PATH=".platforms.dessmonitor"
  company_key="$(jq -r "${PLATFORM_PATH}.company_key // \"bnrl_frRFjEz8Mkn\"" "$CREDS")"
  echo "== DessMonitor MONTHLY CHECK (Multi-Platform Mode) =="
else
  # Check if this is a DessMonitor-only credentials file
  if jq -e '.dessmonitor_accounts' "$CREDS" > /dev/null 2>&1; then
    PLATFORM_PATH="."
    company_key="$(jq -r '.company_key // "bnrl_frRFjEz8Mkn"' "$CREDS")"
    echo "== DessMonitor MONTHLY CHECK (DessMonitor-Only Mode) =="
  else
    echo "ERROR: No DessMonitor configuration found in credentials"
    echo "Expected: .platforms.dessmonitor or .dessmonitor_accounts"
    exit 2
  fi
fi

echo "Config : $CREDS"
echo "Month  : $MONTH"
echo "Output : data/dessmonitor-<label>-<plantId>-${MONTH}.csv"
echo "----------------------------------------------"

mkdir -p data

# ---- Extract accounts based on format ----
if [[ "$PLATFORM_PATH" == ".platforms.dessmonitor" ]]; then
  ACCOUNTS_JSON=$(jq -c "${PLATFORM_PATH}.accounts[]" "$CREDS" 2>/dev/null || echo "")
else
  ACCOUNTS_JSON=$(jq -c '.dessmonitor_accounts[]' "$CREDS" 2>/dev/null || echo "")
fi

if [[ -z "$ACCOUNTS_JSON" ]]; then
  echo "ERROR: No DessMonitor accounts found"
  exit 2
fi

# -------------------------------------------------------------------
# PROCESS EACH ACCOUNT
# -------------------------------------------------------------------
echo "$ACCOUNTS_JSON" | while read -r acc; do
  label="$(echo "$acc" | jq -r '.label // empty')"
  username="$(echo "$acc" | jq -r '.username // empty')"
  password="$(echo "$acc" | jq -r '.password // empty')"

  [[ -z "$username" || -z "$password" ]] && continue
  [[ -z "$label" ]] && label="$username"

  echo
  echo "Account: $label"

  # ---- AUTH using shared function (dual fallback) ----
  if ! dessmonitor_authenticate "$username" "$password" "$company_key"; then
    echo "  AUTH FAIL"
    continue
  fi

  echo "  AUTH OK"

  # -----------------------------------------------------------------
  # QUERY PLANTS
  # -----------------------------------------------------------------
  plants_resp="$(dessmonitor_api_call "queryPlants" "page=0&pagesize=50" || true)"

  # DEBUG: Show raw plants response
  echo "  DEBUG queryPlants response: $(echo "$plants_resp" | head -c 500)"

  if [[ -z "$plants_resp" ]] || ! printf "%s" "$plants_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
    echo "  PLANTS FAIL"
    echo "  Response: $(echo "$plants_resp" | head -c 200)..."
    continue
  fi

  # Extract plant IDs and names using jq
  # Try both .dat[] (array) and .dat.plant[] (nested) formats
  PLANT_DATA=$(echo "$plants_resp" | jq -r '.dat[]? | "\(.pid)|\(.name // "unknown")"' 2>/dev/null || true)

  # If empty, try alternate format .dat.plant[]
  if [[ -z "$PLANT_DATA" ]]; then
    PLANT_DATA=$(echo "$plants_resp" | jq -r '.dat.plant[]? | "\(.pid // .id)|\(.name // .pname // "unknown")"' 2>/dev/null || true)
  fi

  if [[ -z "$PLANT_DATA" ]]; then
    echo "  No plants found."
    echo "  DEBUG: dat content: $(echo "$plants_resp" | jq '.dat' 2>/dev/null || echo 'parse error')"
    continue
  fi

  # -----------------------------------------------------------------
  # PROCESS EACH PLANT/COLLECTOR
  # -----------------------------------------------------------------
  echo "$PLANT_DATA" | while read -r plant_line; do
    [[ -z "$plant_line" ]] && continue

    pid="${plant_line%%|*}"
    pname="${plant_line#*|}"
    [[ -z "$pid" ]] && continue

    echo "    Plant: ${pname:-?} (pid=$pid)"

    # ---- QUERY DEVICES for this plant/collector ----
    # CRITICAL: Use sn=${pid} to query devices (NOT pn=${pid})
    # Local testing confirmed: pn=${pid} returns ERR_NOT_FOUND_DEVICE, sn=${pid} works
    devices_resp="$(dessmonitor_api_call "webQueryDeviceEs" "sn=${pid}" || true)"

    echo "      DEBUG webQueryDeviceEs response: $(echo "$devices_resp" | head -c 500)"

    # Extract device info: pn, sn, devcode, devaddr
    # CRITICAL: pn comes from device response, NOT from queryPlants pid
    # Try multiple JSON structures for device list
    DEVICE_DATA=$(echo "$devices_resp" | jq -r '.dat.device[]? | "\(.pn)|\(.sn)|\(.devcode)|\(.devaddr)"' 2>/dev/null || true)

    # If empty, try alternate format .dat[]
    if [[ -z "$DEVICE_DATA" ]]; then
      DEVICE_DATA=$(echo "$devices_resp" | jq -r '.dat[]? | "\(.pn)|\(.sn)|\(.devcode // 2500)|\(.devaddr // 1)"' 2>/dev/null || true)
    fi

    # If still no devices, skip this plant (can't get energy without device info)
    if [[ -z "$DEVICE_DATA" ]]; then
      echo "      No devices found for this plant, skipping"
      continue
    fi

    # Sanitize names for filename
    safe_name="$(printf "%s" "$pname" \
      | tr '[:upper:]' '[:lower:]' \
      | sed 's/[^a-z0-9]/-/g' \
      | sed 's/--*/-/g' \
      | sed 's/^-//;s/-$//')"

    safe_label="$(printf "%s" "$label" \
      | tr '[:upper:]' '[:lower:]' \
      | sed 's/[^a-z0-9]/-/g' \
      | sed 's/--*/-/g' \
      | sed 's/^-//;s/-$//')"

    # Initialize CSV file for this plant
    out="data/dessmonitor-${safe_label}-${safe_name}-${MONTH}.csv"
    : > "$out"
    echo "date,kwh" >> "$out"

    # Accumulate daily totals across all devices
    declare -A daily_totals

    # ---- PROCESS EACH DEVICE ----
    echo "$DEVICE_DATA" | while read -r device_line; do
      [[ -z "$device_line" ]] && continue

      # Parse 4 fields: pn|sn|devcode|devaddr
      # CRITICAL: pn comes from device response (e.g., "D70000210187967959"), NOT from queryPlants pid
      device_pn="${device_line%%|*}"
      rest="${device_line#*|}"
      device_sn="${rest%%|*}"
      rest="${rest#*|}"
      devcode="${rest%%|*}"
      devaddr="${rest#*|}"

      echo "      Device: pn=$device_pn sn=$device_sn devcode=$devcode devaddr=$devaddr"

      # ---- QUERY DEVICE ENERGY using correct endpoint ----
      # CRITICAL: Use ENERGY_TODAY parameter (local testing confirmed it returns real data)
      # ENERGY_TODAY_FROM_GRID returns 0.0000 for all values
      d_resp="$(dessmonitor_api_call "querySPDeviceKeyParameterMonthPerDay" \
        "pn=${device_pn}&sn=${device_sn}&devcode=${devcode}&devaddr=${devaddr}&parameter=ENERGY_TODAY&date=${MONTH}&i18n=en_US&chartStatus=false" || true)"

      echo "        DEBUG querySPDeviceKeyParameterMonthPerDay response: $(echo "$d_resp" | head -c 500)"

      # Check if API call succeeded
      if [[ -z "$d_resp" ]] || ! printf "%s" "$d_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
        echo "        Device energy query failed"
        continue
      fi

      # Extract day rows: val + gts from response
      # CRITICAL: Response format is {"dat":{"option":[{"gts":"2026-01-01","val":"8.6120"},...]}}
      # - Data is in "option" array (NOT "perday")
      # - Date field is "gts" (NOT "ts")
      mapfile -t ROWS < <(
        printf "%s" "$d_resp" | tr -d '\r\n' |
        awk '
          {
            s=$0
            while (1) {
              gts=""
              val=""

              # Match gts field: "gts":"2026-01-01"
              if (match(s, /"gts"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
                gts_part = substr(s, RSTART, RLENGTH)
                gts = gts_part
                sub(/.*:"/, "", gts); sub(/"$/, "", gts)
                rest = substr(s, RSTART+RLENGTH)
              }
              else {
                break
              }

              # Match val field: "val":"8.6120"
              if (match(rest, /"val"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
                val_part = substr(rest, RSTART, RLENGTH)
                val = val_part
                sub(/.*:"/, "", val); sub(/"$/, "", val)
                s = substr(rest, RSTART+RLENGTH)
              }
              # Try unquoted format: "val":8.6120
              else if (match(rest, /"val"[[:space:]]*:[[:space:]]*[0-9.]+/)) {
                val_part = substr(rest, RSTART, RLENGTH)
                val = val_part
                sub(/.*:/, "", val)
                s = substr(rest, RSTART+RLENGTH)
              }
              else {
                break
              }

              # Only output non-zero values
              if (val != "0.0000" && val != "0") {
                print gts "," val
              }
            }
          }
        '
      )

      if [[ "${#ROWS[@]}" -eq 0 ]]; then
        echo "        (No non-zero daily values)"
      else
        printf "%s\n" "${ROWS[@]}" >> "$out"
        echo "        Got ${#ROWS[@]} non-zero daily values"
      fi
    done  # devices

    # Count rows written (excluding header)
    row_count=$(($(wc -l < "$out") - 1))
    if [[ "$row_count" -gt 0 ]]; then
      echo "      Daily CSV written: $out ($row_count days)"
    else
      echo "      (No data written to CSV)"
    fi
  done  # plants
done  # accounts

echo
echo "DONE."
