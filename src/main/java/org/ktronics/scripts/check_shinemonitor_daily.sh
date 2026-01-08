#!/usr/bin/env bash
set -euo pipefail

# Source common configuration and functions
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common_config.sh"
source "$SCRIPT_DIR/shinemonitor_common.sh"

# Script-specific configuration
CREDS="${1:-$CREDENTIALS_FILE}"
DATE_TO_TEST="${DATE_TO_TEST:-$(date -u -d "yesterday" +%F)}"
TEST_PLANTS="${TEST_PLANTS:-1}"
TEST_ENERGY="${TEST_ENERGY:-1}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1"; exit 2; }; }
need curl
need sha1sum
need awk
need sed
need grep
need date

# Extract first match of a JSON string field: "key":"value"
json_get_first() {
  local key="$1"
  sed -nE "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"([^\"]*)\".*/\1/p" "$CREDS" | head -n 1
}

company_key="$(json_get_first company_key)"
if [[ -z "$company_key" ]]; then
  echo "ERROR: company_key not found in $CREDS"
  exit 2
fi

echo "ShineMonitor API check (NO jq) — Bash+grep"
echo "API_URL: $API_URL"
echo "CREDS:   $CREDS"
echo "DATE:    $DATE_TO_TEST"
echo "------------------------------------------"

fail_auth=0
fail_plants=0
fail_energy=0

# Pull accounts by extracting each object containing username+password.
# This is simplistic but works for typical JSON.
# It emits lines: label|username|password
while IFS= read -r line; do
  label="$(printf "%s" "$line" | sed -nE 's/.*"label"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p')"
  username="$(printf "%s" "$line" | sed -nE 's/.*"username"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p')"
  password="$(printf "%s" "$line" | sed -nE 's/.*"password"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p')"

  [[ -z "$label" ]] && label="$username"

  if [[ -z "$username" || -z "$password" ]]; then
    continue
  fi

  s="$(salt_ms)"
  pwd_sha="$(sha1hex "$password")"
  tail="&action=authEmail&usr=${username}&company-key=${company_key}"
  sign="$(sha1hex "${s}${pwd_sha}${tail}")"

  auth_url="${API_URL}?sign=${sign}&salt=${s}&action=authEmail&usr=${username}&company-key=${company_key}"
  auth_resp="$(curl -sS --max-time 25 "$auth_url" || true)"

  if [[ -z "$auth_resp" ]]; then
    echo "[AUTH][FAIL] $label: empty response"
    fail_auth=$((fail_auth+1))
    continue
  fi

  if ! printf "%s" "$auth_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
    err="$(printf "%s" "$auth_resp" | json_blob_get_first err || true)"
    echo "[AUTH][FAIL] $label: err=${err:-?} resp=$(echo "$auth_resp" | tr -d '\n' | head -c 220)..."
    fail_auth=$((fail_auth+1))
    continue
  fi

  token="$(printf "%s" "$auth_resp" | tr -d '\n' | json_blob_get_first token || true)"
  secret="$(printf "%s" "$auth_resp" | tr -d '\n' | json_blob_get_first secret || true)"

  if [[ -z "$token" || -z "$secret" ]]; then
    echo "[AUTH][FAIL] $label: could not extract token/secret (parsing fragile)"
    fail_auth=$((fail_auth+1))
    continue
  fi

  echo "[AUTH][OK]   $label"

  [[ "$TEST_PLANTS" != "1" ]] && continue

  s2="$(salt_ms)"
  action_plants="&action=queryPlants"
  sign2="$(sha1hex "${s2}${secret}${token}${action_plants}")"
  plants_url="${API_URL}?sign=${sign2}&salt=${s2}&token=${token}${action_plants}"
  plants_resp="$(curl -sS --max-time 25 "$plants_url" || true)"

  if [[ -z "$plants_resp" ]]; then
    echo "  [PLANTS][FAIL] $label: empty response"
    fail_plants=$((fail_plants+1))
    continue
  fi

  if ! printf "%s" "$plants_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
    err2="$(printf "%s" "$plants_resp" | json_blob_get_first err || true)"
    echo "  [PLANTS][FAIL] $label: err=${err2:-?} resp=$(echo "$plants_resp" | tr -d '\n' | head -c 220)..."
    fail_plants=$((fail_plants+1))
    continue
  fi

  # Extract first pid and name (fragile, but ok for now)
  first_pid="$(printf "%s" "$plants_resp" | tr -d '\n' | sed -nE 's/.*"pid"[[:space:]]*:[[:space:]]*([0-9]+).*/\1/p' | head -n 1)"
  first_name="$(printf "%s" "$plants_resp" | tr -d '\n' | sed -nE 's/.*"name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n 1)"

  echo "  [PLANTS][OK]  $label: first pid=${first_pid:-?} name=${first_name:-?}"

  [[ "$TEST_ENERGY" != "1" ]] && continue
  [[ -z "${first_pid:-}" ]] && { echo "    [ENERGY][SKIP] $label: no pid parsed"; continue; }

  s3="$(salt_ms)"
  action_energy="&action=queryPlantEnergyDay&plantid=${first_pid}&date=${DATE_TO_TEST}"
  sign3="$(sha1hex "${s3}${secret}${token}${action_energy}")"
  energy_url="${API_URL}?sign=${sign3}&salt=${s3}&token=${token}${action_energy}"
  energy_resp="$(curl -sS --max-time 25 "$energy_url" || true)"

  if [[ -z "$energy_resp" ]]; then
    echo "    [ENERGY][FAIL] $label: empty response"
    fail_energy=$((fail_energy+1))
    continue
  fi

  # err=0 or err=12
  if printf "%s" "$energy_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
    energy="$(printf "%s" "$energy_resp" | tr -d '\n' | json_blob_get_first energy || true)"
    echo "    [ENERGY][OK] $label: date=$DATE_TO_TEST energy=${energy:-?}"
  elif printf "%s" "$energy_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*12'; then
    echo "    [ENERGY][OK] $label: date=$DATE_TO_TEST err=12 (no data) => 0.0"
  else
    err3="$(printf "%s" "$energy_resp" | tr -d '\n' | json_blob_get_first err || true)"
    echo "    [ENERGY][FAIL] $label: err=${err3:-?} resp=$(echo "$energy_resp" | tr -d '\n' | head -c 220)..."
    fail_energy=$((fail_energy+1))
  fi

done < <(
  # Convert JSON to one-line-per-account object by grabbing {...} blocks containing username/password
  tr -d '\r\n' < "$CREDS" \
    | sed 's/},{/}\n{/g' \
    | grep -E '"username"[[:space:]]*:' \
    | grep -E '"password"[[:space:]]*:'
)

echo "------------------------------------------"
echo "Summary: AUTH=$fail_auth PLANTS=$fail_plants ENERGY=$fail_energy"

if [[ "$fail_auth" -gt 0 || "$fail_plants" -gt 0 || "$fail_energy" -gt 0 ]]; then
  exit 1
fi
echo "All checks passed."
