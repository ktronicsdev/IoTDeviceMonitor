#!/usr/bin/env python3
"""
SolisCloud MCP server - exposes the SolisCloud open API to Claude as tools.

Runs over stdio. Reuses the signed client in solis_common.py (HMAC-SHA1,
API Key ID + Secret - NOT the web login).

Credentials, first match wins:
    1. env SOLIS_KEY_ID / SOLIS_KEY_SECRET (and optional SOLIS_API_BASE)
    2. the `solis` block of credentials.json (see solis/README_SOLIS.md)

Read-only by default. The one write tool (set_control_register) is only
registered when SOLIS_MCP_ALLOW_CONTROL=1, because a wrong cid can change an
unrelated inverter setting.

Register (user scope, see memory note on c:/ vs C:/ project keys):
    claude mcp add -s user solis -- py <repo>/src/main/java/org/ktronics/scripts/solis_mcp.py
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import CREDENTIALS_PATH  # noqa: E402
from solis_common import DEFAULT_BASE_URL, SolisClient, is_success  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

# CREDENTIALS_PATH is relative to the repo root; the MCP host may start us anywhere.
REPO_ROOT = Path(__file__).resolve().parents[6]

# SolisCloud allows ~2 requests/second per key; stay under it.
MIN_REQUEST_GAP_S = 0.6

# inverterDetail fields worth surfacing for a hybrid (battery) inverter.
DETAIL_FIELDS = [
    "sn", "stationName", "state", "dataTimestamp",
    "pac", "pacStr", "eToday", "eTotal",
    "batteryCapacitySoc", "batteryHealthSoh", "batteryPower", "batteryPowerStr",
    "batteryTodayChargeEnergy", "batteryTodayDischargeEnergy",
    "psum", "psumStr", "gridPurchasedTodayEnergy", "gridSellTodayEnergy",
    "familyLoadPower", "familyLoadPowerStr", "homeLoadTodayEnergy",
    "bypassLoadPower", "bypassLoadPowerStr",
    "uAc1", "iAc1", "fac", "inverterTemperature",
]

# inverterDay points carry ~200 fields each (288 points/day); keep the useful ones.
DAY_POINT_FIELDS = [
    "timeStr", "pac", "eToday", "batteryCapacitySoc", "batteryPower",
    "psum", "familyLoadPower", "bypassLoadPower",
]


def _load_cfg() -> dict:
    cfg = {}
    path = REPO_ROOT / CREDENTIALS_PATH
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f).get("solis") or {}
        except (OSError, json.JSONDecodeError):
            cfg = {}
    cfg["key_id"] = os.environ.get("SOLIS_KEY_ID") or cfg.get("key_id")
    cfg["key_secret"] = os.environ.get("SOLIS_KEY_SECRET") or cfg.get("key_secret")
    cfg["api_base"] = os.environ.get("SOLIS_API_BASE") or cfg.get("api_base") or DEFAULT_BASE_URL
    return cfg


class ThrottledClient(SolisClient):
    """SolisClient that spaces requests to respect the per-key rate limit."""

    _lock = threading.Lock()
    _last = 0.0

    def post(self, resource, payload):
        with self._lock:
            wait = MIN_REQUEST_GAP_S - (time.monotonic() - ThrottledClient._last)
            if wait > 0:
                time.sleep(wait)
            ThrottledClient._last = time.monotonic()
        return super().post(resource, payload)


CFG = _load_cfg()
TZ_MINUTES = int(CFG.get("tz_offset_minutes", 330))
_client = None


def client() -> SolisClient:
    global _client
    if _client is None:
        if not CFG.get("key_id") or not CFG.get("key_secret"):
            raise ToolError(
                "SolisCloud API key missing. Set SOLIS_KEY_ID/SOLIS_KEY_SECRET or fill "
                "solis.key_id/key_secret in credentials.json "
                "(SolisCloud -> Service -> API Management).")
        _client = ThrottledClient(CFG["key_id"], CFG["key_secret"], base_url=CFG["api_base"])
    return _client


def _call(resource: str, payload: dict) -> dict:
    resp = client().post(resource, payload)
    if not is_success(resp):
        raise ToolError(f"{resource} failed: {resp.get('msg') or resp.get('code') or resp}")
    return resp.get("data")


def _records(data) -> list:
    """List endpoints nest records as data.page.records (sometimes data.records)."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    return (data.get("page") or {}).get("records") or data.get("records") or []


def _local_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=TZ_MINUTES)


def _tz_hours():
    h = TZ_MINUTES / 60
    return int(h) if h == int(h) else h


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _age_minutes(data_timestamp):
    ts = _as_float(data_timestamp)
    if ts is None:
        return None
    return round((time.time() - ts / 1000) / 60, 1)


def _pick(d: dict, fields) -> dict:
    return {k: d[k] for k in fields if k in d}


def _id_or_sn(inverter_id, sn) -> dict:
    if not inverter_id and not sn:
        raise ToolError("pass inverter_id or sn")
    payload = {}
    if inverter_id:
        payload["id"] = str(inverter_id)
    if sn:
        payload["sn"] = str(sn)
    return payload


mcp = MCPServer(
    name="solis",
    instructions=(
        "SolisCloud (Ginlong) monitoring for Ktronics plants. Times are local "
        f"(UTC{TZ_MINUTES / 60:+g}). A station (plant) can hold several independent "
        "inverters, e.g. one per floor - compare inverters, do not trust station totals "
        "alone. Stale dataTimestamp or null fields usually mean a logger/comms fault."),
)


@mcp.tool()
def list_stations(page_no: int = 1, page_size: int = 100) -> list:
    """List plants (stations) on the account: id, name, capacity, today's energy, state."""
    data = _call("/v1/api/userStationList", {"pageNo": page_no, "pageSize": page_size})
    keep = ["id", "stationName", "capacity", "capacityStr", "dayEnergy", "dayEnergyStr",
            "power", "powerStr", "state", "inverterCount", "dataTimestamp", "addr"]
    return [_pick(r, keep) for r in _records(data)]


@mcp.tool()
def station_detail(station_id: str) -> dict:
    """Full detail for one plant (station) by its id."""
    return _call("/v1/api/stationDetail", {"id": str(station_id)})


@mcp.tool()
def list_inverters(station_id: str = "", page_no: int = 1, page_size: int = 100) -> list:
    """List inverters (optionally for one station): id, sn, name, station, state, power, today."""
    payload = {"pageNo": page_no, "pageSize": page_size}
    if station_id:
        payload["stationId"] = str(station_id)
    keep = ["id", "sn", "name", "stationId", "stationName", "state", "pac", "pacStr",
            "etoday", "etodayStr", "power", "dataTimestamp", "collectorSn"]
    return [_pick(r, keep) for r in _records(_call("/v1/api/inverterList", payload))]


@mcp.tool()
def inverter_detail(inverter_id: str = "", sn: str = "", raw: bool = False) -> dict:
    """Live state of one inverter: power, today's yield, battery SOC/power, grid, load,
    per-MPPT DC strings, and data age in minutes. raw=True returns every field."""
    data = _call("/v1/api/inverterDetail", _id_or_sn(inverter_id, sn)) or {}
    if raw:
        return data
    out = _pick(data, DETAIL_FIELDS)
    out["data_age_min"] = _age_minutes(data.get("dataTimestamp"))
    strings = []
    for n in range(1, 33):
        u, i = data.get(f"uPv{n}"), data.get(f"iPv{n}")
        if u is None and i is None:
            continue
        strings.append({"mppt": n, "V": u, "A": i, "W": data.get(f"pow{n}")})
    out["mppt"] = strings
    return out


@mcp.tool()
def inverter_energy(period: str, date: str = "", inverter_id: str = "", sn: str = "",
                    money: str = "LKR") -> list:
    """Inverter history. period: 'day' (5-min points, date YYYY-MM-DD), 'month'
    (daily totals, date YYYY-MM) or 'year' (monthly totals, date YYYY).
    date defaults to the current local day/month/year."""
    payload = {**_id_or_sn(inverter_id, sn), "money": money}
    return _energy("inverter", period, date, payload)


@mcp.tool()
def station_energy(station_id: str, period: str, date: str = "", money: str = "LKR") -> list:
    """Plant-level history, same periods as inverter_energy. Station totals hide a
    dead inverter - use compare_inverters for multi-inverter plants."""
    return _energy("station", period, date, {"id": str(station_id), "money": money})


def _energy(kind: str, period: str, date: str, payload: dict) -> list:
    now = _local_now()
    if period == "day":
        payload.update(time=date or now.strftime("%Y-%m-%d"), timeZone=_tz_hours())
        data = _call(f"/v1/api/{kind}Day", payload)
        return [_pick(p, DAY_POINT_FIELDS) for p in (data or [])]
    if period == "month":
        payload["month"] = date or now.strftime("%Y-%m")
        return _call(f"/v1/api/{kind}Month", payload) or []
    if period == "year":
        payload["year"] = date or now.strftime("%Y")
        return _call(f"/v1/api/{kind}Year", payload) or []
    raise ToolError("period must be 'day', 'month' or 'year'")


@mcp.tool()
def alarms(station_id: str = "", device_sn: str = "", begin: str = "", end: str = "",
           page_no: int = 1, page_size: int = 100) -> list:
    """Alarms, optionally filtered by station or device SN. begin/end are YYYY-MM-DD
    (default: last 7 days)."""
    today = _local_now().date()
    payload = {
        "pageNo": page_no, "pageSize": page_size,
        "alarmBeginTime": begin or str(today - timedelta(days=7)),
        "alarmEndTime": end or str(today),
    }
    if station_id:
        payload["stationId"] = str(station_id)
    if device_sn:
        payload["alarmDeviceSn"] = str(device_sn)
    keep = ["stationName", "alarmDeviceSn", "alarmCode", "alarmMsg", "alarmLevel",
            "alarmBeginTime", "alarmEndTime", "state", "advice"]
    return [_pick(r, keep) for r in _records(_call("/v1/api/alarmList", payload))]


@mcp.tool()
def compare_inverters(station_id: str, stale_after_min: int = 30) -> dict:
    """Health check for a plant with several independent inverters (e.g. one per floor).
    Returns each inverter's live power, today's yield, battery SOC/power, data age,
    and flags: stale data, missing values, or yield far below the best sibling."""
    invs = list_inverters(station_id=station_id)
    rows = []
    for inv in invs:
        try:
            d = inverter_detail(inverter_id=inv.get("id", ""), sn=inv.get("sn", ""))
        except ToolError as e:
            rows.append({"sn": inv.get("sn"), "name": inv.get("name"), "error": str(e)})
            continue
        rows.append({
            "sn": d.get("sn") or inv.get("sn"), "name": inv.get("name"),
            "state": d.get("state"), "pac": d.get("pac"), "eToday": d.get("eToday"),
            "soc": d.get("batteryCapacitySoc"), "batteryPower": d.get("batteryPower"),
            "gridPower": d.get("psum"), "data_age_min": d.get("data_age_min"),
        })

    yields = [y for y in (_as_float(r.get("eToday")) for r in rows) if y is not None]
    best = max(yields) if yields else None
    for r in rows:
        flags = []
        if "error" in r:
            flags.append("no data from API")
        else:
            age = r.get("data_age_min")
            if age is None or age > stale_after_min:
                flags.append(f"stale data ({age} min old)")
            if r.get("eToday") is None or r.get("pac") is None:
                flags.append("missing power/yield fields")
            y = _as_float(r.get("eToday"))
            if best and y is not None and best >= 5 and y < 0.5 * best:
                flags.append(f"yield {y:g} kWh < 50% of best sibling ({best:g})")
        r["flags"] = flags
    return {"station_id": station_id, "local_time": _local_now().strftime("%Y-%m-%d %H:%M"),
            "inverters": rows}


@mcp.tool()
def list_control_registers(inverter_id: str) -> dict:
    """Read-only: list readable control registers (cid) for an inverter.
    Needs the Control API permission on the key."""
    return _call("/v2/api/atReadList", {"inverterId": str(inverter_id)})


@mcp.tool()
def read_control_register(inverter_id: str, cid: int) -> dict:
    """Read-only: read one control register (e.g. battery reserve SOC) by cid."""
    return _call("/v2/api/atRead", {"inverterId": str(inverter_id), "cid": int(cid)})


if os.environ.get("SOLIS_MCP_ALLOW_CONTROL") == "1":
    @mcp.tool()
    def set_control_register(inverter_id: str, cid: int, value: str) -> dict:
        """WRITES an inverter setting. Read the register first and confirm with the
        user - a wrong cid changes an unrelated setting."""
        return _call("/v2/api/control",
                     {"inverterId": str(inverter_id), "cid": int(cid), "value": str(value)})


if __name__ == "__main__":
    mcp.run()
