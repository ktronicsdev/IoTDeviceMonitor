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
