#!/usr/bin/env python3
"""
SolisCloud API client (Ginlong / soliscloud.com).

This is a SEPARATE platform from ShineMonitor and DessMonitor. SolisCloud signs
every request with HMAC-SHA1 using an API **Key ID + Key Secret** (issued from
SolisCloud -> Service -> API Management), NOT the web username/password.

Signing scheme (the standard SolisCloud "open API" scheme):

    stringToSign = VERB        + "\n"
                 + Content-MD5  + "\n"   # base64( md5(body) )
                 + Content-Type + "\n"   # application/json
                 + Date         + "\n"   # RFC1123 in GMT
                 + CanonicalizedResource  # the request path, e.g. /v1/api/inverterDetail

    sign         = base64( hmac_sha1(key_secret, stringToSign) )
    Authorization: API <key_id>:<sign>

Read endpoints used here:
    POST /v1/api/inverterList     -> inverters under the account
    POST /v1/api/inverterDetail   -> live state + pac (AC power) for one inverter

Control endpoints (require the Control API permission to be enabled by Solis):
    POST /v2/api/control          -> send a control command (cid + value)
    POST /v2/api/atRead           -> read back a control register (cid)
    POST /v2/api/atReadList       -> list readable control registers (discovery)

Docs reference: "SolisCloud Platform API" / "Solis Control API" (request via your
Solis distributor). The on/off control "cid" is account/model specific — see
check_solis_switch.py --discover to find it before enabling auto-control.
"""

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_BASE_URL = "https://www.soliscloud.com:13333"
CONTENT_TYPE = "application/json"


def _md5_base64(body: bytes) -> str:
    """Content-MD5 header: base64 of the raw MD5 digest of the body."""
    return base64.b64encode(hashlib.md5(body).digest()).decode()


def _gmt_date() -> str:
    """RFC1123 date in GMT, e.g. 'Mon, 29 Jun 2026 12:00:00 GMT'.

    Uses an explicit weekday/month map so the result is locale-independent
    (strftime('%a','%b') would localise on non-English CI runners)."""
    now = datetime.now(timezone.utc)
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return (f"{days[now.weekday()]}, {now.day:02d} {months[now.month - 1]} "
            f"{now.year} {now.hour:02d}:{now.minute:02d}:{now.second:02d} GMT")


def _hmac_sha1_base64(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


class SolisClient:
    """Minimal signed-POST client for the SolisCloud open API."""

    def __init__(self, key_id, key_secret, base_url=DEFAULT_BASE_URL, timeout=30):
        if not key_id or not key_secret:
            raise ValueError("SolisClient requires key_id and key_secret")
        self.key_id = key_id
        self.key_secret = key_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def post(self, resource: str, payload: dict) -> dict:
        """Signed POST. Returns the decoded JSON, or an error dict with
        success=False and a 'msg' (never raises for network/HTTP errors)."""
        body = json.dumps(payload, separators=(",", ":")).encode()
        content_md5 = _md5_base64(body)
        date = _gmt_date()
        string_to_sign = f"POST\n{content_md5}\n{CONTENT_TYPE}\n{date}\n{resource}"
        sign = _hmac_sha1_base64(self.key_secret, string_to_sign)

        req = urllib.request.Request(
            self.base_url + resource,
            data=body,
            method="POST",
            headers={
                "Content-MD5": content_md5,
                "Content-Type": CONTENT_TYPE,
                "Date": date,
                "Authorization": f"API {self.key_id}:{sign}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace") if e.fp else ""
            return {"success": False, "code": str(e.code), "msg": f"HTTP {e.code}: {detail}"}
        except Exception as e:  # noqa: BLE001 - network errors must not crash the run
            return {"success": False, "msg": str(e)}

    # --- read API ---------------------------------------------------------- #
    def inverter_list(self, page_no=1, page_size=100, station_id=None) -> dict:
        payload = {"pageNo": page_no, "pageSize": page_size}
        if station_id:
            payload["stationId"] = station_id
        return self.post("/v1/api/inverterList", payload)

    def inverter_detail(self, inverter_id=None, sn=None) -> dict:
        payload = {}
        if inverter_id:
            payload["id"] = inverter_id
        if sn:
            payload["sn"] = sn
        return self.post("/v1/api/inverterDetail", payload)

    # --- control API (requires Control permission) ------------------------- #
    def control(self, inverter_id, cid, value) -> dict:
        return self.post("/v2/api/control",
                         {"inverterId": str(inverter_id), "cid": int(cid), "value": str(value)})

    def at_read(self, inverter_id, cid) -> dict:
        return self.post("/v2/api/atRead",
                         {"inverterId": str(inverter_id), "cid": int(cid)})

    def at_read_list(self, inverter_id) -> dict:
        return self.post("/v2/api/atReadList", {"inverterId": str(inverter_id)})


def is_success(resp: dict) -> bool:
    """SolisCloud signals success via success=True and/or code '0'/0."""
    if not isinstance(resp, dict):
        return False
    if resp.get("success") is True:
        return True
    return str(resp.get("code")) == "0"
