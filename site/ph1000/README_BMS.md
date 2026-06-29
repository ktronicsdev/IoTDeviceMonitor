# Hystorix / PACEEX BMS — Cloud API (reverse-engineered)

How the **Hystorix battery BMS** (PACEEX app, `com.paicheng.bms`, by Shenzhen PACE Electronics)
cloud APIs were reverse-engineered to read true **SOC, voltage, current, cell data** for the
Mifanza packs — the data ShineMonitor's inverter cloud can't provide.

> Secrets (appSecret, account password, tokens) are **redacted** here and live only in GitHub
> secrets / the gitignored `ph1000/BMS_PROGRESS.md`. The `appKey` is the app's public client id.

---

## 1. Why it was hard, and how it was cracked

1. **Static APK analysis → wall.** The app is built on **Alibaba Cloud IoT (Living Link / ILOP)**.
   The `appKey`/`appSecret` are sealed in Alibaba's **Security Guard** blob
   (`res/drawable/yw_1222_china_production.jpg`), not extractable statically.
2. **ADB root** (BlueStacks Pie64, `enable_root_access=1`) → read the app's `shared_prefs`:
   recovered `appKey`, `appSecret`, region `ap-southeast-1`, product key, both device `iotId`s.
3. **Realtime data is MQTT** (`public.iot-as-mqtt.ap-southeast-1.aliyuncs.com`) — invisible to an
   HTTP proxy; and the **login is TLS certificate-pinned** (mitmproxy → "network error").
4. **Frida SSL-unpinning** (Java hooks: `okhttp3.CertificatePinner`, Conscrypt
   `TrustManagerImpl.verifyChain/checkTrustedRecursive`, `SSLContext.init` trust-all,
   HostnameVerifier) bypassed the pinning → the full HTTPS login + data flow was captured through
   mitmproxy.

Tooling: modern `adb` (BlueStacks' is too old), `mitmproxy` (system-CA via bind-mount over
`/system/etc/security/cacerts`), `frida` + `frida-server` (x86_64; the app's ARM64 libs run via
native bridge but the SSL pinning is Java-level, so x86_64 server + Java hooks work).

---

## 2. Architecture: two clouds

| Backend | Role | Auth |
|---|---|---|
| **`cloud.pace-power.com`** | login broker → issues an **AuthCode** + JWT (30-day refresh) | plain `Mobile`+`Password` |
| **Aliyun IoT** (`*.api-iot.aliyuncs.com`, `living-account.*`) | device list + telemetry | AuthCode → `iotToken`, HMAC-signed |

The **pace-power login is trivial** (plaintext email/password, no RSA), and its AuthCode bootstraps
the Aliyun session — so the whole pipeline is replicable in plain Python (no app/emulator needed).

---

## 3. The full request chain

All Aliyun gateway calls are signed (see §4). `appKey = 34285539`, region `ap-southeast-1`.

1. **pace-power login** — `POST https://cloud.pace-power.com/api/v1/Http2/login`
   body `Mobile=<email>&Password=<pwd>&BoundId=com.paicheng.bms&AppVersion=1.0.110&OsVersion=9&OsType=SM-S908E`
   → `Data.AuthCode` (e.g. `2026...4702`) + `Data.Token.AccessToken` (JWT, ~2 h) + `RefreshToken` (~30 d).

2. **session init** — `POST https://living-account.ap-southeast-1.aliyuncs.com/api/prd/connect.json`
   form `request={"context":{"sdkVersion":"3.4.2","utDid":"<utdid>","platformName":"android",
   "appKey":"34285539",...},"config":{"version":0,"lastModify":0}}` → `data.vid`.

3. **Aliyun login** — `POST .../api/prd/loginbyoauth.json` (header `vid:<vid>`)
   form `loginByOauthRequest={"country":"CN","authCode":"<AuthCode>","oauthPlateform":23,
   "oauthAppKey":"34285539","riskControlInfo":{...device fields, mostly static...}}`
   → `data.data.loginSuccessResult.sid` (+ `token`, `refreshToken`).

4. **create IoT session** — `POST https://ap-southeast-1.api-iot.aliyuncs.com/account/createSessionByAuthCode`
   octet body `{"id":"<uuid>","version":"1.0","request":{"$ref":"$.c"},"params":{"$ref":"$.d"},
   "a":"<uuid>","b":"1.0","c":{"apiVer":"1.0.4","language":"en-US"},
   "d":{"request":{"authCode":"<sid>","accountType":"OA_SESSION","appKey":"34285539"}}}`
   → **`data.iotToken`** (~20 h) + `refreshToken` (~200 h) + `identityId`.

5. **device list** — `POST .../uc/listBindingByAccount`, octet body with `c.iotToken=<iotToken>`,
   `d={}` → both packs: `Mifanza B1` iotId `zOLiPD3fRlynsamRRui2000000`, `Mifanza B2`
   iotId `ifpd7O8D0ZqlBGDY1fd6000000` (productKey `a1PSEqrdtdU`).

6. **telemetry** — `POST .../thing/properties/get`, octet body `c.iotToken`, `d.iotId=<iotId>`
   → `data.WIFI_Band.value` = a **hex BMS frame** (see §5). (`Dev_Mcu_Ota_State` also returned.)

> Token lifecycle for a CI poller: re-run steps 1→4 (a fresh pace-power login each run is cheapest,
> since it's plaintext), or refresh the `iotToken`/JWT within their windows.

---

## 4. Aliyun API-Gateway signature (validated against captures)

Each `api-iot` / `living-account` request carries headers `x-ca-key`, `x-ca-nonce` (uuid),
`x-ca-timestamp` (ms), `x-ca-signature-method: HmacSHA1`,
`x-ca-signature-headers: x-ca-nonce,x-ca-timestamp,x-ca-key,x-ca-signature-method`, and
`x-ca-signature`. The signature:

```
stringToSign =
  "POST\n" +
  Accept            + "\n" +   # application/json; charset=utf-8
  Content-MD5       + "\n" +   # base64(md5(body)); "" for form posts
  Content-Type      + "\n" +   # application/octet-stream; charset=utf-8  (or x-www-form-urlencoded)
  Date              + "\n" +   # HTTP date header
  "x-ca-key:" + appKey            + "\n" +   # signed headers, SORTED alphabetically
  "x-ca-nonce:" + nonce           + "\n" +
  "x-ca-signature-method:HmacSHA1"+ "\n" +
  "x-ca-timestamp:" + ts          + "\n" +
  path + "?" + query              # includes ?x-ca-request-id=<uuid>

x-ca-signature = base64( HMAC-SHA1( appSecret, stringToSign ) )
```

The `octet-stream` bodies also set `Content-MD5 = base64(md5(body))` and a
`?x-ca-request-id=<uuid>` query equal to the body's `id`/`a`.

---

## 5. `WIFI_Band` hex decode (16S LFP frame) — **fully validated**

The telemetry is a 62-byte big-endian hex frame. Every offset below was decoded in
[`check_ph1000_bms.py`](check_ph1000_bms.py) (`decode_wifi_band`) and verified **byte-for-byte
against a live capture**: with the PACEEX "Summary data" screen open, a fresh B1 frame
(`9A…506400000001…090D2101100D1D01020C0501040BFF…`) decoded to SOC 80, 53.77 V, cycles 1,
remaining 80.0 Ah, cells 3361/3357 mV, temps 34.7/34.1 °C — matching the app exactly. Two
regression tests pin these offsets ([`test_ph1000.py`](../../../../../test/java/org/ktronics/scripts/integration/test_ph1000.py) `TestBMSDecode`).

| Field | Location | Scale / encoding |
|---|---|---|
| **Current** | int16 @ 11 | ÷100 → A (**signed**: + charge / − discharge) |
| Battery voltage | uint16 @ 15 | ÷100 → V |
| Remaining capacity | uint16 @ 19 | ÷100 → Ah |
| Full capacity | uint16 @ 23 | ÷100 → Ah |
| Designed capacity | uint16 @ 27 | ÷100 → Ah |
| **SOC** | byte @ 29 | % |
| **SOH** | byte @ 30 | % |
| **Cycle count** | uint16 @ **33** | count (the word @31 next to it is reserved/0) |
| Tail: cell + temp records | `01 <addr> <u16>` from @44 | high/low cell mV @45/49 (addr 9/16); max/min temp @53/57 (addr 2/4), `(raw−2730)÷10` → °C |

Frames shorter than 60 bytes decode to `None` (treated as no-data).

---

## 6. The fetcher — `check_ph1000_bms.py` (**built & live**)

[`check_ph1000_bms.py`](check_ph1000_bms.py) runs §3.6 (`/thing/properties/get`) for both pack
`iotId`s, decodes §5, and writes a `bms` block (`{ok, packs:[{name, role, soc, voltage, current,
state, soh, remaining_ah, full_ah, cycles, max_temp, min_temp, …}]}`). The
[`trigger-ph1000.yml`](../../../../../../.github/workflows/trigger-ph1000.yml) workflow merges it
into `ph1000_live.json` every ~10 min, and the dashboard renders a **Battery Profile** pane
(B1 master + B2 slave) plus a **Live SOC = average(B1, B2)**.

### Self-renewing auth (solved — no app, no native code)

The fetcher signs the Aliyun gateway itself (§4) with `appKey` (public) + `appSecret`. For the
**session token it self-renews**: the createSession response (§3.4) also returns a long-lived
**`refreshToken`** (~200 h) and **`identityId`**, and the SDK refreshes the 20 h `iotToken` by
POSTing to **`/account/checkOrRefreshSession`** with body
`{"request":{"identityId":<id>,"refreshToken":<rt>}}` (apiVer `1.0.4`). That call is signed with
the **same APIGW HMAC we already replicate**, so it runs in pure Python — the native pace-power
login signature (`libpace.so`) is **only needed for the one-time bootstrap**, never per-run.

So each run `check_ph1000_bms.py`:
1. calls `refresh_iot_token(refreshToken, identityId)` → a fresh `iotToken` (while still valid it
   returns the current one; near expiry it issues a new one),
2. fetches both packs (§3.6) and decodes §5.

Endpoint discovery: `IoTCredentialUtils.getRefreshIoTCredentialRequest()` (Frida) revealed the
path/params; verified with a pure-Python `code:200` round-trip. The same Frida getter
(`IoTCredentialManageImpl.getInstance(ctx).getIoTRefreshToken()`) re-bootstraps the refreshToken
should the ~200 h window ever lapse — see the local helper noted below.

**Secrets** (GitHub, never committed): `BMS_IOT_REFRESH` (refreshToken), `BMS_IOT_IDENTITY`
(identityId), `BMS_APPSECRET`. `BMS_IOT_TOKEN` is now an optional static fallback.

**Fail-soft contract:** on any auth/API error (e.g. the refreshToken finally expires) the fetcher
emits `{"ok": false, "packs": []}`; the dashboard then **hides the Battery Profile pane** and
**falls back to the voltage-based SOC curve** (`socFromV`) — never showing stale battery data.

```bash
# self-renewing (preferred)
BMS_IOT_REFRESH=<rt> BMS_IOT_IDENTITY=<id> BMS_APPSECRET=<secret> \
  python check_ph1000_bms.py --out bms.json
# static fallback
BMS_IOT_TOKEN=<token> BMS_APPSECRET=<secret> python check_ph1000_bms.py --out bms.json
```

### Local re-bootstrap (belt-and-suspenders)

[`refresh_bms_creds.py`](refresh_bms_creds.py) + [`harvest_bms_cred.js`](harvest_bms_cred.js)
read the **current** credential straight from the running PACEEX app's memory
(`IoTCredentialManageImpl.getInstance(ctx)` via Frida — no UI/network) and update the GitHub
secrets. The Windows Task Scheduler job **`BMSCredsRefresh`** runs it every 5 days, so the
~200 h refreshToken in the secrets never lapses even if it does not roll forward on its own.
Needs BlueStacks + `frida-server` on the device + `gh` auth; it is the only piece that touches
the app, and only as a periodic top-up — the per-run cloud refresh needs nothing local.

`appSecret`, the account password, the refreshToken and tokens live only in GitHub secrets and the
gitignored `ph1000/BMS_PROGRESS.md` — never committed.
