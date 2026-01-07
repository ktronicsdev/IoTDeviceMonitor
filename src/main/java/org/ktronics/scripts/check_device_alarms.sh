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

# Step 1: Authenticate using shared function
echo "[1/3] Authenticating..."
echo "  Username: ${USERNAME}"
echo "  Company Key: ${COMPANY_KEY:0:8}..."

if ! shinemonitor_auth_email "${USERNAME}" "${PASSWORD}" "${COMPANY_KEY}"; then
  exit 1
fi

echo "✓ Authenticated successfully (token: ${SM_TOKEN:0:8}...)"

# Step 2: Fetch ALL alarms using shared API call function
echo "[2/3] Fetching ALL device alarms (TESTING MODE)..."

# API parameters for ALL alarms (TESTING MODE)
# status=0 means UNHANDLED, status=1 means HANDLED
# For testing, we fetch ALL alarms (no status filter) to test email feature
alarms_response=$(shinemonitor_api_call "webQueryPlantsWarning" "date=")

err=$(echo "$alarms_response" | json_blob_get_first "err")

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

# Count alarms in response (look for "dat" array)
alarm_count=$(echo "$alarms_response" | grep -o '"dat":\[' | wc -l)

if [ "$alarm_count" -gt 0 ]; then
  echo "✓ Found alarms (TESTING MODE - includes HANDLED) - saved to ${OUTPUT_FILE}"
else
  echo "✓ No alarms found"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
exit 0
