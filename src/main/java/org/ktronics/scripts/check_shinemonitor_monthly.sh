#!/usr/bin/env bash
set -euo pipefail

API_URL="http://api.shinemonitor.com/public/"

# 1st arg = credentials.json (required)
CREDS="${1:?Usage: $0 <credentials.json> [YYYY-MM]}"

# 2nd arg = month (optional), default = LAST MONTH (UTC)
MONTH="${2:-$(date -u -d "$(date -u +%Y-%m-01) -1 day" +%Y-%m)}"

# ---- Requirements ----
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1"; exit 2; }; }
need curl sha1sum awk sed grep tr date mkdir

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

# ---- AUTH ----
s="$(salt_ms)"
pwd_sha="$(sha1hex "$password")"
usr_enc="$(urlencode "$username")"
ck_enc="$(urlencode "$company_key")"

auth_tail="&action=authEmail&usr=${usr_enc}&company-key=${ck_enc}"
sign="$(sha1hex "${s}${pwd_sha}${auth_tail}")"

auth_resp="$(curl -sS --max-time 25 \
  "${API_URL}?sign=${sign}&salt=${s}&action=authEmail&usr=${usr_enc}&company-key=${ck_enc}")"


  if ! printf "%s" "$auth_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
    echo "  AUTH FAIL"
    continue
  fi

  token="$(printf "%s" "$auth_resp" | tr -d '\n' | json_blob_get_first token)"
  secret="$(printf "%s" "$auth_resp" | tr -d '\n' | json_blob_get_first secret)"
  [[ -z "$token" || -z "$secret" ]] && { echo "  AUTH FAIL (parse)"; continue; }

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
    s2="$(salt_ms)"
    act_plants="&action=queryPlants&page=${page}&pagesize=${PAGESIZE}"
    sign2="$(sha1hex "${s2}${secret}${token}${act_plants}")"

    plants_resp="$(curl -sS --max-time 25 \
      "${API_URL}?sign=${sign2}&salt=${s2}&token=${token}${act_plants}" || true)"

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

    # ---- MONTH TOTAL ----
    s3="$(salt_ms)"
    act_m="&action=queryPlantEnergyMonth&plantid=${pid}&date=${MONTH}"
    sign3="$(sha1hex "${s3}${secret}${token}${act_m}")"

    m_resp="$(curl -sS --max-time 25 \
      "${API_URL}?sign=${sign3}&salt=${s3}&token=${token}${act_m}" || true)"

    total_kwh="$(printf "%s" "$m_resp" | tr -d '\n' | json_blob_get_first energy || true)"
    echo "      Month total kWh: ${total_kwh:-?}"

    # ---- MONTH PER DAY ----
    s4="$(salt_ms)"
    act_d="&action=queryPlantEnergyMonthPerDay&plantid=${pid}&date=${MONTH}"
    sign4="$(sha1hex "${s4}${secret}${token}${act_d}")"

    d_resp="$(curl -sS --max-time 25 \
      "${API_URL}?sign=${sign4}&salt=${s4}&token=${token}${act_d}" || true)"

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
     

    # Always overwrite monthly file: <pname>-<YYYY-MM>.csv
    out="data/${safe_label}-${safe_name}-${MONTH}.csv"
    : > "$out"
    echo "date,kwh" >> "$out"

    # Extract day rows: ts + val
    mapfile -t ROWS < <(
      printf "%s" "$d_resp" | tr -d '\r\n' |
      awk '
        {
          s=$0
          while (match(s, /"ts"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
            ts_part = substr(s, RSTART, RLENGTH)
            ts = ts_part
            sub(/.*:"/, "", ts); sub(/"$/, "", ts)
            day = ts
            sub(/[[:space:]].*$/, "", day)

            rest = substr(s, RSTART+RLENGTH)
            val=""
            if (match(rest, /"val"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
              val_part = substr(rest, RSTART, RLENGTH)
              val = val_part
              sub(/.*:"/, "", val); sub(/"$/, "", val)
            } else {
              # If val is not found, stop scanning
              break
            }

            print day "," val
            s = rest
          }
        }
      '
    )

    if [[ "${#ROWS[@]}" -eq 0 ]]; then
      echo "      (No daily rows returned)"
    else
      printf "%s\n" "${ROWS[@]}" >> "$out"
    fi

    echo "      Daily CSV written: $out"
  done
done

echo
echo "DONE."
