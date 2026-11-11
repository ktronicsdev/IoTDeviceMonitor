#!/usr/bin/env python3
"""
SolaX Cloud developer-API client for the pipeline (UC21) - read-only, standard library only.

A separate platform from ShineMonitor, DessMonitor, SolisCloud and Deye. An
application (client_id + client_secret, made in the SolaX developer portal)
trades them for a bearer token with the OAuth client-credentials grant:

    POST <base>/openapi/auth/oauth/token   (form: client_id, client_secret,
         grant_type=client_credentials)  -> {code:0, result:{access_token, expires_in}}
    GET  <base><path>?...   with   Authorization: bearer <access_token>

The token lasts about 30 days; this client fetches a fresh one per run, so no
token is ever stored. Replies are {code, message, result}; code 10000 is ok.
The limit is 100 requests a minute; the client keeps to about 80.

Read-only by construction: get() refuses any path outside READ_PATHS.

Config: the `solax` block of credentials.json - client_id, client_secret,
api_base (default the EU region), tz_offset_minutes.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://openapi-eu.solaxcloud.com"
TOKEN_PATH = "/openapi/auth/oauth/token"
READ_PATHS = frozenset({
    "/openapi/v2/plant/page_plant_info",
    "/openapi/v2/device/page_device_info",
    "/openapi/v2/alarm/page_alarm_info",
})
OK_CODE = 10000
MIN_REQUEST_GAP_S = 0.75
RETRY_WAIT_S = 5.0


class SolaxError(Exception):
    """A SolaX call failed. The message never carries credentials."""


def _urllib_transport(method, url, form, headers, timeout):
    """Return (http_status, parsed_json_or_None)."""
    data = urllib.parse.urlencode(form).encode("utf-8") if form is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SolaxError(f"network error: {getattr(e, 'reason', e)}") from None
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, None


def _why(resp):
    if not isinstance(resp, dict):
        return "no JSON body"
    return str(resp.get("message") or resp.get("code") or "unknown error")[:200]


class SolaxClient:
    def __init__(self, client_id, client_secret, base_url=DEFAULT_BASE, timeout=30,
                 transport=None, min_gap_s=MIN_REQUEST_GAP_S, sleep=time.sleep):
        if not (client_id and client_secret):
            raise SolaxError("client_id and client_secret are required in the 'solax' block")
        self._client_id = str(client_id)
        self._client_secret = str(client_secret)
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self._transport = transport or _urllib_transport
        self._min_gap_s = min_gap_s
        self._sleep = sleep
        self._last = None
        self._token = None

    @classmethod
    def from_config(cls, cfg, **kwargs):
        return cls(cfg.get("client_id"), cfg.get("client_secret"),
                   base_url=cfg.get("api_base") or DEFAULT_BASE, **kwargs)

    def _send(self, method, path, form, headers):
        if self._last is not None:
            wait = self._min_gap_s - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = time.monotonic()
        return self._transport(method, self.base_url + path, form, headers, self.timeout)

    def token(self, force=False):
        if self._token and not force:
            return self._token
        status, resp = self._send("POST", TOKEN_PATH, {
            "client_id": self._client_id, "client_secret": self._client_secret,
            "grant_type": "client_credentials"},
            {"Content-Type": "application/x-www-form-urlencoded"})
        tok = ((resp or {}).get("result") or {}).get("access_token") if isinstance(resp, dict) else None
        if status != 200 or not tok:
            raise SolaxError(f"SolaX login failed (HTTP {status}): {_why(resp)}")
        self._token = str(tok).strip()
        return self._token

    def get(self, path, params=None):
        """GET a read path; returns the `result` object. One re-login if the token
        is refused, one retry after a rate limit, a server error or a network error."""
        if path not in READ_PATHS:
            raise SolaxError(f"{path} is not a read endpoint; this client is read-only")
        query = "?" + urllib.parse.urlencode(params) if params else ""
        relogin = retried = refreshed = False
        while True:
            headers = {"Authorization": f"bearer {self.token(force=relogin)}"}
            relogin = False
            try:
                status, resp = self._send("GET", path + query, None, headers)
            except SolaxError:
                if retried:
                    raise
                retried = True
                self._sleep(RETRY_WAIT_S)
                continue
            if status == 200 and isinstance(resp, dict) and resp.get("code") == OK_CODE:
                return resp.get("result") or {}
            msg = _why(resp).lower()
            if not refreshed and (status == 401 or "token" in msg):
                refreshed = relogin = True
                continue
            if not retried and (status == 429 or status >= 500 or resp is None or "frequen" in msg
                                or "too many" in msg):
                retried = True
                self._sleep(RETRY_WAIT_S)
                continue
            raise SolaxError(f"{path} failed (HTTP {status}): {_why(resp)}")

    def paged(self, path, params):
        out, page = [], 1
        while True:
            res = self.get(path, {**params, "pageNo": page})
            recs = res.get("records") or []
            out += recs
            if not recs or page >= int(res.get("pages") or 1):
                return out
            page += 1
