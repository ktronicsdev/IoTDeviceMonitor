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
    if len(b) < 35:
        return None
    u16 = lambda i: (b[i] << 8) | b[i + 1]
    s16 = lambda i: (u16(i) - 65536 if u16(i) >= 32768 else u16(i))
    cur = s16(11) / 100.0
    # The fixed-offset HEADER (bytes 0-34) is the reliable part of the frame: it carries
    # voltage/current/SOC/SOH/Ah/cycles and decodes identically across frame variants.
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
        "high_cell_mv": None,
        "low_cell_mv": None,
        "max_temp": None,
        "min_temp": None,
    }
    # The datalogger pushes several frame TYPES. An offline/idle logger leaves a *non-summary*
    # frame cached, which at these header offsets yields physical nonsense (e.g. 568 V /
    # SOC 201%). Gate on the reliable header so the dashboard cleanly falls back to voltage-SOC
    # and auto-resumes the moment a real summary frame arrives -- no manual re-harvest needed.
    if not _frame_is_sane(d):
        return None
    # The tail holds up to four `01 <addr> <u16>` records in a FIXED ORDER: high-cell mV,
    # low-cell mV, max-temp, min-temp (temps in 0.1 K -> C). Their byte offset shifts per
    # frame (and zero-padding precedes them), so scan for the real records and read them
    # positionally rather than from a fixed offset.
    recs = []
    i = 34
    while i + 3 < len(b) - 2:            # stop before the trailing 2-byte CRC + terminator
        if b[i] == 0x01:
            v = (b[i + 2] << 8) | b[i + 3]
            if 2500 <= v <= 3700:        # a real cell-mV / temp(0.1 K) record; skips 0-padding
                recs.append(v)
                i += 4
                continue
        i += 1
    if len(recs) >= 2:
        d["high_cell_mv"], d["low_cell_mv"] = recs[0], recs[1]
    if len(recs) >= 4:
        d["max_temp"] = round((recs[2] - 2730) / 10.0, 1)
        d["min_temp"] = round((recs[3] - 2730) / 10.0, 1)
    return d


def _frame_is_sane(d):
    """True only if the reliable header of a WIFI_Band frame holds physically possible values.

    Validates ONLY fixed-offset header fields (voltage/SOC/SOH/current/Ah) -- the cell-mV and
    temperature fields sit at variable tail offsets and are parsed/range-guarded separately.
    """
    return (
        40.0 <= d["voltage"] <= 60.0           # 16 x 2.5-3.75 V
        and 0 <= d["soc"] <= 100
        and 0 <= d["soh"] <= 100
        and abs(d["current"]) <= 600
        and 0 <= d["full_ah"] <= 2000
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

    # auth_failed = the cloud SESSION is dead (refreshToken rejected) and needs a one-time app
    # re-login. Distinct from a pack being merely offline (ok:false but session fine). The
    # workflow alerts the operator only on auth_failed -- the rare event a human must act on.
    out = {"ok": False, "packs": [], "generated": formatdate(usegmt=True), "auth_failed": False}

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
        except RuntimeError as e:
            # checkOrRefreshSession REJECTED the refreshToken -> the cloud session is dead. No
            # cloud/script path recovers this; it needs a one-time app re-login. Flag it so the
            # workflow emails the operator (see the BMS re-login alert in trigger-ph1000.yml).
            print("[BMS] refreshToken REJECTED (%s) -> session dead, app re-login needed" % e)
            out["auth_failed"] = True
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError) as e:
            # transient network/parse hiccup -> not a session death; fall back, don't alert.
            print("[BMS] refresh failed transiently (%s) -> falling back to static token" % e)

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
                temp = ("%.1f/%.1f" % (d["max_temp"], d["min_temp"])
                        if d["max_temp"] is not None else "n/a")
                print("[BMS] %s: SOC %d%% %.2fV %.2fA %s temp %s cyc %d"
                      % (d["name"], d["soc"], d["voltage"], d["current"], d["state"],
                         temp, d["cycles"]))
            out["ok"] = len(out["packs"]) > 0
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, RuntimeError) as e:
            # token expired / auth failed -> ok stays False; dashboard hides the pane.
            print("[BMS] fetch failed (token expired?): %s -> dashboard falls back to voltage SOC" % e)
            out = {"ok": False, "packs": [], "generated": formatdate(usegmt=True),
                   "auth_failed": out.get("auth_failed", False)}

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print("[BMS] wrote", args.out)
    else:
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
