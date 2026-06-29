#!/usr/bin/env python3
"""
PH1000 weather + solar-harvest-impact fetcher (Open-Meteo).

Adds a `weather` block to the dashboard JSON so the page can show the live sky
condition (clear / cloudy / rain), ambient temperature and solar irradiance, and —
the useful part — quantify how much the weather is *costing* the harvest.

Why Open-Meteo: free, NO API key (works straight from GitHub Actions), and it returns
both the weather AND the solar irradiance (GHI) that physically drives PV output, in one
call. https://open-meteo.com/en/docs

Harvest impact (flagged estimate, in keeping with this module's PV/load estimates):
  Clear-sky surface GHI is approximated as 0.75 x extraterrestrial (terrestrial_radiation),
  the standard clear-sky transmittance. Per day:
      harvest_factor = actual_GHI / clear_sky_GHI        (1.0 = clear, lower = cloud/rain)
      loss_pct       = 100 * (1 - harvest_factor)        (~harvest lost to weather)
  So a headline like "Cloudy - ~55% of clear-sky harvest (weather cost ~45%)" is possible,
  and the hourly GHI series lets the dashboard overlay irradiance on the PV power curve.

Location: auto-detected from the plant block in ph1000_live.json (plant.lat/plant.lon,
populated by check_ph1000.py from queryPlantInfo); falls back to DEFAULT_LAT/LON if absent
(override with --lat/--lon or env PH1000_LAT/PH1000_LON).

Fails soft (ok=false) like the BMS fetcher, so the dashboard simply hides the weather pane
and keeps working if Open-Meteo is unreachable.

Usage:
    python check_ph1000_weather.py --live-json publish/ph1000_live.json --out publish/weather.json
    python check_ph1000_weather.py --lat 6.93 --lon 79.86 --out weather.json
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

API_URL = "https://api.open-meteo.com/v1/forecast"

# Mifanza site location — Plus Code WVR5+RC9 Colombo, Sri Lanka (decodes to 6.94205, 79.85856).
# Used unless queryPlantInfo carries coords (it currently does not for this plant) or --lat/--lon
# / env override it. ShineMonitor only exposes the country, so in practice this is the value used.
DEFAULT_LAT = float(os.environ.get("PH1000_LAT", "6.94205"))
DEFAULT_LON = float(os.environ.get("PH1000_LON", "79.85856"))

PAST_DAYS = 7          # match the dashboard's series7d window
CLEAR_SKY_FACTOR = 0.75  # clear-sky surface GHI ~= 0.75 x extraterrestrial radiation

HOURLY_VARS = ("temperature_2m,cloud_cover,precipitation,weather_code,"
               "shortwave_radiation,terrestrial_radiation")
CURRENT_VARS = "temperature_2m,cloud_cover,precipitation,weather_code,shortwave_radiation"


# --------------------------------------------------------------------------- #
# WMO weather code -> human label + coarse category (icon hook for the UI)
# --------------------------------------------------------------------------- #
def weather_label(code):
    """(label, category) for a WMO weather code. category in: clear, partly, cloudy,
    fog, drizzle, rain, snow, storm, unknown."""
    try:
        c = int(code)
    except (TypeError, ValueError):
        return ("Unknown", "unknown")
    table = {
        0: ("Clear", "clear"),
        1: ("Mainly clear", "clear"),
        2: ("Partly cloudy", "partly"),
        3: ("Overcast", "cloudy"),
        45: ("Fog", "fog"), 48: ("Rime fog", "fog"),
        51: ("Light drizzle", "drizzle"), 53: ("Drizzle", "drizzle"), 55: ("Heavy drizzle", "drizzle"),
        56: ("Freezing drizzle", "drizzle"), 57: ("Freezing drizzle", "drizzle"),
        61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
        66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
        71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"),
        77: ("Snow grains", "snow"),
        80: ("Light showers", "rain"), 81: ("Showers", "rain"), 82: ("Violent showers", "rain"),
        85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
        95: ("Thunderstorm", "storm"), 96: ("Thunderstorm + hail", "storm"),
        99: ("Thunderstorm + hail", "storm"),
    }
    return table.get(c, ("Unknown", "unknown"))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


# --------------------------------------------------------------------------- #
# Aggregation (pure — unit-tested without network)
# --------------------------------------------------------------------------- #
def aggregate_daily(hourly):
    """Roll Open-Meteo hourly arrays into one record per calendar date.

    `hourly` is the API's `hourly` dict (parallel arrays keyed by time). Returns a list of
    daily dicts sorted by date, each with the condition, cloud %, rain, GHI/clear-sky kWh,
    and the weather-driven harvest loss %. Daylight = hours where extraterrestrial > 0, so
    night-time zeros don't dilute cloud/condition averages.
    """
    times = hourly.get("time", []) or []
    ghi = hourly.get("shortwave_radiation", []) or []
    tr = hourly.get("terrestrial_radiation", []) or []
    cloud = hourly.get("cloud_cover", []) or []
    rain = hourly.get("precipitation", []) or []
    code = hourly.get("weather_code", []) or []

    by_date = defaultdict(lambda: {"ghi_wh": 0.0, "clear_wh": 0.0, "rain": 0.0,
                                   "cloud": [], "codes": []})
    for i, t in enumerate(times):
        date = str(t)[:10]
        d = by_date[date]
        g = _f(ghi[i]) if i < len(ghi) else None
        x = _f(tr[i]) if i < len(tr) else None
        if g is not None:
            d["ghi_wh"] += g                                  # W/m^2 over 1h = Wh/m^2
        if x is not None:
            d["clear_wh"] += x * CLEAR_SKY_FACTOR
        r = _f(rain[i]) if i < len(rain) else None
        if r is not None:
            d["rain"] += r
        daylight = (x or 0) > 0
        if daylight:                                          # only daytime defines "the sky"
            cv = _f(cloud[i]) if i < len(cloud) else None
            if cv is not None:
                d["cloud"].append(cv)
            if i < len(code) and code[i] is not None:
                d["codes"].append(int(code[i]))

    out = []
    for date in sorted(by_date):
        d = by_date[date]
        ghi_kwh = round(d["ghi_wh"] / 1000.0, 2)
        clear_kwh = round(d["clear_wh"] / 1000.0, 2)
        factor = (ghi_kwh / clear_kwh) if clear_kwh > 0 else None
        loss = round(_clamp(100.0 * (1 - factor), 0, 100)) if factor is not None else None
        dom_code = Counter(d["codes"]).most_common(1)[0][0] if d["codes"] else None
        label, cat = weather_label(dom_code)
        out.append({
            "date": date,
            "code": dom_code,
            "condition": label,
            "category": cat,
            "cloud_pct": round(sum(d["cloud"]) / len(d["cloud"])) if d["cloud"] else None,
            "rain_mm": round(d["rain"], 1),
            "ghi_kwh_m2": ghi_kwh,
            "clear_kwh_m2": clear_kwh,
            "harvest_factor": round(factor, 3) if factor is not None else None,
            "loss_pct": loss,
        })
    return out


def hourly_ghi_series(hourly):
    """Per-hour weather series for the dashboard (local time):
    [{timestamp:'YYYY-MM-DD HH:MM', ghi:W/m^2, cloud:%, rain:mm}]. `ghi` drives the Power-Profile
    overlay; `cloud`/`rain` feed the Weather-vs-Solar-Harvest chart. Rows without GHI are skipped."""
    times = hourly.get("time", []) or []
    ghi = hourly.get("shortwave_radiation", []) or []
    cloud = hourly.get("cloud_cover", []) or []
    rain = hourly.get("precipitation", []) or []
    series = []
    for i, t in enumerate(times):
        g = _f(ghi[i]) if i < len(ghi) else None
        if g is None:
            continue
        entry = {"timestamp": str(t).replace("T", " ")[:16], "ghi": round(g)}
        cv = _f(cloud[i]) if i < len(cloud) else None
        rv = _f(rain[i]) if i < len(rain) else None
        if cv is not None:
            entry["cloud"] = round(cv)
        if rv is not None:
            entry["rain"] = round(rv, 1)
        series.append(entry)
    return series


def build_current(cur):
    """Shape the Open-Meteo `current` block into the dashboard's weather.current."""
    code = cur.get("weather_code")
    label, cat = weather_label(code)
    ghi = _f(cur.get("shortwave_radiation"))
    return {
        "temp_c": _f(cur.get("temperature_2m")),
        "cloud_pct": _f(cur.get("cloud_cover")),
        "rain_mm": _f(cur.get("precipitation")),
        "ghi_wm2": round(ghi) if ghi is not None else None,
        "code": int(code) if code is not None else None,
        "condition": label,
        "category": cat,
    }


# --------------------------------------------------------------------------- #
# Location
# --------------------------------------------------------------------------- #
def resolve_location(live_json_path, cli_lat, cli_lon):
    """lat/lon from --lat/--lon, else the plant block in ph1000_live.json, else defaults."""
    if cli_lat is not None and cli_lon is not None:
        return cli_lat, cli_lon, "cli"
    if live_json_path and os.path.exists(live_json_path):
        try:
            with open(live_json_path, "r", encoding="utf-8") as f:
                plant = (json.load(f) or {}).get("plant", {}) or {}
            lat, lon = _f(plant.get("lat")), _f(plant.get("lon"))
            if lat is not None and lon is not None:
                return lat, lon, "plant"
        except (OSError, ValueError):
            pass
    return DEFAULT_LAT, DEFAULT_LON, "default"


def fetch_open_meteo(lat, lon):
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "current": CURRENT_VARS, "hourly": HOURLY_VARS,
        "past_days": PAST_DAYS, "forecast_days": 1, "timezone": "auto",
    })
    with urllib.request.urlopen(f"{API_URL}?{q}", timeout=25) as resp:
        return json.loads(resp.read().decode())


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="PH1000 weather + harvest-impact fetcher (Open-Meteo)")
    ap.add_argument("--out", help="write the weather JSON block here")
    ap.add_argument("--live-json", dest="live_json",
                    help="ph1000_live.json to read plant.lat/lon from (auto-detect location)")
    ap.add_argument("--lat", type=float, help="override latitude")
    ap.add_argument("--lon", type=float, help="override longitude")
    args = ap.parse_args()

    lat, lon, src = resolve_location(args.live_json, args.lat, args.lon)
    out = {"ok": False, "lat": lat, "lon": lon, "location_source": src, "source": "open-meteo",
           "generated": datetime.now(timezone.utc).isoformat()}

    try:
        raw = fetch_open_meteo(lat, lon)
        hourly = raw.get("hourly", {}) or {}
        daily = aggregate_daily(hourly)
        # The API uses the plant's own timezone (timezone=auto) and forecast_days=1, so the LAST
        # daily record is the plant-local current day — timezone-proof, no UTC/local mismatch.
        today_rec = daily[-1] if daily else None
        out.update({
            "ok": True,
            "current": build_current(raw.get("current", {}) or {}),
            "today": today_rec,
            "daily": daily,
            "hourly_ghi": hourly_ghi_series(hourly),
        })
        c = out["current"]
        print("[WEATHER] %s, %.1f°C, cloud %s%%, GHI %s W/m² @ (%.4f,%.4f via %s)"
              % (c["condition"], c["temp_c"] or 0, c["cloud_pct"], c["ghi_wm2"], lat, lon, src))
        if today_rec and today_rec.get("loss_pct") is not None:
            print("[WEATHER] today: %s — harvest ~%d%% of clear-sky (weather cost ~%d%%)"
                  % (today_rec["condition"], 100 - today_rec["loss_pct"], today_rec["loss_pct"]))
    except Exception as e:  # noqa: BLE001 — fail soft, exactly like the BMS fetcher
        print("[WEATHER] fetch failed (%s) -> dashboard hides the weather pane" % e)
        out = {"ok": False, "lat": lat, "lon": lon, "location_source": src,
               "source": "open-meteo", "generated": datetime.now(timezone.utc).isoformat()}

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, default=str)
        print("[WEATHER] wrote", args.out)
    else:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
