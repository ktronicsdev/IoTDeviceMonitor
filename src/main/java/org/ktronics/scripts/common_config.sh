#!/usr/bin/env bash
#
# Centralized configuration for all bash monitoring scripts.
# This module provides a single source of truth for file paths.
#

# Central credentials file path (relative to project root)
CREDENTIALS_FILE="src/main/java/org/ktronics/config/credentials.json"

# Central feature-flags file (see features.py for the authoritative reader)
FEATURES_FILE="src/main/java/org/ktronics/config/features.json"

# feature_enabled <dotted.path> -> exit 0 if the flag is on, 1 if off.
# Honours the same KT_FEATURE_* env overrides as features.py, and fails OPEN
# (treats an unreadable flags file as "on") so a broken file never silences alerts.
feature_enabled() {
  local path="$1"
  local env_name="KT_FEATURE_$(echo "$path" | tr '.a-z' '_A-Z')"
  local override="${!env_name:-}"

  if [ -n "$override" ]; then
    case "$(echo "$override" | tr 'A-Z' 'a-z')" in
      1|true|yes|on)  return 0 ;;
      0|false|no|off) return 1 ;;
    esac
  fi

  [ -f "$FEATURES_FILE" ] || return 0

  local value
  value=$(jq -r --arg p "$path" 'getpath($p | split("."))' "$FEATURES_FILE" 2>/dev/null) || return 0

  case "$value" in
    false) return 1 ;;
    *)     return 0 ;;
  esac
}
