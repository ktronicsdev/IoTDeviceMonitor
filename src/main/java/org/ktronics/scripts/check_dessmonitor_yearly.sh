#!/usr/bin/env bash
set -euo pipefail

# UC10: Multi-Cloud Platform Support - DessMonitor Yearly Data Fetcher
# Fetches yearly energy data from DessMonitor API
# Uses querySPDeviceKeyParameterYearPerMonth with ENERGY_TOTAL parameter
# (Confirmed from web portal browser DevTools - returns monthly totals for the year)

# Source common configuration and functions
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common_config.sh"
source "$SCRIPT_DIR/dessmonitor_common.sh"

# 1st arg = credentials.json (optional, defaults to central config)
CREDS="${1:-$CREDENTIALS_FILE}"

# 2nd arg = year (optional), default = LAST YEAR (UTC)
YEAR="${2:-$(date -u -d "$(date -u +%Y-01-01) -1 day" +%Y)}"

# ---- Requirements ----
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1"; exit 2; }; }
need curl sha1sum awk sed grep tr date mkdir jq

# ---- Check for dessmonitor platform in credentials ----
# Support both old format (accounts array at root) and new format (platforms.dessmonitor)
if jq -e '.platforms.dessmonitor' "$CREDS" > /dev/null 2>&1; then
  # New multi-platform format
  PLATFORM_PATH=".platforms.dessmonitor"
  company_key="$(jq -r "${PLATFORM_PATH}.company_key // \"bnrl_frRFjEz8Mkn\"" "$CREDS")"
  echo "== DessMonitor YEARLY CHECK (Multi-Platform Mode) =="
else
  # Check if this is a DessMonitor-only credentials file
  if jq -e '.dessmonitor_accounts' "$CREDS" > /dev/null 2>&1; then
    PLATFORM_PATH="."
    company_key="$(jq -r '.company_key // "bnrl_frRFjEz8Mkn"' "$CREDS")"
    echo "== DessMonitor YEARLY CHECK (DessMonitor-Only Mode) =="
  else
    echo "ERROR: No DessMonitor configuration found in credentials"
    echo "Expected: .platforms.dessmonitor or .dessmonitor_accounts"
    exit 2
  fi
fi

echo "Config : $CREDS"
echo "Year   : $YEAR"
echo "Output : data/dessmonitor-<label>-<plantId>-${YEAR}.csv"
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

  if [[ -z "$plants_resp" ]] || ! printf "%s" "$plants_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
    echo "  PLANTS FAIL"
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
    devices_resp="$(dessmonitor_api_call "webQueryDeviceEs" "sn=${pid}" || true)"

    echo "      DEBUG webQueryDeviceEs response: $(echo "$devices_resp" | head -c 300)"

    # Extract device info: pn, sn, devcode, devaddr
    # CRITICAL: pn comes from device response (e.g., "D70000210187967959"), NOT from queryPlants pid
    DEVICE_DATA=$(echo "$devices_resp" | jq -r '.dat.device[]? | "\(.pn)|\(.sn)|\(.devcode)|\(.devaddr)"' 2>/dev/null || true)

    # If empty, try alternate format .dat[]
    if [[ -z "$DEVICE_DATA" ]]; then
      DEVICE_DATA=$(echo "$devices_resp" | jq -r '.dat[]? | "\(.pn)|\(.sn)|\(.devcode // 2500)|\(.devaddr // 1)"' 2>/dev/null || true)
    fi

    # If still no devices, skip this plant
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

    # Initialize CSV file for this plant's yearly data
    out="data/dessmonitor-${safe_label}-${safe_name}-${YEAR}.csv"
    : > "$out"
    echo "month,kwh" >> "$out"

    # ---- PROCESS EACH DEVICE ----
    # Use querySPDeviceKeyParameterYearPerMonth to get all 12 months in one API call
    # (Confirmed from web portal browser DevTools)
    echo "$DEVICE_DATA" | while read -r device_line; do
      [[ -z "$device_line" ]] && continue

      # Parse 4 fields: pn|sn|devcode|devaddr
      device_pn="${device_line%%|*}"
      rest="${device_line#*|}"
      device_sn="${rest%%|*}"
      rest="${rest#*|}"
      devcode="${rest%%|*}"
      devaddr="${rest#*|}"

      echo "      Device: pn=$device_pn sn=$device_sn devcode=$devcode devaddr=$devaddr"

      # ---- QUERY YEARLY ENERGY DATA ----
      # CRITICAL: Use querySPDeviceKeyParameterYearPerMonth with ENERGY_TOTAL
      # (Confirmed from web portal - returns monthly totals for the entire year)
      d_resp="$(dessmonitor_api_call "querySPDeviceKeyParameterYearPerMonth" \
        "pn=${device_pn}&sn=${device_sn}&devcode=${devcode}&devaddr=${devaddr}&parameter=ENERGY_TOTAL&date=${YEAR}&i18n=en_US&chartStatus=false" || true)"

      echo "        DEBUG querySPDeviceKeyParameterYearPerMonth response: $(echo "$d_resp" | head -c 500)"

      # Check if API call succeeded
      if [[ -z "$d_resp" ]] || ! printf "%s" "$d_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
        echo "        Yearly energy query failed"
        continue
      fi

      # Extract month rows: gts (YYYY-MM) and val from response
      # Response format: {"dat":{"option":[{"gts":"2026-01","val":"150.5"},{"gts":"2026-02","val":"140.2"},...]}}
      mapfile -t ROWS < <(
        printf "%s" "$d_resp" | tr -d '\r\n' |
        awk '
          {
            s=$0
            while (1) {
              gts=""
              val=""

              # Match gts field: "gts":"2026-01"
              if (match(s, /"gts"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
                gts_part = substr(s, RSTART, RLENGTH)
                gts = gts_part
                sub(/.*:"/, "", gts); sub(/"$/, "", gts)
                rest = substr(s, RSTART+RLENGTH)
              }
              else {
                break
              }

              # Match val field: "val":"150.5" or "val":150.5
              if (match(rest, /"val"[[:space:]]*:[[:space:]]*"?[0-9.]+/)) {
                val_part = substr(rest, RSTART, RLENGTH)
                val = val_part
                sub(/.*:/, "", val); gsub(/"/, "", val)
                s = substr(rest, RSTART+RLENGTH)
              }
              else {
                break
              }

              # Output month,kwh (gts is already YYYY-MM format)
              print gts "," val
            }
          }
        '
      )

      if [[ "${#ROWS[@]}" -eq 0 ]]; then
        echo "        (No monthly values returned)"
      else
        printf "%s\n" "${ROWS[@]}" >> "$out"
        echo "        Got ${#ROWS[@]} monthly values"
      fi
    done  # devices

    # Count rows written (excluding header)
    row_count=$(($(wc -l < "$out") - 1))
    if [[ "$row_count" -gt 0 ]]; then
      echo "      Yearly CSV written: $out ($row_count months)"
    else
      echo "      (No data written to CSV)"
    fi
  done  # plants
done  # accounts

echo
echo "DONE."
