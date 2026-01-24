#!/usr/bin/env bash
set -euo pipefail

# UC10: Multi-Cloud Platform Support - DessMonitor Yearly Data Fetcher
# Fetches yearly energy data from DessMonitor API by aggregating monthly data
# Uses device-level API (querySPDeviceKeyParameterMonthPerDay) since plant-level API returns 0

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
  PLANT_DATA=$(echo "$plants_resp" | jq -r '.dat[]? | "\(.pid)|\\(.name // "unknown")"' 2>/dev/null || true)

  # If empty, try alternate format .dat.plant[]
  if [[ -z "$PLANT_DATA" ]]; then
    PLANT_DATA=$(echo "$plants_resp" | jq -r '.dat.plant[]? | "\(.pid // .id)|\\(.name // .pname // "unknown")"' 2>/dev/null || true)
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

    # ---- LOOP THROUGH MONTHS 01..12 ----
    for m in $(seq -w 1 12); do
      ym="${YEAR}-${m}"
      month_total="0"

      # ---- PROCESS EACH DEVICE ----
      echo "$DEVICE_DATA" | while read -r device_line; do
        [[ -z "$device_line" ]] && continue

        # Parse 4 fields: pn|sn|devcode|devaddr
        device_pn="${device_line%%|*}"
        rest="${device_line#*|}"
        device_sn="${rest%%|*}"
        rest="${rest#*|}"
        devcode="${rest%%|*}"
        devaddr="${rest#*|}"

        # ---- QUERY DEVICE ENERGY for this month ----
        # CRITICAL: Use ENERGY_TODAY parameter (returns real data)
        d_resp="$(dessmonitor_api_call "querySPDeviceKeyParameterMonthPerDay" \
          "pn=${device_pn}&sn=${device_sn}&devcode=${devcode}&devaddr=${devaddr}&parameter=ENERGY_TODAY&date=${ym}&i18n=en_US&chartStatus=false" || true)"

        # Check if API call succeeded
        if [[ -z "$d_resp" ]] || ! printf "%s" "$d_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
          continue
        fi

        # Sum all daily values for this month using awk
        # Response format: {"dat":{"option":[{"gts":"2026-01-01","val":"8.6120"},...]}}
        device_sum=$(echo "$d_resp" | tr -d '\r\n' | \
          awk '
            {
              s=$0
              total=0
              while (1) {
                # Match val field: "val":"8.6120" or "val":8.6120
                if (match(s, /"val"[[:space:]]*:[[:space:]]*"?[0-9.]+/)) {
                  val_part = substr(s, RSTART, RLENGTH)
                  val = val_part
                  sub(/.*:/, "", val); gsub(/"/, "", val)
                  total += val
                  s = substr(s, RSTART+RLENGTH)
                } else {
                  break
                }
              }
              printf "%.4f", total
            }
          ')

        # Add device sum to month total (handled via subshell - need to print for aggregation)
        echo "$device_sum"
      done | awk '{total+=$1} END {printf "%.4f", total}' > "/tmp/dessmonitor_month_total_$$"

      month_total=$(cat "/tmp/dessmonitor_month_total_$$" 2>/dev/null || echo "0")
      rm -f "/tmp/dessmonitor_month_total_$$"

      # Write month row
      echo "${ym},${month_total}" >> "$out"
    done

    echo "      Yearly CSV written: $out"
  done  # plants
done  # accounts

echo
echo "DONE."
