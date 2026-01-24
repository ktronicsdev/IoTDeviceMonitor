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

    # ---- MONTH PER DAY using plant-level API (not device-level) ----
    # Try queryPlantEnergyMonthPerDay first (matches ShineMonitor pattern)
    d_resp="$(dessmonitor_api_call "queryPlantEnergyMonthPerDay" "plantid=${pid}&date=${MONTH}" || true)"

    # DEBUG: Show API response
    echo "      DEBUG queryPlantEnergyMonthPerDay response: $(echo "$d_resp" | head -c 500)"

    # Check if API call succeeded
    if [[ -z "$d_resp" ]] || ! printf "%s" "$d_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
      echo "      queryPlantEnergyMonthPerDay failed, trying alternate API..."

      # Try alternate endpoint (webQueryPlantEnergyMonthPerDay)
      d_resp="$(dessmonitor_api_call "webQueryPlantEnergyMonthPerDay" "plantid=${pid}&date=${MONTH}" || true)"

      if [[ -z "$d_resp" ]] || ! printf "%s" "$d_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
        echo "      No daily data available for this plant"
        continue
      fi
    fi

    # Sanitize plant name for filename (lowercase, no spaces/symbols)
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

    # Always overwrite monthly file: dessmonitor-<label>-<pname>-<YYYY-MM>.csv
    out="data/dessmonitor-${safe_label}-${safe_name}-${MONTH}.csv"
    : > "$out"
    echo "date,kwh" >> "$out"

    # Extract day rows: val + ts (API returns val before ts, same as ShineMonitor)
    # FIX: DessMonitor API returns unquoted numbers: "val":10.5 (not "val":"10.5")
    mapfile -t ROWS < <(
      printf "%s" "$d_resp" | tr -d '\r\n' |
      awk '
        {
          s=$0
          while (1) {
            val=""
            # Try quoted format first: "val":"10.5"
            if (match(s, /"val"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
              val_part = substr(s, RSTART, RLENGTH)
              val = val_part
              sub(/.*:"/, "", val); sub(/"$/, "", val)
              rest = substr(s, RSTART+RLENGTH)
            }
            # Try unquoted format: "val":10.5 (DessMonitor API format)
            else if (match(s, /"val"[[:space:]]*:[[:space:]]*[0-9.]+/)) {
              val_part = substr(s, RSTART, RLENGTH)
              val = val_part
              sub(/.*:/, "", val)
              rest = substr(s, RSTART+RLENGTH)
            }
            else {
              break
            }

            ts=""
            # Try quoted ts format: "ts":"2026-01-01"
            if (match(rest, /"ts"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
              ts_part = substr(rest, RSTART, RLENGTH)
              ts = ts_part
              sub(/.*:"/, "", ts); sub(/"$/, "", ts)
              day = ts
              sub(/[[:space:]].*$/, "", day)
            }
            # Try unquoted ts format (unlikely but handle it)
            else if (match(rest, /"ts"[[:space:]]*:[[:space:]]*[0-9-]+/)) {
              ts_part = substr(rest, RSTART, RLENGTH)
              ts = ts_part
              sub(/.*:/, "", ts)
              day = ts
            }
            else {
              break
            }

            print day "," val
            s = rest
          }
        }
      '
    )

    if [[ "${#ROWS[@]}" -eq 0 ]]; then
      echo "      (No daily rows returned - API may not support daily breakdown)"
    else
      printf "%s\n" "${ROWS[@]}" >> "$out"
      echo "      Daily CSV written: $out (${#ROWS[@]} days)"
    fi
  done  # plants
done  # accounts

echo
echo "DONE."
