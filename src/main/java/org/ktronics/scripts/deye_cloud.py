#!/usr/bin/env python3
"""
Deye Cloud OpenAPI client for the pipeline (UC19) - read-only, standard library only.

This is a SEPARATE platform from ShineMonitor, DessMonitor and SolisCloud. A
developer app (App ID + App Secret) logs in with an account's email and the
SHA-256 of its password, and gets a bearer token:

    POST <base>/account/token?appId=<AppId>
         {"appSecret", "email", "password": sha256 hex, "companyId"?}
         -> top-level accessToken, expiresIn (seconds)
    POST <base><path> {json}   with   Authorization: bearer <accessToken>

Replies are NOT wrapped in "data": the fields sit at the top level beside
success / code ("1000000" = ok) / msg / requestId.

With `companyId` set the token sees every plant of the company (installer
view); without it, only the account's own plants.

Read-only by construction: post() refuses any path outside READ_PATHS, so the
Open API's control endpoints (/order/*, /strategy/*) cannot be reached from the
pipeline, whatever a caller passes.

Found on the live API (Nov 2026), not in Deye's Swagger text:
  * /station/device takes at most 10 stationIds per call ("list too long").
  * Alert times are epoch SECONDS; status is 0 closed (with an end time) and
    1 open.

Config: the `deye` block of credentials.json - app_id, app_secret, email,
password_sha256, company_id, data_center. The workstation's read-only Deye MCP
has its own, fuller client; it is not part of this repository.
"""

import json
import time
import urllib.error
import urllib.request

DATA_CENTERS = {
    "eu": "https://eu1-developer.deyecloud.com/v1.0",       # EMEA + APAC (Sri Lanka)
    "am": "https://us1-developer.deyecloud.com/v1.0",       # Americas
    "india": "https://india-developer.deyecloud.com/v1.0",
}

TOKEN_PATH = "/account/token"
READ_PATHS = frozenset({"/station/list", "/station/device", "/station/alertList"})
OK_CODE = "1000000"

# Deye's best-practices page puts a preliminary ceiling near 60 requests a
# minute per app; one request a second stays under it.
MIN_REQUEST_GAP_S = 1.0
RETRY_WAIT_S = 5.0


class DeyeError(Exception):
    """A Deye Cloud call failed. The message never carries credentials."""


def is_success(resp):
    if not isinstance(resp, dict):
        return False
    if resp.get("success") is True:
        return True
    return resp.get("success") is None and str(resp.get("code")) == OK_CODE


def base_url_for(data_center):
    try:
        return DATA_CENTERS[(data_center or "eu").lower()]
    except KeyError:
        raise DeyeError(f"unknown data_center {data_center!r}; use one of "
                        f"{', '.join(DATA_CENTERS)}") from None


def _urllib_transport(url, body, headers, timeout):
    """POST JSON, return (http_status, parsed_json_or_None)."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise DeyeError(f"network error: {getattr(e, 'reason', e)}") from None
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, None


def _why(resp):
    if not isinstance(resp, dict):
        return "no JSON body"
    return str(resp.get("msg") or resp.get("code") or "unknown error")[:200]


def _token_refused(status, resp):
    if status == 401:
        return True
    return (isinstance(resp, dict) and not is_success(resp)
            and "token" in str(resp.get("msg", "")).lower())


def _retryable(status, resp):
    """Worth one more try: rate limit, a server error, or no JSON at all."""
    if status == 429 or status >= 500:
        return True
    if resp is None:
        return True
    return "too many" in _why(resp).lower()


class DeyeClient:
    """Bearer-token client for the Deye Cloud developer API (read paths only)."""

    def __init__(self, app_id, app_secret, password_sha256, email=None, username=None,
                 company_id=None, data_center="eu", base_url=None, timeout=30,
                 transport=None, min_gap_s=MIN_REQUEST_GAP_S, sleep=time.sleep):
        if not (app_id and app_secret and password_sha256 and (email or username)):
            raise DeyeError("app_id, app_secret, password_sha256 and email or username "
                            "are required in the 'deye' block")
        self.app_id = str(app_id)
        self._app_secret = str(app_secret)
        self._password_sha256 = str(password_sha256).lower()
        self.email = email
        self.username = username
        self.company_id = company_id
        self.base_url = (base_url or base_url_for(data_center)).rstrip("/")
        self.timeout = timeout
        self._transport = transport or _urllib_transport
        self._min_gap_s = min_gap_s
        self._sleep = sleep
        self._last = None
        self._token = None

    @classmethod
    def from_config(cls, cfg, **kwargs):
        return cls(cfg.get("app_id"), cfg.get("app_secret"), cfg.get("password_sha256"),
                   email=cfg.get("email"), username=cfg.get("username"),
                   company_id=cfg.get("company_id"),
                   data_center=cfg.get("data_center", "eu"),
                   base_url=cfg.get("api_base"), **kwargs)

    def _send(self, url, body, headers):
        if self._last is not None:
            wait = self._min_gap_s - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = time.monotonic()
        return self._transport(url, body, headers, self.timeout)

    def _token_body(self):
        body = {"appSecret": self._app_secret, "password": self._password_sha256}
        if self.email:
            body["email"] = self.email
        else:
            body["username"] = self.username
        if self.company_id not in (None, ""):
            body["companyId"] = int(self.company_id)
        return body

    def token(self, force=False):
        if self._token and not force:
            return self._token
        url = f"{self.base_url}{TOKEN_PATH}?appId={self.app_id}"
        status, resp = self._send(url, self._token_body(),
                                  {"Content-Type": "application/json"})
        if status != 200 or not is_success(resp) or not (resp or {}).get("accessToken"):
            raise DeyeError(f"Deye Cloud login failed (HTTP {status}): {_why(resp)}")
        tok = str(resp["accessToken"]).strip()
        if tok.lower().startswith("bearer "):
            tok = tok[7:].strip()
        self._token = tok
        return tok

    def post(self, path, body=None):
        """POST to a read path. Logs in again once if the token is refused, and
        tries once more after a rate limit, a server error or a network error."""
        if path not in READ_PATHS:
            raise DeyeError(f"{path} is not a read endpoint; this client is read-only")
        body = body or {}
        refreshed = retried = relogin = False
        while True:
            headers = {"Content-Type": "application/json",
                       "Authorization": f"bearer {self.token(force=relogin)}"}
            relogin = False
            try:
                status, resp = self._send(self.base_url + path, body, headers)
            except DeyeError:
                if retried:
                    raise
                retried = True
                self._sleep(RETRY_WAIT_S)
                continue
            if status == 200 and is_success(resp):
                return resp
            if not refreshed and _token_refused(status, resp):
                refreshed = relogin = True
                continue
            if not retried and _retryable(status, resp):
                retried = True
                self._sleep(RETRY_WAIT_S)
                continue
            raise DeyeError(f"{path} failed (HTTP {status}): {_why(resp)}")
