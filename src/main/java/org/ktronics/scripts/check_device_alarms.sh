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

# API endpoints
LOGIN_API="${API_URL}?action=authSource"
ALARMS_API="${API_URL}?action=webQueryPlantsWarning"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔔 CHECKING DEVICE ALARMS FOR: ${USERNAME}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Step 1: Login to get token & secret
echo "[1/3] Authenticating..."
salt=$(salt_ms)
pw_sha1=$(sha1hex "${PASSWORD}")
sign=$(sha1hex "${USERNAME}${pw_sha1}${salt}")

login_response=$(curl -s -X POST "$LOGIN_API" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "usr=${USERNAME}&company-key=${COMPANY_KEY}&pwd=${pw_sha1}&sign=${sign}&salt=${salt}")

token=$(echo "$login_response" | json_blob_get_first "token")
secret=$(echo "$login_response" | json_blob_get_first "secret")
err=$(echo "$login_response" | json_blob_get_first "err")

if [ -z "$token" ] || [ "$err" != "0" ]; then
  echo "❌ Authentication failed for ${USERNAME}"
  echo "Response: ${login_response}"
  exit 1
fi

echo "✓ Authenticated successfully (token: ${token:0:8}...)"

# Step 2: Fetch ALL alarms (TESTING MODE - normally status=0 for UNHANDLED only)
echo "[2/3] Fetching ALL device alarms (TESTING MODE)..."
salt=$(salt_ms)
sign=$(sha1hex "${token}${salt}${secret}")

# API parameters for ALL alarms (TESTING MODE)
# status=0 means UNHANDLED, status=1 means HANDLED
# For testing, we fetch ALL alarms (no status filter) to test email feature
alarms_response=$(curl -s -X POST "$ALARMS_API" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "sign=${sign}&salt=${salt}&token=${token}&date=")

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
