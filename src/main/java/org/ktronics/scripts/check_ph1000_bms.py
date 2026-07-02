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


def decode_cell_frame(hexstr):
    """Decode the 79-byte PACK-cell `WIFI_Band` frame: all 16 cell mV + 4 temps + pack V.

    A DIFFERENT frame type from the summary frame (decode_wifi_band). The datalogger emits it
    while the PACEEX app is on a pack's "Voltage temperature / PACK cell" screen — a *sticky*
    per-device mode that persists after the app leaves, so our HTTP poll keeps reading it.

    Layout validated byte-for-byte against the app: byte[3]==0x0A marks the cell frame,
    byte[11] = cell count (16). Then `n` four-byte records at off=12+4*i: mv=u16(off); the
    FIRST 4 records carry a temperature at u16(off+2) as 0.1 K -> (raw/10 - 273) C (records
    5..16 hold 0000 there). Returns None if it is not a cell frame.
    """
    b = bytes.fromhex(hexstr)
    if len(b) < 12 or b[3] != 0x0A:
        return None
    n = b[11]
    if not (8 <= n <= 24) or len(b) < 12 + 4 * n:
        return None
    u16 = lambda i: (b[i] << 8) | b[i + 1]
    mv, temps = [], []
    for i in range(n):
        off = 12 + 4 * i
        v = u16(off)
        if not (2000 <= v <= 4000):        # plausible LFP cell mV; else this isn't a cell frame
            return None
        mv.append(v)
        aux = u16(off + 2)                 # first 4 records: temp in 0.1 K; rest: 0
        if 2700 <= aux <= 3300:            # ~ -3..57 C -> a real temp sensor
            temps.append(round(aux / 10.0 - 273.0, 1))
    hi_i = max(range(n), key=lambda i: mv[i])
    lo_i = min(range(n), key=lambda i: mv[i])
    out = {
        "mv": mv,
        "temps": temps,
        "pack_v": round(sum(mv) / 1000.0, 2),
        "high": {"n": hi_i + 1, "mv": mv[hi_i]},
        "low": {"n": lo_i + 1, "mv": mv[lo_i]},
        "spread": mv[hi_i] - mv[lo_i],
    }
    if temps:
        tmax_i = max(range(len(temps)), key=lambda i: temps[i])
        tmin_i = min(range(len(temps)), key=lambda i: temps[i])
        out["tmax"] = {"n": tmax_i + 1, "c": temps[tmax_i]}
        out["tmin"] = {"n": tmin_i + 1, "c": temps[tmin_i]}
    return out


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
    hexval = wb.get("value", "")
    reported = int(wb.get("time", 0)) or None
    meta = {"name": pack["name"], "role": pack["role"], "iotId": pack["iotId"]}
    data = decode_wifi_band(hexval)
    if data is not None:
        # `time` = when the pack's datalogger last pushed this frame (epoch ms). Packs report at
        # different rates (B2 lags ~20-30 min), so surface it; the dashboard shows the data's age.
        data["reported_ms"] = reported
        data.update(meta)
        return data
    # Not a summary frame -> no fresh SOC/V this run. It may instead be the sticky CELL frame
    # (app's PACK-cell screen): capture the per-cell block so the Battery tab can show it. The
    # pack is still "offline" for the main SOC pane (which carries its last summary forward).
    marker = {**meta, "offline": True, "reported_ms": reported}
    cells = decode_cell_frame(hexval)
    if cells is not None:
        marker["cell_frame"] = cells
    return marker


# Where to read the previously published data from, for per-pack carry-forward (below).
PREV_URL_DEFAULT = ("https://raw.githubusercontent.com/ktronicsdev/IoTDeviceMonitor/"
                    "ph1000-live/ph1000_live.json")


def _load_prev_bms(url):
    """Fetch the previously published `bms` block (the last-good reading per pack)."""
    if not url:
        return {}
    try:
        with urllib.request.urlopen(url + ("?t=%d" % int(time.time() * 1000)), timeout=15) as r:
            return json.loads(r.read().decode()).get("bms") or {}
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
        print("[BMS] prev-bms fetch failed (%s) -> no carry-forward" % e)
        return {}


def carry_forward_packs(out, prev_bms):
    """Keep both packs on the dashboard across heartbeat-only cycles.

    The datalogger pushes the full SUMMARY frame (SOC/voltage/temps) only intermittently;
    between summaries it sends short heartbeat frames that carry no telemetry, so a pack's
    cached property is often a heartbeat at fetch time. Without this, that pack vanishes from
    the dashboard until the next summary happens to be cached. Instead, for any pack with no
    fresh summary THIS run, reuse its last-good summary from the previously published data —
    tagged `carried`, keeping its original `reported_ms` so the dashboard shows the true age
    and flags it STALE. Skipped when the cloud session is dead (auth_failed) so a genuine
    re-login need still surfaces. Mutates and returns `out`.
    """
    if out.get("auth_failed"):
        return out
    have = {p.get("iotId") for p in out["packs"]}
    for pack in PACKS:
        if pack["iotId"] in have:
            continue
        old = next((q for q in (prev_bms.get("packs") or [])
                    if q.get("iotId") == pack["iotId"] and q.get("soc") is not None), None)
        if not old:
            continue
        old = dict(old)
        old["carried"] = True
        out["packs"].append(old)
        if old.get("name") in out.get("offline_packs", []):
            out["offline_packs"].remove(old["name"])   # shown from cache, not "offline"
        print("[BMS] %s: no fresh summary -> carried forward last-good (SOC %s%%)"
              % (old.get("name"), old.get("soc")))
    out["ok"] = len(out["packs"]) > 0
    rep = [q["reported_ms"] for q in out["packs"] if q.get("reported_ms")]
    if rep:
        out["freshest_ms"] = max(rep)
        out["data_stale_min"] = max(0, int((time.time() * 1000 - max(rep)) / 60000))
    return out


def attach_cells(out, cell_updates, prev_bms):
    """Attach a per-pack `cells` block (16 mV + temps + pack V) for the Battery B1/B2 tabs.

    Independent of the summary/SOC layer: `cells` and the summary fields come from DIFFERENT
    (mutually-exclusive) frame types, so each is carried forward on its own timeline. For every
    pack now in `out["packs"]`, attach this run's fresh cells if we caught a cell frame, else the
    last-good cells from the previously published data (kept with their own `ts` so the frontend
    can STALE-gate them). A pack emitting cell frames is in cell mode, not offline -> drop it from
    `offline_packs`. Mutates `out`.
    """
    prev = {q.get("iotId"): q for q in (prev_bms.get("packs") or [])}
    have = {p.get("iotId") for p in out["packs"]}
    for pk in out["packs"]:
        iid = pk.get("iotId")
        cells = cell_updates.get(iid)
        if cells is None:
            cells = (prev.get(iid) or {}).get("cells")
        if cells is not None:
            pk["cells"] = cells
            if pk.get("name") in out.get("offline_packs", []):
                out["offline_packs"].remove(pk["name"])   # cell mode, not offline
    # Cells-only fallback: a pack in cell mode with no summary anywhere (fresh or prev) isn't in
    # out["packs"] yet -> add a minimal pack so the Battery tab can still show its cells. It has no
    # SOC, so the main BMS pane skips it (renderBMS filters on soc).
    for pack in PACKS:
        iid = pack["iotId"]
        if iid in have or iid not in cell_updates:
            continue
        out["packs"].append({"name": pack["name"], "role": pack["role"], "iotId": iid,
                             "cells_only": True, "cells": cell_updates[iid]})
        if pack["name"] in out.get("offline_packs", []):
            out["offline_packs"].remove(pack["name"])
    out["ok"] = len(out["packs"]) > 0
    return out


def main():
    ap = argparse.ArgumentParser(description="PH1000 BMS fetcher (Hystorix/PACEEX)")
    ap.add_argument("--out", help="write the bms JSON block here")
    ap.add_argument("--token", default=os.environ.get("BMS_IOT_TOKEN", ""))
    ap.add_argument("--rotated-out", dest="rotated_out",
                    help="if the refreshToken rotated, write {refreshToken,identityId} JSON here "
                         "(the workflow persists it back into the GitHub secret — cloud self-renewal)")
    ap.add_argument("--prev-url", dest="prev_url",
                    default=os.environ.get("BMS_PREV_URL", PREV_URL_DEFAULT),
                    help="URL of the previously published ph1000_live.json; any pack that only "
                         "heartbeats this run is carried forward from it. Empty string disables.")
    args = ap.parse_args()

    # auth_failed = the cloud SESSION is dead (refreshToken rejected) and needs a one-time app
    # re-login. Distinct from a pack being merely offline (ok:false but session fine). The
    # workflow alerts the operator only on auth_failed -- the rare event a human must act on.
    out = {"ok": False, "packs": [], "generated": formatdate(usegmt=True), "auth_failed": False}
    cell_updates = {}   # iotId -> fresh per-cell block (populated if a pack is in cell mode)

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
            reported = []
            for p in PACKS:
                d = fetch_pack(p, token)
                if d.get("reported_ms"):
                    reported.append(d["reported_ms"])
                if d.get("offline"):
                    if d.get("cell_frame"):
                        # Pack is in cell mode: no fresh SOC, but we captured the 16-cell block.
                        cf = dict(d["cell_frame"]); cf["ts"] = d.get("reported_ms")
                        cell_updates[d["iotId"]] = cf
                        print("[BMS] %s: cell frame -> %d cells, pack %.2fV (Battery tab)"
                              % (d["name"], len(cf["mv"]), cf["pack_v"]))
                    else:
                        print("[BMS] %s: datalogger offline (no fresh summary frame) -> skipped"
                              % d["name"])
                    # Either way, no summary this run -> carried forward below. Self-heals when a
                    # summary frame is next cached.
                    out.setdefault("offline_packs", []).append(d["name"])
                    continue
                out["packs"].append(d)
                temp = ("%.1f/%.1f" % (d["max_temp"], d["min_temp"])
                        if d["max_temp"] is not None else "n/a")
                print("[BMS] %s: SOC %d%% %.2fV %.2fA %s temp %s cyc %d"
                      % (d["name"], d["soc"], d["voltage"], d["current"], d["state"],
                         temp, d["cycles"]))
            out["ok"] = len(out["packs"]) > 0
            # Freshness: even with a HEALTHY session, the physical datalogger at the plant can
            # stop pushing (lost power / wifi). Surface the age of the newest frame so the
            # workflow can alert on a stale device -- a different problem from a dead session.
            if reported:
                freshest = max(reported)
                out["freshest_ms"] = freshest
                out["data_stale_min"] = max(0, int((time.time() * 1000 - freshest) / 60000))
                print("[BMS] newest frame is %d min old" % out["data_stale_min"])
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, RuntimeError) as e:
            # token expired / auth failed -> ok stays False; dashboard hides the pane.
            print("[BMS] fetch failed (token expired?): %s -> dashboard falls back to voltage SOC" % e)
            out = {"ok": False, "packs": [], "generated": formatdate(usegmt=True),
                   "auth_failed": out.get("auth_failed", False)}

    # Carry forward from the previously published data (loaded once). Two independent layers:
    #  1) summary/SOC for packs with no fresh summary this run (heartbeat or cell mode);
    #  2) the per-cell block for the Battery tabs (fresh this run, else last-good).
    if args.prev_url:
        prev_bms = _load_prev_bms(args.prev_url)
        carry_forward_packs(out, prev_bms)
        attach_cells(out, cell_updates, prev_bms)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print("[BMS] wrote", args.out)
    else:
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
