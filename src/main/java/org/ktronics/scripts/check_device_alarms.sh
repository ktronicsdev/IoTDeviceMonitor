#!/usr/bin/env bash
################################################################################
# check_device_alarms.sh
#
# Fetch UNHANDLED device alarms from ShineMonitor API and save to JSON.
# Uses webQueryPlantsWarning API endpoint to get alarm data.
#
# Usage:
#   ./check_device_alarms.sh <username> <password> <company_key> <output_file>
#
# Exit codes:
#   0 - Success
#   1 - Error (authentication failed, API error, etc.)
################################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/shinemonitor_common.sh"

# Arguments
USERNAME="${1:?Missing username}"
PASSWORD="${2:?Missing password}"
COMPANY_KEY="${3:?Missing company_key}"
OUTPUT_FILE="${4:?Missing output_file}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔔 CHECKING DEVICE ALARMS FOR: ${USERNAME}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Step 1: Authenticate using shared function (try both methods)
echo "[1/3] Authenticating..."
echo "  Username: ${USERNAME}"
echo "  Company Key: ${COMPANY_KEY:0:8}..."

# Try authEmail first (GET method - faster)
if ! shinemonitor_auth_email "${USERNAME}" "${PASSWORD}" "${COMPANY_KEY}"; then
  echo "  → Retrying with authSource (POST method)..."
  if ! shinemonitor_auth_source "${USERNAME}" "${PASSWORD}" "${COMPANY_KEY}"; then
    echo "❌ Both authentication methods failed"
    exit 1
  fi
fi

echo "✓ Authenticated successfully (token: ${SM_TOKEN:0:8}...)"

# Step 2: Fetch ALL alarms using shared API call function
echo "[2/3] Fetching ALL device alarms (TESTING MODE)..."

# API parameters for ALL alarms (TESTING MODE)
# status=0 means UNHANDLED, status=1 means HANDLED
# For testing, we fetch ALL alarms (no status filter) to test email feature
alarms_response=$(shinemonitor_api_call "webQueryPlantsWarning" "date=")

# DEBUG: Show raw API response to diagnose failures
echo "DEBUG: Raw API response:"
echo "$alarms_response"
echo ""

err=$(echo "$alarms_response" | json_blob_get_first "err")

if [ "$err" != "0" ]; then
  # Error 264 = ERR_NOT_FOUND_DEVICE_WARNING = No alarms found (not an actual error)
  if [ "$err" = "264" ]; then
    echo "✓ No device alarms found (err: 264 = ERR_NOT_FOUND_DEVICE_WARNING)"
    # Create empty response with no alarms
    echo '{"err":"0","dat":[]}' > "$OUTPUT_FILE"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 0
  fi

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
