#!/usr/bin/env bash
set -euo pipefail

# Source common configuration and functions
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common_config.sh"
source "$SCRIPT_DIR/shinemonitor_common.sh"

# 1st arg = credentials.json (optional, defaults to central config)
CREDS="${1:-$CREDENTIALS_FILE}"

# 2nd arg = month (optional), default = LAST MONTH (UTC)
MONTH="${2:-$(date -u -d "$(date -u +%Y-%m-01) -1 day" +%Y-%m)}"

# ---- Requirements ----
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1"; exit 2; }; }
need curl sha1sum awk sed grep tr date mkdir

# ---- Read company_key ----
company_key="$(sed -nE 's/.*"company_key"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "$CREDS" | head -n 1)"
[[ -z "$company_key" ]] && { echo "ERROR: company_key missing"; exit 2; }

echo "== ShineMonitor MONTHLY CHECK =="
echo "Config : $CREDS"
echo "Month  : $MONTH"
echo "Output : data/<label>-<plantId>-${MONTH}.csv"
echo "----------------------------------------------"

mkdir -p data

# -------------------------------------------------------------------
# ACCOUNT EXTRACTION (CORRECT & SAFE)
# -------------------------------------------------------------------
# Emits EACH account object as one line
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

# -------------------------------------------------------------------
# PROCESS EACH ACCOUNT
# -------------------------------------------------------------------
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

  # -----------------------------------------------------------------
  # PLANTS (PAGINATED!)
  # Your real response shows: total=2 page=0 pagesize=1
  # So we must paginate or increase pagesize.
  # -----------------------------------------------------------------
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
    # Extract pid|name pairs from THIS page
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

    # stop when enough pages fetched
    count_so_far=$(( (page + 1) * PAGESIZE ))
    if [[ -n "${total:-}" && "$count_so_far" -ge "$total" ]]; then
      break
    fi

    # or stop if no results on this page
    if [[ "${#LINES[@]}" -eq 0 ]]; then
      break
    fi

    page=$((page + 1))
  done

  if [[ "${#PLANT_LINES[@]}" -eq 0 ]]; then
    echo "  No plants found."
    continue
  fi

  # Optional: dedupe pid lines (in case API repeats)
  mapfile -t PLANT_LINES < <(printf "%s\n" "${PLANT_LINES[@]}" | awk '!seen[$0]++')

  # -----------------------------------------------------------------
  # PROCESS EACH PLANT
  # -----------------------------------------------------------------
  for pl in "${PLANT_LINES[@]}"; do
    pid="${pl%%|*}"
    pname="${pl#*|}"
    [[ -z "$pid" ]] && continue

    echo "    Plant: ${pname:-?} (pid=$pid)"

    # ---- MONTH TOTAL using shared function ----
    m_resp="$(shinemonitor_api_call "queryPlantEnergyMonth" "plantid=${pid}&date=${MONTH}" || true)"

    total_kwh="$(printf "%s" "$m_resp" | tr -d '\n' | json_blob_get_first energy || true)"
    echo "      Month total kWh: ${total_kwh:-?}"

    # ---- MONTH PER DAY using shared function ----
    d_resp="$(shinemonitor_api_call "queryPlantEnergyMonthPerDay" "plantid=${pid}&date=${MONTH}" || true)"

    # Sanitize plant name for filename (lowercase, no spaces/symbols)
    safe_name="$(printf "%s" "$pname" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]/-/g' \
    | sed 's/--*/-/g' \
    | sed 's/^-//;s/-$//')"

    safe_label="$(printf "%s" "$label" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]/-/g' \
    | sed 's/--*/-/g' \
    | sed 's/^-//;s/-$//')"
     

    # Build the month in a temporary file and swap it in only if the API
    # actually gave us rows (see the guard at the end of this block).
    #
    # This used to truncate the real file here, before the response had been
    # parsed. When ShineMonitor answered with an empty body - which it does
    # to GitHub runners, see #32 - the plant's CSV was left holding nothing
    # but its header AND COMMITTED in that state. Proof on main: this file
    # for crdesilva-home alternated 31 rows / 0 rows / 31 rows across
    # 20-22 Oct. connectivity.py then read "no data rows at all" and mailed
    # DATA LINK DOWN for a plant that had reported that same day.
    final_out="data/${safe_label}-${safe_name}-${MONTH}.csv"
    out="$(mktemp)"
    : > "$out"
    echo "date,kwh" >> "$out"

    # Extract day rows: val + ts (API returns val before ts!)
    mapfile -t ROWS < <(
      printf "%s" "$d_resp" | tr -d '\r\n' |
      awk '
        {
          s=$0
          while (match(s, /"val"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
            val_part = substr(s, RSTART, RLENGTH)
            val = val_part
            sub(/.*:"/, "", val); sub(/"$/, "", val)

            rest = substr(s, RSTART+RLENGTH)
            ts=""
            if (match(rest, /"ts"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
              ts_part = substr(rest, RSTART, RLENGTH)
              ts = ts_part
              sub(/.*:"/, "", ts); sub(/"$/, "", ts)
              day = ts
              sub(/[[:space:]].*$/, "", day)
            } else {
              # If ts is not found, stop scanning
              break
            }

            print day "," val
            s = rest
          }
        }
      '
    )

    if [[ "${#ROWS[@]}" -eq 0 ]]; then
      # Keep whatever is already committed. An empty answer is the vendor
      # having a moment, not the plant having no history, and overwriting
      # real data with a header is not a thing a fetch should ever do.
      # Loud on purpose: the workflow greps this step's output, so a quiet
      # message would be filtered out exactly like AUTH FAIL is (#32).
      echo "      WARNING: no daily rows returned - keeping existing ${final_out}"
      rm -f "$out"
    else
      printf "%s\n" "${ROWS[@]}" >> "$out"
      mv "$out" "$final_out"
      echo "      Daily CSV written: $final_out"
    fi
  done
done

echo
echo "DONE."
