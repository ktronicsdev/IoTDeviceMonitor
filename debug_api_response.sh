#!/usr/bin/env bash
################################################################################
# debug_api_response.sh - Debug device alarm API responses
################################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="${SCRIPT_DIR}/src/main/java/org/ktronics/scripts"

source "${SCRIPTS_DIR}/shinemonitor_common.sh"

# Load credentials
CREDS_FILE="${SCRIPT_DIR}/src/main/java/org/ktronics/config/credentials.json"

if [ ! -f "$CREDS_FILE" ]; then
  echo "❌ Error: credentials.json not found at $CREDS_FILE"
  exit 1
fi

# Extract first account credentials
USERNAME=$(cat "$CREDS_FILE" | python3 -c "import sys, json; data = json.load(sys.stdin); print(data['accounts'][0]['username'])")
PASSWORD=$(cat "$CREDS_FILE" | python3 -c "import sys, json; data = json.load(sys.stdin); print(data['accounts'][0]['password'])")
COMPANY_KEY=$(cat "$CREDS_FILE" | python3 -c "import sys, json; data = json.load(sys.stdin); print(data['company_key'])")
LABEL=$(cat "$CREDS_FILE" | python3 -c "import sys, json; data = json.load(sys.stdin); print(data['accounts'][0]['label'])")

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔍 DEBUG: Testing Device Alarm API for: $LABEL ($USERNAME)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# API endpoints
LOGIN_API="${API_URL}?action=authSource"
ALARMS_API="${API_URL}?action=webQueryPlantsWarning"

# Step 1: Login
echo "[1/3] Authenticating..."
salt=$(salt_ms)
pw_sha1=$(sha1hex "${PASSWORD}")
sign=$(sha1hex "${USERNAME}${pw_sha1}${salt}")

login_response=$(curl -s -X POST "$LOGIN_API" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "usr=${USERNAME}&company-key=${COMPANY_KEY}&pwd=${pw_sha1}&sign=${sign}&salt=${salt}")

echo "Login Response:"
echo "$login_response" | python3 -m json.tool 2>/dev/null || echo "$login_response"
echo ""

token=$(echo "$login_response" | json_blob_get_first "token")
secret=$(echo "$login_response" | json_blob_get_first "secret")
err=$(echo "$login_response" | json_blob_get_first "err")

if [ -z "$token" ] || [ "$err" != "0" ]; then
  echo "❌ Authentication failed"
  exit 1
fi

echo "✓ Authenticated (token: ${token:0:12}...)"
echo ""

# Step 2: Fetch ALL alarms (TESTING MODE)
echo "[2/3] Fetching ALL device alarms (no status filter)..."
salt=$(salt_ms)
sign=$(sha1hex "${token}${salt}${secret}")

alarms_response=$(curl -s -X POST "$ALARMS_API" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "sign=${sign}&salt=${salt}&token=${token}&date=")

echo "Alarms API Response:"
echo "$alarms_response" | python3 -m json.tool 2>/dev/null || echo "$alarms_response"
echo ""

# Step 3: Parse and count alarms
echo "[3/3] Analyzing response..."

# Count alarms in "dat" array
alarm_count=$(echo "$alarms_response" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    alarms = data.get('dat', [])
    print(len(alarms))
except:
    print(0)
" 2>/dev/null || echo "0")

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 SUMMARY:"
echo "  Total alarms found: $alarm_count"

if [ "$alarm_count" -gt 0 ]; then
  echo "  ✓ Alarms exist - email should be sent"

  # Show first alarm as sample
  echo ""
  echo "Sample alarm (first one):"
  echo "$alarms_response" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    alarms = data.get('dat', [])
    if alarms:
        alarm = alarms[0]
        print(f\"  Plant: {alarm.get('pName', 'Unknown')}\")
        print(f\"  Device: {alarm.get('devName', 'Unknown')}\")
        print(f\"  Message: {alarm.get('warnMsg', 'No message')}\")
        print(f\"  Time: {alarm.get('warnTime', 'Unknown')}\")
        print(f\"  Status: {alarm.get('status', 'Unknown')}\")
except Exception as e:
    print(f\"  Error parsing: {e}\")
"
else
  echo "  ℹ️  No alarms - email will NOT be sent"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
