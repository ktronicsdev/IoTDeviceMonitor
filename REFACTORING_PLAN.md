# ShineMonitor Scripts Refactoring Plan

## Summary
Eliminate code duplication across ShineMonitor API scripts by using shared authentication and API call functions in `shinemonitor_common.sh`.

## ✅ Completed
- **check_device_alarms.sh** - Refactored to use shared functions

## 📋 Remaining Scripts to Refactor

### 1. check_shinemonitor_monthly.sh
**Current duplication:**
- Lines 67-80: authEmail authentication logic
- Lines 103-115: API call for queryMonthEnergy

**Refactoring:**
```bash
# BEFORE (lines 67-80):
s=$(salt_ms)
pwd_sha=$(sha1hex "$password")
auth_tail="&action=authEmail&usr=${usr_enc}&company-key=${ck_enc}"
sign=$(sha1hex "${s}${pwd_sha}${auth_tail}")
auth_resp=$(curl -sS ... )
token=$(...)
secret=$(...)

# AFTER (3 lines):
if ! shinemonitor_auth_email "$username" "$password" "$company_key"; then
  continue
fi

# BEFORE (API call):
s2=$(salt_ms)
sign2=$(sha1hex "${token}${s2}${secret}")
month_resp=$(curl -sS ... )

# AFTER (1 line):
month_resp=$(shinemonitor_api_call "queryMonthEnergy" "pId=${plant_id}&year=${year}&month=${month}")
```

**Lines saved:** ~25 lines → ~5 lines

---

### 2. check_shinemonitor_yearly.sh
**Current duplication:**
- Lines 59-72: authEmail authentication logic
- Lines 96-108: API call for queryYearEnergy

**Refactoring:**
```bash
# BEFORE (lines 59-72):
s=$(salt_ms)
pwd_sha=$(sha1hex "$password")
auth_tail="&action=authEmail&usr=${usr_enc}&company-key=${ck_enc}"
sign=$(sha1hex "${s}${pwd_sha}${auth_tail}")
auth_resp=$(curl -sS ... )
token=$(...)
secret=$(...)

# AFTER (3 lines):
if ! shinemonitor_auth_email "$username" "$password" "$company_key"; then
  continue
fi

# BEFORE (API call):
s2=$(salt_ms)
sign2=$(sha1hex "${token}${s2}${secret}")
year_resp=$(curl -sS ... )

# AFTER (1 line):
year_resp=$(shinemonitor_api_call "queryYearEnergy" "pId=${plant_id}&year=${year}")
```

**Lines saved:** ~25 lines → ~5 lines

---

### 3. check_shinemonitor.sh
**Current duplication:**
- Lines 58-88: authEmail authentication logic
- Lines 92-96: queryPlants API call
- Lines 115-119: queryDayEnergy API call

**Refactoring:**
```bash
# BEFORE (lines 58-88):
s=$(salt_ms)
pwd_sha="$(sha1hex "$password")"
tail="&action=authEmail&usr=${username}&company-key=${company_key}"
sign="$(sha1hex "${s}${pwd_sha}${tail}")"
auth_resp="$(curl -sS ... )"
token="$(... )"
secret="$(... )"

# AFTER (3 lines):
if ! shinemonitor_auth_email "$username" "$password" "$company_key"; then
  fail_auth=$((fail_auth+1))
  continue
fi

# BEFORE (queryPlants):
s2="$(salt_ms)"
action_plants="&action=queryPlants"
sign2="$(sha1hex "${s2}${secret}${token}${action_plants}")"
plants_url="${API_URL}?sign=${sign2}&salt=${s2}&token=${token}${action_plants}"
plants_resp="$(curl -sS ... )"

# AFTER (1 line):
plants_resp=$(shinemonitor_api_call "queryPlants" "")

# BEFORE (queryDayEnergy):
s3="$(salt_ms)"
action_energy="&action=queryDayEnergy&date=${DATE_TO_TEST}"
sign3="$(sha1hex "${s3}${secret}${token}${action_energy}")"
energy_url="${API_URL}?sign=${sign3}&salt=${s3}&token=${token}${action_energy}"
energy_resp="$(curl -sS ... )"

# AFTER (1 line):
energy_resp=$(shinemonitor_api_call "queryDayEnergy" "date=${DATE_TO_TEST}")
```

**Lines saved:** ~40 lines → ~7 lines

---

### 4. check_shinemonitor_query-plants.sh
**Current duplication:**
- Lines 11-24: authEmail authentication logic
- Lines 28-32: queryPlants API call

**Refactoring:**
```bash
# BEFORE (lines 11-24):
salt=$(salt_ms)
pwd_sha=$(sha1hex "$PASSWORD")
tail="&action=authEmail&usr=${USERNAME}&company-key=${COMPANY_KEY}"
sign=$(sha1hex "${salt}${pwd_sha}${tail}")
auth_resp=$(curl -sS ... )
token=$(...)
secret=$(...)

# AFTER (3 lines):
if ! shinemonitor_auth_email "$USERNAME" "$PASSWORD" "$COMPANY_KEY"; then
  exit 1
fi

# BEFORE (API call):
salt2=$(salt_ms)
action="&action=queryPlants"
sign2=$(sha1hex "${salt2}${secret}${token}${action}")
plants_url="${API_URL}?sign=${sign2}&salt=${salt2}&token=${token}${action}"
plants_resp=$(curl -sS ... )

# AFTER (1 line):
plants_resp=$(shinemonitor_api_call "queryPlants" "")
```

**Lines saved:** ~20 lines → ~5 lines

---

## Benefits of Refactoring

### 1. **DRY Principle (Don't Repeat Yourself)**
- Authentication logic exists in ONE place
- API call logic exists in ONE place
- Reduces code duplication by ~115 lines across 4 scripts

### 2. **Maintainability**
- Future API changes only need ONE update in `shinemonitor_common.sh`
- Bug fixes apply to all scripts automatically
- Consistent error messages across all scripts

### 3. **Reliability**
- Less duplicated code = fewer bugs
- Shared functions are battle-tested
- Consistent error handling

### 4. **Readability**
- Scripts are shorter and clearer
- Intent is obvious: "authenticate then make API call"
- Less cognitive overhead

### 5. **Testability**
- Can test authentication logic in isolation
- Easier to mock for testing
- Shared functions can have integration tests

---

## Implementation Order

**Priority 1 (High Impact):**
1. ✅ check_device_alarms.sh - DONE
2. check_shinemonitor_monthly.sh - Used by production workflows
3. check_shinemonitor_yearly.sh - Used by production workflows

**Priority 2 (Medium Impact):**
4. check_shinemonitor.sh - Used for testing/verification
5. check_shinemonitor_query-plants.sh - Utility script

---

## Shared Functions Reference

### Authentication Functions

#### `shinemonitor_auth_source(username, password, company_key)`
- Uses **POST** method with `action=authSource`
- Returns: Sets `SM_TOKEN` and `SM_SECRET` global variables
- Exit code: 0=success, 1=failure
- Used by: check_device_alarms.sh

#### `shinemonitor_auth_email(username, password, company_key)`
- Uses **GET** method with `action=authEmail`
- Returns: Sets `SM_TOKEN` and `SM_SECRET` global variables
- Exit code: 0=success, 1=failure
- Used by: Most other scripts (monthly, yearly, query-plants, etc.)

### API Call Function

#### `shinemonitor_api_call(action, extra_params)`
- Makes authenticated POST request with `SM_TOKEN` and `SM_SECRET`
- Automatically handles salt and signature generation
- Returns: API response on stdout
- Examples:
  ```bash
  shinemonitor_api_call "queryPlants" ""
  shinemonitor_api_call "queryMonthEnergy" "pId=123&year=2026&month=01"
  shinemonitor_api_call "webQueryPlantsWarning" "status=0&date="
  ```

---

## Migration Template

```bash
# 1. Remove duplicate variables
# DELETE: LOGIN_API, AUTH_API, etc.

# 2. Replace authentication block
# OLD CODE (~15-20 lines):
salt=$(salt_ms)
pwd_sha=$(sha1hex "$password")
sign=$(sha1hex ...)
auth_resp=$(curl ...)
token=$(...)
secret=$(...)
if [ -z "$token" ]; then
  echo "Auth failed"
  exit 1
fi

# NEW CODE (3 lines):
if ! shinemonitor_auth_email "$username" "$password" "$company_key"; then
  exit 1
fi

# 3. Replace API calls
# OLD CODE (~5-7 lines):
salt2=$(salt_ms)
sign2=$(sha1hex "${token}${salt2}${secret}")
response=$(curl -s -X POST "${API_URL}?action=..." \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "sign=${sign2}&salt=${salt2}&token=${token}&...")

# NEW CODE (1 line):
response=$(shinemonitor_api_call "actionName" "param1=value1&param2=value2")
```

---

## Testing Plan

After each refactoring:
1. ✅ Verify script runs without errors
2. ✅ Compare output with original script
3. ✅ Test authentication failure scenarios
4. ✅ Test API error responses
5. ✅ Run in GitHub Actions workflow

---

## Notes

- All refactored scripts must source `shinemonitor_common.sh`
- Global variables `SM_TOKEN` and `SM_SECRET` are set by auth functions
- Auth functions write errors to stderr
- API response is written to stdout
- Maintain backward compatibility with existing workflows

---

## Estimated Impact

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Total Lines of Auth Code | ~115 | ~20 | **-82%** |
| Duplication Count | 5 scripts | 0 scripts | **-100%** |
| Maintenance Locations | 5 files | 1 file | **-80%** |
| Code Clarity | Mixed | High | ✅ |
| Error Handling | Inconsistent | Consistent | ✅ |

---

*Generated: 2026-01-07*
*Status: Refactoring in progress*
