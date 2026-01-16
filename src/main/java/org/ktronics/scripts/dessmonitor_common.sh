#!/usr/bin/env bash
# Common configuration and functions for DessMonitor scripts
# UC10: Multi-Cloud Platform Support - DessMonitor Integration

# API Configuration
# DessMonitor uses web.dessmonitor.com (same backend as ShineMonitor but different domain)
API_URL="https://web.dessmonitor.com/public/"

# Common utility functions
sha1hex() { printf "%s" "$1" | sha1sum | awk '{print $1}'; }
salt_ms() { date +%s%3N; }

urlencode() {
  local s="${1:-}" out="" i c
  for ((i=0; i<${#s}; i++)); do
    c="${s:i:1}"
    case "$c" in
      [a-zA-Z0-9._~-]) out+="$c" ;;
      *) printf -v out '%s%%%02X' "$out" "'$c" ;;
    esac
  done
  printf '%s' "$out"
}

# Extract first JSON field value from a blob (token/secret/energy/err)
json_blob_get_first() {
  local key="$1"
  sed -nE "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"?([^\",}]+)\"?.*/\1/p" | head -n 1
}

# Extract a JSON string value from ONE object string
json_obj_get_str() {
  local key="$1"
  sed -nE "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"([^\"]*)\".*/\1/p" | head -n 1
}

################################################################################
# SHARED AUTHENTICATION FUNCTIONS
################################################################################

# Authenticate with DessMonitor API using authSource (POST method)
# Returns: Sets global variables DM_TOKEN and DM_SECRET
# Usage: dessmonitor_auth_source "username" "password" "company_key"
# Exit codes: 0=success, 1=auth failed
dessmonitor_auth_source() {
  local username="${1:?Missing username}"
  local password="${2:?Missing password}"
  local company_key="${3:?Missing company_key}"

  local salt pw_sha1 sign login_response token secret err

  salt=$(salt_ms)
  pw_sha1=$(sha1hex "${password}")
  sign=$(sha1hex "${username}${pw_sha1}${salt}")

  login_response=$(curl -s -X POST "${API_URL}?action=authSource" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "usr=$(urlencode "${username}")&company-key=${company_key}&pwd=${pw_sha1}&sign=${sign}&salt=${salt}&source=1")

  token=$(echo "$login_response" | json_blob_get_first "token")
  secret=$(echo "$login_response" | json_blob_get_first "secret")
  err=$(echo "$login_response" | json_blob_get_first "err")

  if [ -z "$token" ] || [ "$err" != "0" ]; then
    echo "❌ Authentication failed (authSource) for ${username}" >&2
    echo "Response: ${login_response}" >&2
    return 1
  fi

  # Export as global variables
  DM_TOKEN="$token"
  DM_SECRET="$secret"

  return 0
}

# Authenticate with DessMonitor API using authEmail (GET method)
# Returns: Sets global variables DM_TOKEN and DM_SECRET
# Usage: dessmonitor_auth_email "username" "password" "company_key"
# Exit codes: 0=success, 1=auth failed
dessmonitor_auth_email() {
  local username="${1:?Missing username}"
  local password="${2:?Missing password}"
  local company_key="${3:?Missing company_key}"

  local salt pw_sha1 tail sign auth_url auth_resp token secret

  salt=$(salt_ms)
  pw_sha1=$(sha1hex "$password")
  tail="&action=authEmail&usr=$(urlencode "${username}")&company-key=${company_key}"
  sign=$(sha1hex "${salt}${pw_sha1}${tail}")

  auth_url="${API_URL}?sign=${sign}&salt=${salt}&action=authEmail&usr=$(urlencode "${username}")&company-key=${company_key}&source=1"
  auth_resp=$(curl -sS --max-time 25 "$auth_url" 2>/dev/null || true)

  if [ -z "$auth_resp" ]; then
    echo "❌ Authentication failed (authEmail) for ${username}: empty response" >&2
    return 1
  fi

  if ! printf "%s" "$auth_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
    local err_code
    err_code=$(printf "%s" "$auth_resp" | json_blob_get_first err || echo "?")
    echo "❌ Authentication failed (authEmail) for ${username}: err=${err_code}" >&2
    echo "Response: $(echo "$auth_resp" | tr -d '\n' | head -c 220)..." >&2
    return 1
  fi

  token=$(printf "%s" "$auth_resp" | tr -d '\n' | json_blob_get_first token || true)
  secret=$(printf "%s" "$auth_resp" | tr -d '\n' | json_blob_get_first secret || true)

  if [ -z "$token" ] || [ -z "$secret" ]; then
    echo "❌ Authentication failed (authEmail) for ${username}: could not extract token/secret" >&2
    return 1
  fi

  # Export as global variables
  DM_TOKEN="$token"
  DM_SECRET="$secret"

  return 0
}

# Authenticate with dual fallback (try authEmail first, then authSource)
# Returns: Sets global variables DM_TOKEN and DM_SECRET
# Usage: dessmonitor_authenticate "username" "password" "company_key"
# Exit codes: 0=success, 1=both methods failed
dessmonitor_authenticate() {
  local username="${1:?Missing username}"
  local password="${2:?Missing password}"
  local company_key="${3:?Missing company_key}"

  # Try authEmail first (GET method - faster)
  if dessmonitor_auth_email "$username" "$password" "$company_key"; then
    echo "✓ Authenticated via authEmail for ${username}" >&2
    return 0
  fi

  # Fallback to authSource (POST method)
  echo "⚠ authEmail failed, trying authSource..." >&2
  if dessmonitor_auth_source "$username" "$password" "$company_key"; then
    echo "✓ Authenticated via authSource for ${username}" >&2
    return 0
  fi

  echo "❌ All authentication methods failed for ${username}" >&2
  return 1
}

# Make an authenticated API call with token and secret
# Usage: dessmonitor_api_call "action" "extra_params"
# Example: dessmonitor_api_call "queryPlants" ""
# Example: dessmonitor_api_call "queryDeviceWarning" "pn=12345&devcode=2507&devaddr=1&sn=12345"
# Returns: Prints API response to stdout
dessmonitor_api_call() {
  local action="${1:?Missing action}"
  local extra_params="${2:-}"

  if [ -z "$DM_TOKEN" ] || [ -z "$DM_SECRET" ]; then
    echo "ERROR: DM_TOKEN and DM_SECRET must be set (call authentication first)" >&2
    return 1
  fi

  local salt sign action_string api_response

  salt=$(salt_ms)

  # Build the action string that will be included in the signature
  # DessMonitor uses source=1 for energy storage
  if [ -n "$extra_params" ]; then
    action_string="&action=${action}&source=1&${extra_params}"
  else
    action_string="&action=${action}&source=1"
  fi

  # Signature order: salt, secret, token, action_string
  sign=$(sha1hex "${salt}${DM_SECRET}${DM_TOKEN}${action_string}")

  api_response=$(curl -sS --max-time 25 "${API_URL}?sign=${sign}&salt=${salt}&token=${DM_TOKEN}${action_string}" 2>/dev/null || true)

  echo "$api_response"
}

# Query plants/projects for the authenticated user
# Returns: JSON response with plant list
dessmonitor_query_plants() {
  dessmonitor_api_call "queryPlants" ""
}

# Query device warnings/alarms
# Usage: dessmonitor_query_device_warnings "pn" "devcode" "devaddr" "sn"
# Returns: JSON response with warning list
dessmonitor_query_device_warnings() {
  local pn="${1:?Missing pn (collector ID)}"
  local devcode="${2:?Missing devcode}"
  local devaddr="${3:?Missing devaddr}"
  local sn="${4:?Missing sn}"

  dessmonitor_api_call "queryDeviceWarning" "pn=${pn}&devcode=${devcode}&devaddr=${devaddr}&sn=${sn}&i18n=en_US&page=0&pagesize=50"
}

# Query device last data (real-time readings)
# Usage: dessmonitor_query_device_data "pn" "devcode" "devaddr" "sn"
# Returns: JSON response with latest device data
dessmonitor_query_device_data() {
  local pn="${1:?Missing pn (collector ID)}"
  local devcode="${2:?Missing devcode}"
  local devaddr="${3:?Missing devaddr}"
  local sn="${4:?Missing sn}"

  dessmonitor_api_call "querySPDeviceLastData" "pn=${pn}&devcode=${devcode}&devaddr=${devaddr}&sn=${sn}&i18n=en_US"
}

# Query plant energy statistics
# Usage: dessmonitor_query_plant_energy "plant_id" "date" (date format: YYYY-MM-DD)
# Returns: JSON response with energy data
dessmonitor_query_plant_energy() {
  local plant_id="${1:?Missing plant_id}"
  local date="${2:?Missing date}"

  dessmonitor_api_call "queryPlantActiveOuputPowerOneDay" "plantid=${plant_id}&date=${date}&i18n=en_US"
}
