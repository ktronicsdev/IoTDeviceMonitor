#!/usr/bin/env python3
"""
Tuya Cloud MCP server - exposes the Tuya OpenAPI to Claude as tools, for the
devices in the Tuya Smart / Smart Life app account linked to an iot.tuya.com
cloud project (plugs, the 3-phase power clamp, radiator TRVs).

Runs over stdio. Signs requests with the cloud project's Access ID + Secret
(HMAC-SHA256, Tuya's "new" signature) - NOT the app login.

Credentials, first match wins:
    1. env TUYA_ACCESS_ID / TUYA_ACCESS_SECRET (and optional TUYA_REGION or TUYA_API_BASE)
    2. the `tuya` block of credentials.json:
       {"tuya": {"access_id": "...", "access_secret": "...", "region": "eu"}}
They are re-read on each call until present, so filling them in needs no restart.

region = the cloud project's data center, which must be the app account's:
eu (Central Europe - Sweden's default), weu (Western Europe), us, ueaz, in, cn, sg.

Read-only by default. send_command is only registered when
TUYA_MCP_ALLOW_CONTROL=1, because it switches real sockets and heaters.

Register (user scope, see memory note on c:/ vs C:/ project keys):
    claude mcp add -s user tuya -- py <repo>/src/main/java/org/ktronics/scripts/tuya_mcp.py
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from config import CREDENTIALS_PATH  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

# CREDENTIALS_PATH is relative to the repo root; the MCP host may start us anywhere.
REPO_ROOT = Path(__file__).resolve().parents[6]

REGIONS = {
    "eu": "https://openapi.tuyaeu.com",
    "weu": "https://openapi-weaz.tuyaeu.com",
    "us": "https://openapi.tuyaus.com",
    "ueaz": "https://openapi-ueaz.tuyaus.com",
    "in": "https://openapi.tuyain.com",
    "cn": "https://openapi.tuyacn.com",
    "sg": "https://openapi-sg.iotbing.com",
}

# Tuya codes for an expired/invalid access token: fetch a new one and retry once.
TOKEN_ERROR_CODES = {1010, 1011, 1012}

# Fields of a device record worth surfacing. local_key, ip and lat/lon are left out:
# the local key alone is enough to control the device on the LAN.
DEVICE_FIELDS = ["id", "name", "category", "product_name", "model", "online", "sub",
                 "time_zone", "active_time", "update_time"]

# Log type 7 = "data reported" (value changes); the free tier keeps about 7 days.
LOG_TYPE_REPORTED = 7


# --------------------------------------------------------------------------- #
# Signing (https://developer.tuya.com/en/docs/iot/new-singnature)
# --------------------------------------------------------------------------- #
def build_url(path: str, query: dict | None = None) -> str:
    """Path plus query sorted by key, as both the request and the signature use it."""
    q = {k: v for k, v in (query or {}).items() if v is not None and v != ""}
    if not q:
        return path
    return path + "?" + "&".join(f"{k}={q[k]}" for k in sorted(q))


def string_to_sign(method: str, url: str, body: bytes = b"",
                   signature_headers: dict | None = None) -> str:
    headers = "".join(f"{k}:{v}\n" for k, v in (signature_headers or {}).items())
    return "\n".join([method.upper(), hashlib.sha256(body).hexdigest(), headers, url])


def sign(access_id: str, secret: str, t: str, sts: str,
         access_token: str = "", nonce: str = "") -> str:
    msg = access_id + access_token + t + nonce + sts
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest().upper()


class TuyaClient:
    """Signed Tuya OpenAPI client with a cached project token (grant_type=1)."""

    def __init__(self, access_id: str, secret: str, base_url: str, timeout: float = 20,
                 session=None):
        self.access_id = access_id
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self._token = ""
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    def _send(self, method: str, url: str, body=None, access_token: str = "") -> dict:
        raw = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
        t = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        headers = {
            "client_id": self.access_id,
            "t": t,
            "nonce": nonce,
            "sign_method": "HMAC-SHA256",
            "sign": sign(self.access_id, self.secret, t,
                         string_to_sign(method, url, raw), access_token, nonce),
        }
        if access_token:
            headers["access_token"] = access_token
        if body is not None:
            headers["Content-Type"] = "application/json"
        resp = self.session.request(method, self.base_url + url, data=raw or None,
                                    headers=headers, timeout=self.timeout)
        try:
            return resp.json()
        except ValueError:
            return {"success": False, "code": resp.status_code, "msg": resp.text[:300]}

    def token(self, force: bool = False) -> str:
        with self._lock:
            if force or not self._token or time.time() > self._token_expiry - 60:
                data = self._send("GET", build_url("/v1.0/token", {"grant_type": 1}))
                if not data.get("success"):
                    raise ToolError(f"Tuya token request failed: {data.get('code')} "
                                    f"{data.get('msg')}")
                result = data["result"]
                self._token = result["access_token"]
                self._token_expiry = time.time() + int(result.get("expire_time", 7200))
            return self._token

    def request(self, method: str, path: str, query: dict | None = None, body=None) -> dict:
        url = build_url(path, query)
        data = self._send(method, url, body, self.token())
        if not data.get("success") and data.get("code") in TOKEN_ERROR_CODES:
            data = self._send(method, url, body, self.token(force=True))
        return data


# --------------------------------------------------------------------------- #
# Value decoding
# --------------------------------------------------------------------------- #
def parse_values(spec_entry: dict) -> dict:
    """Spec 'values' arrive as a JSON string ({"unit":"kW","scale":3,...})."""
    v = spec_entry.get("values")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            v = {}
    return v if isinstance(v, dict) else {}


def scale_value(value, spec_entry: dict | None):
    """Integer DPs are sent as value * 10**scale; undo that."""
    if not spec_entry or spec_entry.get("type") != "Integer":
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    scale = int(parse_values(spec_entry).get("scale") or 0)
    return round(value / 10 ** scale, scale) if scale else value


# The PC321-Z-TY clamp sends a negative phase power (reverse flow) as
# 0x999999 - |W| in its 3 power bytes, which Tuya's cloud passes on as ~10066 kW.
# Checked 2026-10-20: with this rule A+B+C matched power_total to a median 5 W
# over 208 logged samples; two's complement missed by thousands of kW.
NEGATIVE_POWER_BASE = 0x999999


def signed_power_w(raw_w: int) -> int:
    return -(NEGATIVE_POWER_BASE - raw_w) if raw_w >= 0x800000 else raw_w


def decode_phase(value: str) -> dict | None:
    """3-phase meters (category zndb) report phase_a/b/c either as 8 raw bytes in
    base64 (logs: voltage 2 B in 0.1 V, current 3 B in mA, power 3 B in W) or as
    JSON the cloud already decoded (status). Both come back signed, in V/A/kW."""
    if not isinstance(value, str):
        return None
    if value.startswith("{"):
        try:
            j = json.loads(value)
            return {"voltage_v": j["voltage"], "current_a": j["electricCurrent"],
                    "power_kw": signed_power_w(round(j["power"] * 1000)) / 1000}
        except (ValueError, KeyError, TypeError):
            return None
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None
    if len(raw) < 8:
        return None
    return {
        "voltage_v": int.from_bytes(raw[0:2], "big") / 10,
        "current_a": int.from_bytes(raw[2:5], "big") / 1000,
        "power_kw": signed_power_w(int.from_bytes(raw[5:8], "big")) / 1000,
    }


# Codes a CT gain applies to: current, power and the energy integrated from them.
GAIN_CODES = {"power_total", "forward_energy_total", "reverse_energy_total",
              "cur_power", "cur_current", "add_ele"}


def apply_gain(code: str, value, gain: float):
    """Correct CTs that read low or high by a fixed factor, fitted against the grid
    meter. Voltage is left alone; current, power and energy scale with the CT."""
    if gain == 1:
        return value
    if isinstance(value, dict) and "power_kw" in value:
        return {**value, "current_a": round(value["current_a"] * gain, 3),
                "power_kw": round(value["power_kw"] * gain, 3)}
    if code in GAIN_CODES and isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(value * gain, 3)
    return value


def decode_value(code: str, value, spec: dict, gain: float = 1.0):
    if code and code.startswith("phase_"):
        decoded = decode_phase(value)
        if decoded:
            return apply_gain(code, decoded, gain)
    return apply_gain(code, scale_value(value, spec.get(code)), gain)


def decode_status(status: list, spec: dict, gain: float = 1.0) -> list:
    rows = []
    for s in status or []:
        code, value = s.get("code"), s.get("value")
        entry = spec.get(code)
        row = {"code": code, "value": decode_value(code, value, spec, gain)}
        unit = parse_values(entry).get("unit") if entry else None
        if unit:
            row["unit"] = unit
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Time helpers - the laptop's local time (Sweden); no tzdata on this Python.
# --------------------------------------------------------------------------- #
def fmt_time(ts) -> str | None:
    """Tuya mixes epoch seconds (device records) and milliseconds (logs)."""
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return None
    if ts > 1e11:
        ts /= 1000
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def parse_local(s: str) -> datetime:
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), f)
        except ValueError:
            pass
    raise ToolError(f"bad time {s!r}: use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' (local)")


# --------------------------------------------------------------------------- #
# Config and shared calls
# --------------------------------------------------------------------------- #
def _load_cfg() -> dict:
    cfg = {}
    path = REPO_ROOT / CREDENTIALS_PATH
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f).get("tuya") or {}
        except (OSError, json.JSONDecodeError):
            cfg = {}
    cfg["access_id"] = os.environ.get("TUYA_ACCESS_ID") or cfg.get("access_id")
    cfg["access_secret"] = os.environ.get("TUYA_ACCESS_SECRET") or cfg.get("access_secret")
    region = (os.environ.get("TUYA_REGION") or cfg.get("region") or "eu").lower()
    cfg["api_base"] = (os.environ.get("TUYA_API_BASE") or cfg.get("api_base")
                       or REGIONS.get(region))
    if not cfg["api_base"]:
        raise ToolError(f"unknown Tuya region {region!r}; use one of {sorted(REGIONS)}")
    return cfg


_client = None
_specs: dict = {}


def client() -> TuyaClient:
    global _client
    if _client is None:
        cfg = _load_cfg()
        if not cfg.get("access_id") or not cfg.get("access_secret"):
            raise ToolError(
                "Tuya cloud key missing. Set TUYA_ACCESS_ID/TUYA_ACCESS_SECRET or fill "
                "tuya.access_id/access_secret (and region) in credentials.json "
                "(iot.tuya.com -> Cloud -> your project -> Overview).")
        _client = TuyaClient(cfg["access_id"], cfg["access_secret"], cfg["api_base"])
    return _client


def _call(method: str, path: str, query: dict | None = None, body=None):
    data = client().request(method, path, query, body)
    if not data.get("success"):
        hint = ""
        if data.get("code") in (28841101, 28841105):
            hint = " (no permission: authorize this API service on the cloud project)"
        elif data.get("code") == 1106:
            hint = " (device not in the linked app account, or the link is read-only)"
        raise ToolError(f"{path} failed: {data.get('code')} {data.get('msg')}{hint}")
    return data.get("result")


def _power_gain(device_id: str) -> float:
    """Per-device CT correction: tuya.power_gain {device_id: factor} in credentials.json."""
    try:
        return float((_load_cfg().get("power_gain") or {}).get(device_id, 1))
    except (TypeError, ValueError):
        return 1.0


def _spec(device_id: str) -> dict:
    """code -> spec entry (status first, functions fill the gaps), cached per device."""
    if device_id not in _specs:
        result = _call("GET", f"/v1.0/iot-03/devices/{device_id}/specification") or {}
        merged = {f["code"]: f for f in result.get("functions") or [] if "code" in f}
        merged.update({s["code"]: s for s in result.get("status") or [] if "code" in s})
        _specs[device_id] = merged
    return _specs[device_id]


def _device_summary(d: dict) -> dict:
    row = {k: d[k] for k in DEVICE_FIELDS if k in d}
    for k in ("active_time", "update_time"):
        if k in row:
            row[k] = fmt_time(row[k])
    return row


mcp = MCPServer(
    name="tuya",
    instructions=(
        "Tuya Cloud for the user's own home devices in Sweden (Tuya Smart app, project "
        "'Vall home'): plugs, TRVs, thermostats, smoke detectors, RCBO breakers and a "
        "3-phase PC321 Power Clamp at the grid connection. Times are the laptop's local "
        "time. Status values are scaled to their units, and corrected by a device's CT gain "
        "when credentials.json sets one (the result then says 'calibration'). The clamp's "
        "phase_a/b/c power is signed - negative means reverse flow on that phase - and "
        "power_total is their sum; its forward_energy_total adds |P| of every phase, so it "
        "counts export as use. An offline device's status is its last report, not live. "
        "Start with list_devices to get ids."),
)


@mcp.tool()
def list_devices(name_contains: str = "", online_only: bool = False) -> list:
    """Every device in the linked app account(s): id, name, category, product,
    online. Pass the id to the other tools. Local keys are never returned."""
    devices, last_row_key = [], ""
    for _ in range(20):
        result = _call("GET", "/v1.0/iot-01/associated-users/devices",
                       {"size": 50, "last_row_key": last_row_key}) or {}
        devices += result.get("devices") or []
        last_row_key = result.get("last_row_key") or ""
        if not result.get("has_more") or not last_row_key:
            break
    needle = name_contains.lower()
    return [_device_summary(d) for d in devices
            if needle in (d.get("name") or "").lower()
            and (d.get("online") or not online_only)]


@mcp.tool()
def device_status(device_id: str, raw: bool = False) -> dict:
    """Current values of one device, scaled with units: a plug's switch_1 and
    cur_power, a TRV's temp_current and temp_set, the power clamp's energy totals
    and per-phase voltage/current/power. raw=True returns Tuya's values untouched."""
    info = _call("GET", f"/v1.0/devices/{device_id}") or {}
    out = _device_summary(info)
    status = info.get("status") or []
    if raw:
        out["status"] = status
        return out
    try:
        spec = _spec(device_id)
    except ToolError as e:
        spec, out["note"] = {}, f"values unscaled - specification unavailable: {e}"
    gain = _power_gain(device_id)
    out["status"] = decode_status(status, spec, gain)
    if gain != 1:
        out["calibration"] = (f"current, power and energy x{gain:g} (tuya.power_gain in "
                              "credentials.json); raw=True gives the device's own values")
    return out


@mcp.tool()
def device_spec(device_id: str) -> dict:
    """What a device reports (status) and accepts (functions): each code with its
    type and unit, range, scale or enum values. Read this before send_command."""
    result = _call("GET", f"/v1.0/iot-03/devices/{device_id}/specification") or {}

    def rows(entries):
        return [{"code": e.get("code"), "type": e.get("type"), "values": parse_values(e)}
                for e in entries or []]

    return {"category": result.get("category"), "status": rows(result.get("status")),
            "functions": rows(result.get("functions"))}


@mcp.tool()
def device_logs(device_id: str, codes: str = "", start: str = "", end: str = "",
                hours: float = 24, max_rows: int = 500) -> dict:
    """History of reported values (Tuya keeps about 7 days). start/end are local
    'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'; without start, the last `hours`. codes is
    an optional comma list (e.g. 'cur_power,switch_1'). Values are scaled like
    device_status. Oldest first."""
    end_dt = parse_local(end) if end else datetime.now()
    start_dt = parse_local(start) if start else end_dt - timedelta(hours=hours)
    wanted = {c.strip() for c in codes.split(",") if c.strip()}
    spec = _spec(device_id)
    gain = _power_gain(device_id)
    rows, row_key = [], ""
    while len(rows) < max_rows:
        result = _logs_page(device_id, {
            "type": LOG_TYPE_REPORTED, "query_type": 1, "size": 100,
            "codes": ",".join(sorted(wanted)),
            "start_time": int(start_dt.timestamp() * 1000),
            "end_time": int(end_dt.timestamp() * 1000),
            "start_row_key": row_key}) or {}
        for log in result.get("logs") or []:
            code = log.get("code")
            if wanted and code not in wanted:
                continue
            rows.append({"time": fmt_time(log.get("event_time")), "code": code,
                         "value": decode_value(code, _typed(log.get("value"), spec.get(code)),
                                               spec, gain)})
        row_key = result.get("next_row_key") or ""
        if not result.get("has_next") or not row_key:
            break
    rows.sort(key=lambda r: r["time"] or "")
    return {"from": start_dt.strftime("%Y-%m-%d %H:%M"), "to": end_dt.strftime("%Y-%m-%d %H:%M"),
            "rows": rows[-max_rows:], "truncated": len(rows) > max_rows}


LOG_TOO_FREQUENT = 40000309


def _logs_page(device_id: str, query: dict):
    """The log API throttles bursts (40000309): pace pages and back off on it."""
    for attempt in range(4):
        time.sleep(0.4 if attempt == 0 else 3 * attempt)
        data = client().request("GET", f"/v1.0/devices/{device_id}/logs", query)
        if data.get("code") != LOG_TOO_FREQUENT:
            break
    if not data.get("success"):
        raise ToolError(f"device logs failed: {data.get('code')} {data.get('msg')}")
    return data.get("result")


def _typed(value, spec_entry):
    """Log values arrive as strings; turn Integer/Boolean ones back into numbers."""
    if not isinstance(value, str) or not spec_entry:
        return value
    if spec_entry.get("type") == "Integer":
        try:
            return int(value)
        except ValueError:
            return value
    if spec_entry.get("type") == "Boolean":
        return value.lower() == "true"
    return value


STAT_FORMATS = {
    "hours": ("start_hour", "end_hour", "%Y%m%d%H"),
    "days": ("start_day", "end_day", "%Y%m%d"),
    "months": ("start_month", "end_month", "%Y%m"),
}


STATS_NOT_SUBSCRIBED = 28841101
# Energy counters, in order of preference: cumulative meter readings, then the
# per-report increments plugs send.
CUMULATIVE_ENERGY_CODES = ["forward_energy_total", "total_forward_energy"]
INCREMENT_ENERGY_CODES = ["add_ele"]
BUCKET_FORMATS = {"hours": "%Y-%m-%d %H:00", "days": "%Y-%m-%d"}


def bucket_energy(readings: list, granularity: str, cumulative: bool) -> dict:
    """kWh per bucket from (datetime, value) readings, oldest first. A cumulative
    counter's rise between two readings counts in the later reading's bucket;
    drops (counter resets) are skipped."""
    fmt = BUCKET_FORMATS[granularity]
    buckets: dict = {}
    prev = None
    for t, v in readings:
        if cumulative:
            if prev is not None and v >= prev:
                key = t.strftime(fmt)
                buckets[key] = buckets.get(key, 0) + (v - prev)
            prev = v
        else:
            key = t.strftime(fmt)
            buckets[key] = buckets.get(key, 0) + v
    return {k: round(v, 3) for k, v in buckets.items()}


def _energy_from_logs(device_id: str, granularity: str, s: datetime, e: datetime,
                      code: str) -> dict:
    if granularity not in BUCKET_FORMATS:
        raise ToolError("statistics API not subscribed on the cloud project, and device "
                        "logs only reach back ~7 days - use hours or days")
    spec = _spec(device_id)
    if not code:
        code = next((c for c in CUMULATIVE_ENERGY_CODES + INCREMENT_ENERGY_CODES
                     if c in spec), "")
        if not code:
            raise ToolError("statistics API not subscribed, and this device has no known "
                            f"energy code; its codes: {sorted(spec)}")
    end = e + (timedelta(hours=1) if granularity == "hours" else timedelta(days=1))
    end = min(end, datetime.now())
    rows = device_logs(device_id, codes=code, start=s.strftime("%Y-%m-%d %H:%M"),
                       end=end.strftime("%Y-%m-%d %H:%M"), max_rows=20000)["rows"]
    readings = [(datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S"), r["value"])
                for r in rows if isinstance(r["value"], (int, float))]
    buckets = bucket_energy(readings, granularity, code not in INCREMENT_ENERGY_CODES)
    result = {"code": code, "granularity": granularity, "unit": "kWh",
              "source": "device logs (statistics API not subscribed; logs keep ~7 days)"}
    if code == "forward_energy_total":
        result["note"] = ("forward_energy_total adds |P| of each phase, so it counts reverse "
                          "flow (export) as use - it is not the grid meter's import")
    gain = _power_gain(device_id)
    if gain != 1:
        result["calibration"] = f"x{gain:g} (tuya.power_gain in credentials.json)"
    return {**result,
            "readings": len(readings),
            "first": rows[0]["time"] if rows else None,
            "last": rows[-1]["time"] if rows else None,
            "buckets": buckets, "total": round(sum(buckets.values()), 3)}


@mcp.tool()
def energy_stats(device_id: str, granularity: str = "days", start: str = "", end: str = "",
                 code: str = "") -> dict:
    """Energy per hour, day or month, like the app's bar chart. granularity: hours
    (default today), days (default last 7 days) or months (default this year).
    start/end are local 'YYYY-MM-DD'. code defaults to the device's energy counter.
    Without the statistics API service on the cloud project (the case for 'Vall
    home'), hours and days are summed from the device logs, which keep ~7 days;
    a week of the power clamp takes about a minute to fetch."""
    if granularity not in STAT_FORMATS:
        raise ToolError(f"granularity must be one of {sorted(STAT_FORMATS)}")
    today = date.today()
    if granularity == "hours":
        s = parse_local(start) if start else datetime(today.year, today.month, today.day)
        e = parse_local(end) + timedelta(hours=23) if end else s + timedelta(hours=23)
    elif granularity == "days":
        e = parse_local(end) if end else datetime.combine(today, datetime.min.time())
        s = parse_local(start) if start else e - timedelta(days=6)
    else:
        e = parse_local(end) if end else datetime.combine(today, datetime.min.time())
        s = parse_local(start) if start else datetime(today.year, 1, 1)
    try:
        types = _call("GET", f"/v1.0/devices/{device_id}/all-statistic-type") or []
    except ToolError as err:
        if str(STATS_NOT_SUBSCRIBED) not in str(err):
            raise
        return _energy_from_logs(device_id, granularity, s, e, code)
    if not code:
        sums = [t.get("code") for t in types if t.get("stat_type") == "sum"]
        if not sums:
            raise ToolError(f"no sum-type statistic on this device; types: {types}")
        code = next((c for c in sums if "ele" in c or "energy" in c), sums[0])
    k_start, k_end, f = STAT_FORMATS[granularity]
    result = _call("GET", f"/v1.0/devices/{device_id}/statistics/{granularity}", {
        "code": code, "stat_type": "sum", k_start: s.strftime(f), k_end: e.strftime(f)})
    return {"code": code, "granularity": granularity, "available": types, "result": result}


if os.environ.get("TUYA_MCP_ALLOW_CONTROL") == "1":
    @mcp.tool()
    def send_command(device_id: str, code: str, value: str) -> dict:
        """SWITCHES a real device. value is JSON: true/false, a number or a "string".
        Integer values are raw, before scale (20.5 C with scale 1 is 205). Check
        device_spec for the code and range, and confirm with the user first."""
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = value
        result = _call("POST", f"/v1.0/iot-03/devices/{device_id}/commands",
                       body={"commands": [{"code": code, "value": parsed}]})
        return {"sent": {"code": code, "value": parsed}, "result": result}


if __name__ == "__main__":
    mcp.run()
