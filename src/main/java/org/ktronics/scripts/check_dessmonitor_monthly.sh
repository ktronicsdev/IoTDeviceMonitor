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
  # PROCESS EACH PLANT
  # -----------------------------------------------------------------
  echo "$PLANT_DATA" | while read -r plant_line; do
    [[ -z "$plant_line" ]] && continue

    pid="${plant_line%%|*}"
    pname="${plant_line#*|}"
    [[ -z "$pid" ]] && continue

    echo "    Plant: ${pname:-?} (pid=$pid)"

    # ---- Query devices for this plant ----
    # Try queryDevices first (returns devices directly for plant)
    devices_resp="$(dessmonitor_api_call "queryDevices" "plantid=${pid}&page=0&pagesize=50" || true)"

    # DEBUG: Show devices response
    echo "      DEBUG queryDevices response: $(echo "$devices_resp" | head -c 800)"

    if [[ -z "$devices_resp" ]] || ! printf "%s" "$devices_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
      echo "      queryDevices failed, trying webQueryCollectorsEs..."

      # Fallback to collectors approach
      collectors_resp="$(dessmonitor_api_call "webQueryCollectorsEs" "plantid=${pid}&page=0&pagesize=50" || true)"
      echo "      DEBUG webQueryCollectorsEs response: $(echo "$collectors_resp" | head -c 500)"

      if [[ -z "$collectors_resp" ]] || ! printf "%s" "$collectors_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
        echo "      No devices or collectors found"
        continue
      fi
    fi

    # Extract device info - try multiple formats
    # Format 1: .dat[] array with pn/sn
    DEVICE_DATA=$(echo "$devices_resp" | jq -r '.dat[]? | "\(.pn // .sn)|\(.devcode // 2429)|\(.devaddr // 1)"' 2>/dev/null || true)

    # Format 2: .dat.device[] nested
    if [[ -z "$DEVICE_DATA" ]]; then
      DEVICE_DATA=$(echo "$devices_resp" | jq -r '.dat.device[]? | "\(.pn // .sn)|\(.devcode // 2429)|\(.devaddr // 1)"' 2>/dev/null || true)
    fi

    # Format 3: Check for .dat with total/page structure
    if [[ -z "$DEVICE_DATA" ]]; then
      DEVICE_DATA=$(echo "$devices_resp" | jq -r '.dat[]? | select(.pn or .sn) | "\(.pn // .sn)|\(.devcode // 2429)|\(.devaddr // 1)"' 2>/dev/null || true)
    fi

    if [[ -z "$DEVICE_DATA" ]]; then
      echo "      No devices found for plant"
      echo "      DEBUG: dat content: $(echo "$devices_resp" | jq '.dat' 2>/dev/null || echo 'parse error')"
      continue
    fi

    echo "      Found devices: $(echo "$DEVICE_DATA" | wc -l)"

    # For each device, get energy data
    echo "$DEVICE_DATA" | while read -r device_line; do
      [[ -z "$device_line" ]] && continue

      # Parse: pn|devcode|devaddr
      pn=$(echo "$device_line" | cut -d'|' -f1)
      devcode=$(echo "$device_line" | cut -d'|' -f2)
      devaddr=$(echo "$device_line" | cut -d'|' -f3)
      [[ -z "$pn" ]] && continue

      echo "      Device: pn=$pn, devcode=$devcode, devaddr=$devaddr"

      # ---- Query device energy ----
      energy_resp="$(dessmonitor_api_call "webQueryDeviceEs" "pn=${pn}&devcode=${devcode}&devaddr=${devaddr}&sn=${pn}" || true)"

      if [[ -z "$energy_resp" ]] || ! printf "%s" "$energy_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
        echo "        Energy query failed, trying querySPDeviceLastData..."
        energy_resp="$(dessmonitor_api_call "querySPDeviceLastData" "pn=${pn}&devaddr=${devaddr}" || true)"
      fi

      echo "        DEBUG energy response: $(echo "$energy_resp" | head -c 300)"

      if [[ -z "$energy_resp" ]] || ! printf "%s" "$energy_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
        echo "        Energy query failed"
        continue
      fi

      # Extract energy values
      e_today=$(echo "$energy_resp" | jq -r '.dat.e_today // .dat.eToday // 0' 2>/dev/null || echo "0")
      e_month=$(echo "$energy_resp" | jq -r '.dat.e_month // .dat.eMonth // 0' 2>/dev/null || echo "0")
      e_year=$(echo "$energy_resp" | jq -r '.dat.e_year // .dat.eYear // 0' 2>/dev/null || echo "0")
      e_total=$(echo "$energy_resp" | jq -r '.dat.e_total // .dat.eTotal // 0' 2>/dev/null || echo "0")

      echo "        Today: ${e_today} kWh, Month: ${e_month} kWh, Year: ${e_year} kWh"

      # ---- Save to CSV ----
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

      out="data/dessmonitor-${safe_label}-${safe_name}-${MONTH}.csv"

      if [[ ! -f "$out" ]]; then
        echo "date,kwh" > "$out"
      fi

      today=$(date -u +%Y-%m-%d)
      echo "${today},${e_today}" >> "$out"

      echo "        CSV written: $out"

    done  # devices
  done  # plants
done  # accounts

echo
echo "DONE."
