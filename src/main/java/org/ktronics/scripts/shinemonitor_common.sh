#!/usr/bin/env bash
# Common configuration and functions for ShineMonitor scripts

# API Configuration
API_URL="https://web.shinemonitor.com/public/"

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

# Authenticate with ShineMonitor API using authSource (POST method)
# Returns: Sets global variables SM_TOKEN and SM_SECRET
# Usage: shinemonitor_auth_source "username" "password" "company_key"
# Exit codes: 0=success, 1=auth failed
shinemonitor_auth_source() {
  local username="${1:?Missing username}"
  local password="${2:?Missing password}"
  local company_key="${3:?Missing company_key}"

  local salt pw_sha1 sign login_response token secret err

  salt=$(salt_ms)
  pw_sha1=$(sha1hex "${password}")
  sign=$(sha1hex "${username}${pw_sha1}${salt}")

  login_response=$(curl -s -X POST "${API_URL}?action=authSource" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "usr=$(urlencode "${username}")&company-key=${company_key}&pwd=${pw_sha1}&sign=${sign}&salt=${salt}")

  token=$(echo "$login_response" | json_blob_get_first "token")
  secret=$(echo "$login_response" | json_blob_get_first "secret")
  err=$(echo "$login_response" | json_blob_get_first "err")

  if [ -z "$token" ] || [ "$err" != "0" ]; then
    echo "❌ Authentication failed (authSource) for ${username}" >&2
    echo "Response: ${login_response}" >&2
    return 1
  fi

  # Export as global variables
  SM_TOKEN="$token"
  SM_SECRET="$secret"

  return 0
}

# Authenticate with ShineMonitor API using authEmail (GET method)
# Returns: Sets global variables SM_TOKEN and SM_SECRET
# Usage: shinemonitor_auth_email "username" "password" "company_key"
# Exit codes: 0=success, 1=auth failed
shinemonitor_auth_email() {
  local username="${1:?Missing username}"
  local password="${2:?Missing password}"
  local company_key="${3:?Missing company_key}"

  local salt pw_sha1 tail sign auth_url auth_resp token secret

  salt=$(salt_ms)
  pw_sha1=$(sha1hex "$password")
  tail="&action=authEmail&usr=$(urlencode "${username}")&company-key=${company_key}"
  sign=$(sha1hex "${salt}${pw_sha1}${tail}")

  auth_url="${API_URL}?sign=${sign}&salt=${salt}&action=authEmail&usr=$(urlencode "${username}")&company-key=${company_key}"
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
  SM_TOKEN="$token"
  SM_SECRET="$secret"

  return 0
}

# Make an authenticated API call with token and secret
# Usage: shinemonitor_api_call "action" "extra_params"
# Example: shinemonitor_api_call "queryPlants" ""
# Example: shinemonitor_api_call "webQueryPlantsWarning" "status=0&date="
# Returns: Prints API response to stdout
shinemonitor_api_call() {
  local action="${1:?Missing action}"
  local extra_params="${2:-}"

  if [ -z "$SM_TOKEN" ] || [ -z "$SM_SECRET" ]; then
    echo "ERROR: SM_TOKEN and SM_SECRET must be set (call authentication first)" >&2
    return 1
  fi

  local salt sign action_string api_response

  salt=$(salt_ms)

  # Build the action string that will be included in the signature
  if [ -n "$extra_params" ]; then
    action_string="&action=${action}&${extra_params}"
  else
    action_string="&action=${action}"
  fi

  # Signature order: salt, secret, token, action_string
  sign=$(sha1hex "${salt}${SM_SECRET}${SM_TOKEN}${action_string}")

  api_response=$(curl -sS --max-time 25 "${API_URL}?sign=${sign}&salt=${salt}&token=${SM_TOKEN}${action_string}" 2>/dev/null || true)

  echo "$api_response"
}
