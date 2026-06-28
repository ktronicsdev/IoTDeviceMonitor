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

## 5. `WIFI_Band` hex decode (16S LFP frame)

The telemetry is a big-endian hex frame. Verified byte offsets against the live BMS readout
(SOC 55%, 53.57 V, 100 Ah, SOH 100%, cells ~3349 mV):

| Field | Location | Scale |
|---|---|---|
| Battery voltage | uint16 @ offset 15 | ÷100 → V |
| Full / designed capacity | uint16 @ offset 23 / 27 | ÷100 → Ah |
| **SOC** | byte @ offset 29 | % |
| **SOH** | byte @ offset 30 | % |
| Cell voltage(s) | uint16 @ offset 45, 49 | mV |

(Remaining offsets — current, temps, cycles, remaining capacity — map similarly; refine with more
samples or the PACE protocol sheet in `ph1000/`.)

---

## 6. Using it (sustainable, CI-friendly)

The fetcher (planned `check_ph1000_bms.py`) needs only the **account email + password** and the
**appKey/appSecret** (in `SHINEMONITOR_CREDENTIALS_JSON` / a new BMS secret) — no app or emulator.
It runs steps §3.1→6 for both `iotId`s, decodes §5, and feeds **true SOC / V / cell data** into the
PH1000 dashboard (replacing the rough voltage-curve estimate).
