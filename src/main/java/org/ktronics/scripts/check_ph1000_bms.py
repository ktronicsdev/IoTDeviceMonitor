#!/usr/bin/env python3
"""
PH1000 battery BMS fetcher (Hystorix / PACEEX packs, via the cracked Aliyun IoT API).

Reverse-engineering & API spec: see README_BMS.md. This reads the **true** per-pack BMS
telemetry (SOC, voltage, current, capacity, SOH, cells, temps, cycles) for both Mifanza
packs and writes a `bms` block for the dashboard.

Auth: the Aliyun `iotToken` (session) is supplied via env `BMS_IOT_TOKEN` (a GitHub secret).
Fully-automated re-login is blocked by the pace-power **native** request signature
(a separate crack — see README_BMS.md §3). When the token expires this fetcher fails
gracefully (ok=false) so the dashboard hides the battery pane and falls back to the
voltage-based SOC estimate.

Usage:
    BMS_IOT_TOKEN=<token> python check_ph1000_bms.py --out bms.json
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from email.utils import formatdate

APPKEY = os.environ.get("BMS_APPKEY", "34285539")           # public app client id
APPSECRET = os.environ.get("BMS_APPSECRET", "").encode()    # secret (GitHub secret / env)
HOST = os.environ.get("BMS_HOST", "ap-southeast-1.api-iot.aliyuncs.com")
ACCEPT = "application/json; charset=utf-8"

# Self-renewal credentials (preferred over a static session token). The Aliyun IoT
# `refreshToken` (~200 h) + `identityId` mint a fresh `iotToken` every run via
# /account/checkOrRefreshSession — pure Python, no app/native code. See README_BMS.md §6.
REFRESH_TOKEN = os.environ.get("BMS_IOT_REFRESH", "")
IDENTITY_ID = os.environ.get("BMS_IOT_IDENTITY", "")

# Mifanza packs: B1 = master, B2 = slave (iotIds from the cracked device list).
PACKS = [
    {"name": "Mifanza B1", "role": "master", "iotId": "zOLiPD3fRlynsamRRui2000000"},
    {"name": "Mifanza B2", "role": "slave",  "iotId": "ifpd7O8D0ZqlBGDY1fd6000000"},
]


def _sign(path, query, content_md5, content_type, nonce, ts, date):
    """Aliyun API-Gateway HMAC-SHA1 signature (validated against captures, README_BMS.md §4)."""
    headers = ("x-ca-key:%s\nx-ca-nonce:%s\nx-ca-signature-method:HmacSHA1\nx-ca-timestamp:%s\n"
               % (APPKEY, nonce, ts))
    s = "POST\n%s\n%s\n%s\n%s\n%s%s%s" % (ACCEPT, content_md5, content_type, date, headers, path, query)
    return base64.b64encode(hmac.new(APPSECRET, s.encode(), hashlib.sha1).digest()).decode()


def _octet_call(path, d, api_ver, iot_token):
    rid = str(uuid.uuid4())
    body = json.dumps({
        "a": rid, "b": "1.0",
        "c": {"apiVer": api_ver, "language": "en-US", "iotToken": iot_token},
        "d": d, "id": rid, "params": {"$ref": "$.d"}, "request": {"$ref": "$.c"}, "version": "1.0",
    }, separators=(",", ":")).encode()
    md5 = base64.b64encode(hashlib.md5(body).digest()).decode()
    nonce, ts, date = str(uuid.uuid4()), str(int(time.time() * 1000)), formatdate(usegmt=True)
    ctype = "application/octet-stream; charset=utf-8"
    query = "?x-ca-request-id=" + rid
    sig = _sign(path, query, md5, ctype, nonce, ts, date)
    req = urllib.request.Request("https://" + HOST + path + query, data=body, method="POST")
    for k, v in {
        "Accept": ACCEPT, "Content-Type": ctype, "Content-MD5": md5, "Date": date,
        "x-ca-key": APPKEY, "x-ca-nonce": nonce, "x-ca-timestamp": ts,
        "x-ca-signature-method": "HmacSHA1",
        "x-ca-signature-headers": "x-ca-nonce,x-ca-timestamp,x-ca-key,x-ca-signature-method",
        "x-ca-signature": sig, "user-agent": "ALIYUN-ANDROID-DEMO",
    }.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def decode_wifi_band(hexstr):
    """Decode the 16S-LFP `WIFI_Band` BMS frame.

    Offsets validated byte-for-byte against the live PACEEX app ("Summary data"):
    a fresh B1 frame decoded to SOC 80, 53.77 V, cycles 1, cells 3361/3357 mV,
    temps 34.7/34.1 C, remaining 80.0 Ah — all matching the app. The tail holds
    ``01 <cell-addr> <u16 value>`` records (addr 9/16 = high/low cell mV,
    addr 2/4 = max/min temp in 0.1 K, => (raw-2730)/10 C).
    """
    b = bytes.fromhex(hexstr)
    if len(b) < 60:
        return None
    u16 = lambda i: (b[i] << 8) | b[i + 1]
    s16 = lambda i: (u16(i) - 65536 if u16(i) >= 32768 else u16(i))
    cur = s16(11) / 100.0
    d = {
        "voltage": round(u16(15) / 100.0, 2),       # V
        "current": round(cur, 2),                    # A (signed: + charge / - discharge)
        "state": "charging" if cur > 0.05 else ("discharging" if cur < -0.05 else "idle"),
        "remaining_ah": round(u16(19) / 100.0, 2),
        "full_ah": round(u16(23) / 100.0, 2),
        "designed_ah": round(u16(27) / 100.0, 2),
        "soc": b[29],                                # %
        "soh": b[30],                                # %
        "cycles": u16(33),                           # @33 (the @31 next to it is reserved/0)
        "high_cell_mv": u16(45),
        "low_cell_mv": u16(49),
        "max_temp": round((u16(53) - 2730) / 10.0, 1),  # °C
        "min_temp": round((u16(57) - 2730) / 10.0, 1),
    }
    # The datalogger pushes several frame TYPES (summary, per-cell, status). The summary
    # frame decodes here cleanly; an offline/idle logger leaves a *non-summary* frame as the
    # last value, which at these offsets yields physical nonsense (e.g. 568 V / SOC 201%).
    # Reject anything implausible so the dashboard cleanly falls back to voltage-SOC and
    # auto-resumes the moment a real summary frame arrives -- no manual re-harvest needed.
    if not _frame_is_sane(d):
        return None
    return d


def _frame_is_sane(d):
    """True only if a decoded WIFI_Band frame holds physically possible 16S-LFP values."""
    return (
        40.0 <= d["voltage"] <= 60.0           # 16 x 2.5-3.75 V
        and 0 <= d["soc"] <= 100
        and 0 <= d["soh"] <= 100
        and 1500 <= d["low_cell_mv"] <= 4000    # mV
        and 1500 <= d["high_cell_mv"] <= 4000
        and d["low_cell_mv"] <= d["high_cell_mv"]
        and -30.0 <= d["min_temp"] <= 85.0
        and -30.0 <= d["max_temp"] <= 85.0
        and abs(d["current"]) <= 600
    )


def refresh_iot_token(refresh_token, identity_id, cur_token=""):
    """Mint a fresh iotToken from the (long-lived) refreshToken + identityId.

    Calls /account/checkOrRefreshSession (the same endpoint the PACEEX app's SDK uses):
    while the current token is valid it returns it unchanged; near expiry it issues a new
    one. Returns (iotToken, refreshToken) — the refreshToken may rotate, so callers should
    persist the returned one. Pure APIGW-HMAC signed; needs no app/native signature.
    """
    d = {"request": {"identityId": identity_id, "refreshToken": refresh_token}}
    r = _octet_call("/account/checkOrRefreshSession", d, "1.0.4", cur_token)
    if r.get("code") != 200:
        raise RuntimeError("checkOrRefreshSession code %s" % r.get("code"))
    data = r["data"]
    return data["iotToken"], data.get("refreshToken", refresh_token)


def fetch_pack(pack, iot_token):
    resp = _octet_call("/thing/properties/get", {"iotId": pack["iotId"]}, "1.0.0", iot_token)
    if resp.get("code") != 200:
        raise RuntimeError("API code %s" % resp.get("code"))
    wb = resp["data"]["WIFI_Band"]
    data = decode_wifi_band(wb["value"])
    if data is None:
        # Datalogger offline/idle: last cached frame is non-summary -> can't read this pack.
        return {"name": pack["name"], "role": pack["role"], "iotId": pack["iotId"],
                "offline": True, "reported_ms": int(wb.get("time", 0)) or None}
    # `time` = when the pack's datalogger last pushed this frame (epoch ms). Packs report at
    # different rates (B2 lags ~20-30 min), so surface it; the dashboard shows the data's age.
    data["reported_ms"] = int(wb.get("time", 0)) or None
    data.update({"name": pack["name"], "role": pack["role"], "iotId": pack["iotId"]})
    return data


def main():
    ap = argparse.ArgumentParser(description="PH1000 BMS fetcher (Hystorix/PACEEX)")
    ap.add_argument("--out", help="write the bms JSON block here")
    ap.add_argument("--token", default=os.environ.get("BMS_IOT_TOKEN", ""))
    ap.add_argument("--rotated-out", dest="rotated_out",
                    help="if the refreshToken rotated, write {refreshToken,identityId} JSON here "
                         "(the workflow persists it back into the GitHub secret — cloud self-renewal)")
    args = ap.parse_args()

    out = {"ok": False, "packs": [], "generated": formatdate(usegmt=True)}

    # Preferred: self-renew the iotToken from the long-lived refreshToken (no app needed).
    token = args.token
    if REFRESH_TOKEN and IDENTITY_ID:
        try:
            token, new_rt = refresh_iot_token(REFRESH_TOKEN, IDENTITY_ID, args.token)
            print("[BMS] iotToken refreshed via checkOrRefreshSession")
            if new_rt and new_rt != REFRESH_TOKEN:
                # refreshToken rotated — persist it so the next stateless run uses the fresh one.
                print("[BMS] refreshToken ROTATED -> persisting new value to secret")
                if args.rotated_out:
                    with open(args.rotated_out, "w", encoding="utf-8") as f:
                        json.dump({"refreshToken": new_rt, "identityId": IDENTITY_ID}, f)
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, RuntimeError) as e:
            print("[BMS] refresh failed (%s) -> falling back to static BMS_IOT_TOKEN" % e)

    if not token:
        print("[BMS] no token (set BMS_IOT_REFRESH+BMS_IOT_IDENTITY, or BMS_IOT_TOKEN) "
              "-> dashboard falls back to voltage SOC")
    else:
        try:
            for p in PACKS:
                d = fetch_pack(p, token)
                if d.get("offline"):
                    # Skip from packs -> dashboard ignores it and uses voltage SOC. Self-heals
                    # the moment the logger pushes a fresh summary frame again.
                    out.setdefault("offline_packs", []).append(d["name"])
                    print("[BMS] %s: datalogger offline (no fresh summary frame) -> skipped"
                          % d["name"])
                    continue
                out["packs"].append(d)
                print("[BMS] %s: SOC %d%% %.2fV %.2fA %s temp %.1f/%.1f cyc %d"
                      % (d["name"], d["soc"], d["voltage"], d["current"], d["state"],
                         d["max_temp"], d["min_temp"], d["cycles"]))
            out["ok"] = len(out["packs"]) > 0
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, RuntimeError) as e:
            # token expired / auth failed -> ok stays False; dashboard hides the pane.
            print("[BMS] fetch failed (token expired?): %s -> dashboard falls back to voltage SOC" % e)
            out = {"ok": False, "packs": [], "generated": formatdate(usegmt=True)}

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print("[BMS] wrote", args.out)
    else:
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
