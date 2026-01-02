#!/usr/bin/env bash
set -euo pipefail

API_URL="http://api.shinemonitor.com/public/"

# 1st arg = credentials.json (required)
CREDS="${1:?Usage: $0 <credentials.json> [YYYY]}"

# 2nd arg = year (optional), default = LAST YEAR (UTC)
YEAR="${2:-$(date -u -d "$(date -u +%Y-01-01) -1 day" +%Y)}"

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

json_blob_get_first() {
  local key="$1"
  sed -nE "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"?([^\",}]+)\"?.*/\1/p" | head -n 1
}

json_obj_get_str() {
  local key="$1"
  sed -nE "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"([^\"]*)\".*/\1/p" | head -n 1
}

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

  # ---- AUTH (URL-encoded in BOTH tail + URL) ----
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

  # ---- PLANTS (PAGINATED) ----
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

    out="data/${safe_label}-${safe_name}-${YEAR}.csv"
    : > "$out"
    echo "month,kwh" >> "$out"

    # Loop months 01..12
    for m in $(seq -w 1 12); do
      ym="${YEAR}-${m}"

      s3="$(salt_ms)"
      act_m="&action=queryPlantEnergyMonth&plantid=${pid}&date=${ym}"
      sign3="$(sha1hex "${s3}${secret}${token}${act_m}")"

      m_resp="$(curl -sS --max-time 25 \
        "${API_URL}?sign=${sign3}&salt=${s3}&token=${token}${act_m}" || true)"

      if printf "%s" "$m_resp" | grep -q '"err"[[:space:]]*:[[:space:]]*0'; then
        kwh="$(printf "%s" "$m_resp" | tr -d '\n' | json_blob_get_first energy || true)"
        [[ -z "$kwh" ]] && kwh="0"
      else
        # If API returns err=12 (no data) or other, write 0 to keep shape
        kwh="0"
      fi

      echo "${ym},${kwh}" >> "$out"
    done

    echo "      Yearly CSV written: $out"
  done
done

echo
echo "DONE."
