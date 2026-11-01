#!/usr/bin/env bash
set -euo pipefail

# Source common configuration and functions
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common_config.sh"
source "$SCRIPT_DIR/shinemonitor_common.sh"

# 1st arg = credentials.json (optional, defaults to central config)
CREDS="${1:-$CREDENTIALS_FILE}"

# 2nd arg = year (optional), default = LAST YEAR (UTC)
YEAR="${2:-$(date -u -d "$(date -u +%Y-01-01) -1 day" +%Y)}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1"; exit 2; }; }
need curl sha1sum awk sed grep tr date mkdir

company_key="$(sed -nE 's/.*"company_key"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "$CREDS" | head -n 1)"
[[ -z "$company_key" ]] && { echo "ERROR: company_key missing"; exit 2; }

echo "== ShineMonitor YEARLY CHECK =="
echo "Config : $CREDS"
echo "Year   : $YEAR (default=last year if not specified)"
echo "Output : data/<safe_label>-<safe_plant>-${YEAR}.csv"
echo "--------------------------------------------------"

mkdir -p data

# ---- Accounts extraction (same as your monthly) ----
mapfile -t ACCOUNTS < <(
  awk '
    BEGIN { in_accounts=0; depth=0; buf="" }
    /"accounts"[[:space:]]*:[[:space:]]*\[/ { in_accounts=1; next }
    in_accounts {
      if ($0 ~ /\]/) { in_accounts=0; next }
      if ($0 ~ /{/) { depth++; buf="" }
      if (depth > 0) { buf = buf $0 }
      if ($0 ~ /}/) {
        depth--
        if (depth == 0) { print buf; buf="" }
      }
    }
  ' "$CREDS" | tr -d '\r'
)

if [[ "${#ACCOUNTS[@]}" -eq 0 ]]; then
  echo "ERROR: No accounts found"
  exit 2
fi

for acc in "${ACCOUNTS[@]}"; do
  label="$(printf "%s" "$acc" | json_obj_get_str label || true)"
  username="$(printf "%s" "$acc" | json_obj_get_str username || true)"
  password="$(printf "%s" "$acc" | json_obj_get_str password || true)"

  [[ -z "$username" || -z "$password" ]] && continue
  [[ -z "$label" ]] && label="$username"

  echo
  echo "Account: $label"

  # ---- AUTH using shared function ----
  if ! shinemonitor_auth_email "$username" "$password" "$company_key"; then
    echo "  AUTH FAIL"
    continue
  fi

  echo "  AUTH OK"

  # ---- PLANTS (PAGINATED) ----
  PAGESIZE=50
  page=0
  PLANT_LINES=()

  while :; do
    plants_resp="$(shinemonitor_api_call "queryPlants" "page=${page}&pagesize=${PAGESIZE}" || true)"

    if [[ -z "$plants_resp" ]] || ! printf "%s" "$plants_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
      echo "  PLANTS FAIL (page=$page)"
      break
    fi

    total="$(printf "%s" "$plants_resp" | tr -d '\n' | sed -nE 's/.*"total"[[:space:]]*:[[:space:]]*([0-9]+).*/\1/p')"

    mapfile -t LINES < <(
      printf "%s" "$plants_resp" | tr -d '\r\n' |
      awk '
        {
          s=$0
          while (match(s, /"pid"[[:space:]]*:[[:space:]]*[0-9]+/)) {
            pid_part = substr(s, RSTART, RLENGTH)
            pid = pid_part
            sub(/.*:/, "", pid); gsub(/[[:space:]]*/, "", pid)

            rest = substr(s, RSTART+RLENGTH)
            name=""
            if (match(rest, /"name"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
              name_part = substr(rest, RSTART, RLENGTH)
              name = name_part
              sub(/.*:"/, "", name); sub(/"$/, "", name)
            }

            print pid "|" name
            s = rest
          }
        }
      '
    )

    for x in "${LINES[@]}"; do
      PLANT_LINES+=("$x")
    done

    count_so_far=$(( (page + 1) * PAGESIZE ))
    if [[ -n "${total:-}" && "$count_so_far" -ge "$total" ]]; then
      break
    fi
    if [[ "${#LINES[@]}" -eq 0 ]]; then
      break
    fi

    page=$((page + 1))
  done

  if [[ "${#PLANT_LINES[@]}" -eq 0 ]]; then
    echo "  No plants found."
    continue
  fi

  # Deduplicate (pid|name)
  mapfile -t PLANT_LINES < <(printf "%s\n" "${PLANT_LINES[@]}" | awk '!seen[$0]++')

  # sanitize label once per account
  safe_label="$(printf "%s" "$label" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]/-/g' \
    | sed 's/--*/-/g' \
    | sed 's/^-//;s/-$//')"

  for pl in "${PLANT_LINES[@]}"; do
    pid="${pl%%|*}"
    pname="${pl#*|}"
    [[ -z "$pid" ]] && continue

    safe_name="$(printf "%s" "$pname" \
      | tr '[:upper:]' '[:lower:]' \
      | sed 's/[^a-z0-9]/-/g' \
      | sed 's/--*/-/g' \
      | sed 's/^-//;s/-$//')"

    echo "    Plant: ${pname:-?} (pid=$pid)"

    # Staged, then swapped in only if it carries real figures - see the guard
    # after the month loop. Unlike the monthly script this one always writes
    # 12 rows, so a month with no answer needs a figure from somewhere.
    final_out="data/${safe_label}-${safe_name}-${YEAR}.csv"
    out="$(mktemp)"
    : > "$out"
    echo "month,kwh" >> "$out"
    kept_months=""

    # Loop months 01..12
    for m in $(seq -w 1 12); do
      ym="${YEAR}-${m}"

      m_resp="$(shinemonitor_api_call "queryPlantEnergyMonth" "plantid=${pid}&date=${ym}" || true)"

      kwh=""
      if printf "%s" "$m_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
        kwh="$(printf "%s" "$m_resp" | tr -d '\n' | json_blob_get_first energy || true)"
      fi

      # No figure: a failed call, or err=12 (no data). Only a real answer may
      # change a month, so keep what is committed and fall back to 0 only when
      # nothing is. This used to write 0 outright, and the all-zero guard below
      # only catches all twelve at once, so each failed call zeroed one month.
      # On 2026-11-01 that was 54 months across 17 plants, in the yearly totals
      # customers are sent every Sunday.
      if [[ -z "$kwh" ]]; then
        kwh="$(csv_figure_for "$final_out" "$ym")"
        if [[ -z "$kwh" ]]; then
          kwh="0"
        elif [[ "$kwh" =~ [1-9] ]]; then
          kept_months+=" ${ym}"
        fi
      fi

      echo "${ym},${kwh}" >> "$out"
    done

    if [[ -n "$kept_months" ]]; then
      echo "      WARNING: no figure from the vendor for${kept_months} - kept what is committed"
    fi

    # Swap in only if the year has at least one non-zero month, or there is
    # nothing to lose. A brand-new plant legitimately reads all zeros, so the
    # existence check matters: without it a genuinely empty year could never
    # be written at all.
    if csv_has_production "$out" || [ ! -s "$final_out" ]; then
      mv "$out" "$final_out"
      echo "      Yearly CSV written: $final_out"
    else
      echo "      WARNING: every month came back zero - keeping existing ${final_out}"
      rm -f "$out"
    fi
  done
done

echo
echo "DONE."
