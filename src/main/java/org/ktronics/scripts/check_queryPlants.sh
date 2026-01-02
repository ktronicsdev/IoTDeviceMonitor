#!/usr/bin/env bash
set -euo pipefail

API_URL="http://api.shinemonitor.com/public/"

USERNAME="$1"
PASSWORD="$2"
COMPANY_KEY="$3"

sha1hex() { printf "%s" "$1" | sha1sum | awk '{print $1}'; }
salt_ms() { date +%s%3N; }

# ---- AUTH ----
salt="$(salt_ms)"
pwd_sha="$(sha1hex "$PASSWORD")"
tail="&action=authEmail&usr=${USERNAME}&company-key=${COMPANY_KEY}"
sign="$(sha1hex "${salt}${pwd_sha}${tail}")"

auth_resp="$(curl -s \
  "${API_URL}?sign=${sign}&salt=${salt}&action=authEmail&usr=${USERNAME}&company-key=${COMPANY_KEY}")"

echo "=== AUTH RESPONSE ==="
echo "$auth_resp"
echo

token="$(printf "%s" "$auth_resp" | sed -nE 's/.*"token":"([^"]+)".*/\1/p')"
secret="$(printf "%s" "$auth_resp" | sed -nE 's/.*"secret":"([^"]+)".*/\1/p')"

if [[ -z "$token" || -z "$secret" ]]; then
  echo "AUTH FAILED"
  exit 1
fi

# ---- QUERY PLANTS ----
salt2="$(salt_ms)"
sign2="$(sha1hex "${salt2}${secret}${token}&action=queryPlants")"

plants_resp="$(curl -s \
  "${API_URL}?sign=${sign2}&salt=${salt2}&token=${token}&action=queryPlants")"

echo "=== queryPlants RESPONSE ==="
echo "$plants_resp"
