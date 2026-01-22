#!/usr/bin/env bash
################################################################################
# check_dessmonitor_device_alarms.sh
#
# UC3: DessMonitor Device Alarm Detection
# Fetch UNHANDLED device alarms from DessMonitor API and save to JSON.
# Uses queryDeviceWarning API endpoint to get alarm data.
#
# Usage:
#   ./check_dessmonitor_device_alarms.sh <username> <password> <company_key> <output_file>
#
# Exit codes:
#   0 - Success
#   1 - Error (authentication failed, API error, etc.)
################################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/dessmonitor_common.sh"

# Arguments
USERNAME="${1:?Missing username}"
PASSWORD="${2:?Missing password}"
COMPANY_KEY="${3:?Missing company_key}"
OUTPUT_FILE="${4:?Missing output_file}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔔 CHECKING DESSMONITOR DEVICE ALARMS FOR: ${USERNAME}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Step 1: Authenticate using shared function (dual fallback)
echo "[1/3] Authenticating..."
echo "  Username: ${USERNAME}"
echo "  Company Key: ${COMPANY_KEY:0:8}..."

if ! dessmonitor_authenticate "${USERNAME}" "${PASSWORD}" "${COMPANY_KEY}"; then
  echo "❌ Authentication failed for ${USERNAME}"
  exit 1
fi

echo "✓ Authenticated successfully (token: ${DM_TOKEN:0:8}...)"

# Step 2: Fetch ALL alarms using shared API call function
echo "[2/3] Fetching ALL device alarms (TESTING MODE)..."

# Try multiple API endpoints for device alarms (DessMonitor API discovery)
# Try 1: queryPlantAlert (plant-level alarm endpoint)
alarms_response=$(dessmonitor_api_call "queryPlantAlert" "date=" || true)

# DEBUG: Show raw API response to diagnose failures
echo "DEBUG: Raw API response (queryPlantAlert):"
echo "$alarms_response"
echo ""

err=$(echo "$alarms_response" | json_blob_get_first "err" || echo "unknown")

# If queryPlantAlert fails, try alternate endpoint
if [ "$err" != "0" ]; then
  echo "  → queryPlantAlert failed (err: ${err}), trying queryDeviceWarning..."

  # Try 2: webQueryPlantsWarning (same as ShineMonitor)
  alarms_response=$(dessmonitor_api_call "webQueryPlantsWarning" "date=" || true)

  echo "DEBUG: Raw API response (webQueryPlantsWarning):"
  echo "$alarms_response"
  echo ""

  err=$(echo "$alarms_response" | json_blob_get_first "err" || echo "unknown")
fi

# Handle error 264 (ERR_NOT_FOUND_DEVICE_WARNING) gracefully
if [ "$err" = "264" ]; then
  echo "✓ No device alarms found (err: 264 = ERR_NOT_FOUND_DEVICE_WARNING)"
  # Create empty response with no alarms
  echo '{"err":"0","dat":[]}' > "$OUTPUT_FILE"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  exit 0
fi

if [ "$err" != "0" ]; then
  echo "❌ Failed to fetch alarms (err: ${err})"
  echo "Response: ${alarms_response}"
  exit 1
fi

# Step 3: Save response to file
echo "[3/3] Saving alarms to ${OUTPUT_FILE}..."
mkdir -p "$(dirname "$OUTPUT_FILE")"

# Save raw JSON response
echo "$alarms_response" > "$OUTPUT_FILE"

# Count alarms in response
# API returns either: {"dat":[]} (no alarms) or {"dat":{"warning":[...]}} (with alarms)
if echo "$alarms_response" | grep -q '"warning":\['; then
  # Extract alarm count from "total" field if present
  total=$(echo "$alarms_response" | grep -o '"total":[0-9]*' | head -1 | cut -d':' -f2)
  if [ -n "$total" ]; then
    echo "✓ Found ${total} alarms (TESTING MODE - includes HANDLED) - saved to ${OUTPUT_FILE}"
  else
    echo "✓ Found alarms (TESTING MODE - includes HANDLED) - saved to ${OUTPUT_FILE}"
  fi
else
  echo "✓ No alarms found"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
exit 0
