#!/usr/bin/env python3
"""
SOLARMAN Business client for the pipeline (UC22) - read-only, standard library only.

SOLARMAN Business (globalpro.solarmanpv.com) is a separate platform from
ShineMonitor, DessMonitor, SolisCloud and Deye Cloud. There is no API key: this
calls the JSON endpoints the web app calls, with the bearer token the web app
gets. Checked live on 9-11 Nov 2026:

  * A password login needs a Cloudflare Turnstile token (HTTP 412
    AUTH_SLIDE_ERROR without it), so a GitHub job cannot log in by itself.
  * POST /oauth2-s/oauth/token with grant_type=refresh_token needs no captcha.
    It returns a new access token (60 days) and a new refresh token, but the new
    refresh token keeps the END DATE of the browser login it came from (11 Nov
    2026: three renewals of a 9 Nov login all ended 8 May 2027). Renewing never
    outlives that login, so a browser login is needed every 180 days.
  * Two pairs renew side by side: renewing one leaves the other working (checked
    11 Nov 2026), so the job's pair can be one renewal of a workstation's session.
  * So the job's session is seeded from a browser login, kept in the
    SOLARMAN_TOKENS_JSON secret, and renewed here: when the access token has
    under 7 days left, the pair is refreshed and the new one written back to
    that secret at once (publish_tokens). The refresh token's own age is no
    reason to renew; under 21 days the caller mails that a browser login is due.

The token is NOT read-only on SOLARMAN's side: it carries the account's full
rights, Remote Control included. This client is read-only by construction:
request() refuses any (method, path) outside READ_PATHS before anything is sent,
so the order endpoints (/order-s/...) cannot be reached from the pipeline,
whatever a caller passes.

Replies are plain JSON (no envelope). Times are epoch seconds.
"""

import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://globalpro.solarmanpv.com"
TOKEN_PATH = "/oauth2-s/oauth/token"
SECRET_NAME = "SOLARMAN_TOKENS_JSON"

# Every (method, path) this client may send, the path matched in full without its
# query string. Nothing under /order-s (Remote Control) is here.
READ_PATHS = (
    ("POST", r"/maintain-s/operating/station/v2/search"),
    ("GET", r"/maintain-s/operating/station/\d+/inverter"),
    ("POST", r"/maintain-s/operating/station/alert"),
    ("POST", r"/device-s/device/v3/detail"),
    ("GET", r"/device-s/device/\d+/stats/day"),
)

REFRESH_ACCESS_BELOW_S = 7 * 86400     # renew when the access token has under 7 days
LOGIN_DUE_BELOW_S = 21 * 86400         # warn when the sign-in itself ends within 21 days
MIN_REQUEST_GAP_S = 0.5
RETRY_WAIT_S = 5.0
USER_AGENT = "ktronics-iot-pipeline (SOLARMAN read-only)"


class SolarmanError(Exception):
    """A SOLARMAN call failed. The message never carries a token."""


class SignInError(SolarmanError):
    """No usable session: a browser login is needed to seed a new one."""


def jwt_claims(token):
    """The payload of a JWT, unverified: only its exp and organisation are read."""
    try:
        part = str(token).split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, TypeError):
        return {}


def seconds_left(token, now):
    return float(jwt_claims(token).get("exp") or 0) - now


def allowed(method, path):
    return any(m == method and re.fullmatch(rx, path) for m, rx in READ_PATHS)


def load_tokens(path):
    """{access_token, refresh_token} from a file, or None when it holds no pair."""
    try:
        with open(path, encoding="utf-8") as f:
            tokens = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(tokens, dict) or not tokens.get("access_token") \
            or not tokens.get("refresh_token"):
        return None
    return {"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"]}


def save_tokens(path, tokens):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"access_token": tokens["access_token"],
                   "refresh_token": tokens["refresh_token"],
                   "saved_at": int(time.time())}, f)
    os.replace(tmp, path)


def mask_in_actions(tokens, emit=print, env=os.environ):
    """Register both new tokens with the Actions log masker before anything else
    runs. A renewed token is not the secret GitHub knows, so it is not masked
    unless this says so."""
    if env.get("GITHUB_ACTIONS") == "true":
        for key in ("access_token", "refresh_token"):
            emit(f"::add-mask::{tokens[key]}")


def publish_tokens(tokens, env=os.environ, run=subprocess.run):
    """Write the renewed pair back to the SOLARMAN_TOKENS_JSON secret with gh.

    GH_TOKEN is the repository's GH_PAT secret, the token the PH1000 loop
    already saves its renewed BMS sign-in with; the job's own GITHUB_TOKEN
    cannot write secrets. The pair goes on stdin, never on the command line,
    and gh's output is not printed.
    """
    if not env.get("GH_TOKEN"):
        raise SolarmanError("GH_TOKEN is not set: the renewed SOLARMAN sign-in cannot be saved")
    cmd = ["gh", "secret", "set", SECRET_NAME]
    if env.get("GITHUB_REPOSITORY"):
        cmd += ["--repo", env["GITHUB_REPOSITORY"]]
    body = json.dumps({"access_token": tokens["access_token"],
                       "refresh_token": tokens["refresh_token"]})
    try:
        done = run(cmd, input=body, text=True, capture_output=True, env=dict(env), timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise SolarmanError(f"gh secret set could not run: {type(e).__name__}") from None
    if done.returncode != 0:
        raise SolarmanError(f"gh secret set failed (exit {done.returncode})")


def _urllib_transport(method, url, headers, form=None, body=None, timeout=30):
    """Send one request, return (http_status, parsed_json_or_None)."""
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SolarmanError(f"network error: {type(getattr(e, 'reason', e)).__name__}") from None
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        return status, None


def _why(resp):
    if not isinstance(resp, dict):
        return "no JSON body"
    return str(resp.get("code") or resp.get("error") or resp.get("msg") or "")[:80]


class SolarmanClient:
    """Bearer-token client for SOLARMAN Business, read paths only.

    on_refresh(tokens) runs right after a renewal and before any other request:
    the pipeline masks the new pair, saves it, and writes it to the secret
    there. If on_refresh raises, the new pair is still used for this run and
    the failure is kept in `save_error` for the caller to report.
    """

    def __init__(self, tokens, base_url=None, transport=None, on_refresh=None,
                 now=time.time, sleep=time.sleep, min_gap_s=MIN_REQUEST_GAP_S, timeout=30):
        if not tokens or not tokens.get("access_token") or not tokens.get("refresh_token"):
            raise SignInError("no SOLARMAN sign-in: the tokens are missing")
        self.tokens = dict(tokens)
        self.base = (base_url or DEFAULT_BASE).rstrip("/")
        self._transport = transport or _urllib_transport
        self._on_refresh = on_refresh
        self._now = now
        self._sleep = sleep
        self._min_gap_s = min_gap_s
        self._last = None
        self.timeout = timeout
        self.refreshed = False
        self.refresh_error = None
        self.save_error = None

    # --- tokens --------------------------------------------------------------
    def _send(self, method, url, headers, form=None, body=None):
        if self._last is not None:
            wait = self._min_gap_s - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = time.monotonic()
        return self._transport(method, url, headers, form=form, body=body, timeout=self.timeout)

    def refresh(self):
        form = {"grant_type": "refresh_token", "refresh_token": self.tokens["refresh_token"],
                "client_id": "test"}
        status, resp = self._send("POST", self.base + TOKEN_PATH,
                                  {"User-Agent": USER_AGENT, "Accept": "application/json"},
                                  form=form)
        if status != 200 or not isinstance(resp, dict) or not resp.get("access_token"):
            raise SignInError(f"SOLARMAN refused to renew the sign-in (HTTP {status} {_why(resp)})".strip())
        self.tokens = {"access_token": resp["access_token"],
                       "refresh_token": resp.get("refresh_token") or self.tokens["refresh_token"]}
        self.refreshed = True
        if self._on_refresh:
            try:
                self._on_refresh(dict(self.tokens))
            except SolarmanError as e:
                self.save_error = str(e)
        return self.tokens

    def days_left(self):
        now = self._now()
        return {key: round(seconds_left(self.tokens[key + "_token"], now) / 86400, 1)
                for key in ("access", "refresh")}

    def access_token(self, force_refresh=False):
        """The access token, renewed first when it is near its end.

        A renewal that is refused while the old token still works is kept in
        `refresh_error` and the run goes on: the caller mails about it. Only a
        token that has run out stops the run. A refresh token near its end is
        not renewed: a renewal keeps its end date, so it would only rewrite the
        secret every run.
        """
        now = self._now()
        a_left = seconds_left(self.tokens["access_token"], now)
        due = force_refresh or a_left < REFRESH_ACCESS_BELOW_S
        if due and not self.refreshed and not self.refresh_error:
            # One try a run: a refused renewal is not asked again for every request.
            try:
                self.refresh()
            except SignInError as e:
                self.refresh_error = str(e)
        if not self.refreshed and a_left < 60:
            raise SignInError("the SOLARMAN sign-in has run out and could not "
                              "be renewed: a browser login is needed")
        return self.tokens["access_token"]

    # --- data ----------------------------------------------------------------
    def request(self, method, path, params=None, body=None):
        """Send one read request. On a 401 the sign-in is renewed once; a rate
        limit, a server error or a network error gets one more try."""
        if not allowed(method, path):
            raise SolarmanError(f"{method} {path} is not a read path; this client is read-only")
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        renewed = retried = False
        while True:
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json",
                       "Authorization": "Bearer " + self.access_token()}
            try:
                status, resp = self._send(method, url, headers,
                                          body=(body if body is not None else {})
                                          if method == "POST" else None)
            except SolarmanError:
                if retried:
                    raise
                retried = True
                self._sleep(RETRY_WAIT_S)
                continue
            if status == 200 and resp is not None:
                return resp
            if status == 401 and not renewed:
                renewed = True
                self.refreshed = False
                self.refresh()
                continue
            if not retried and (status == 429 or status >= 500 or resp is None):
                retried = True
                self._sleep(RETRY_WAIT_S)
                continue
            raise SolarmanError(f"{path} failed (HTTP {status} {_why(resp)})".strip())

    # --- the calls UC22 makes ---------------------------------------------------
    def stations(self):
        out, page = [], 1
        while True:
            reply = self.request("POST", "/maintain-s/operating/station/v2/search",
                                 {"page": page, "size": 100, "order.direction": "ASC",
                                  "order.property": "name"}, {})
            items = reply.get("data") or []
            out += [item.get("station") or item for item in items]
            if not items or len(out) >= int(reply.get("total") or 0):
                return out
            page += 1

    def inverters(self, station_id):
        reply = self.request("GET", f"/maintain-s/operating/station/{int(station_id)}/inverter",
                             {"page": 1, "size": 1000, "order.direction": "ASC",
                              "order.property": "device_sn"})
        return reply.get("data") or []

    def open_alerts(self, station_id, tz_name="Asia/Colombo"):
        reply = self.request("POST", "/maintain-s/operating/station/alert",
                             {"order.direction": "DESC", "order.property": "alertTime",
                              "size": 100, "page": 1},
                             {"groupIdList": None, "status": "1", "timezone": tz_name,
                              "stationId": str(station_id)})
        return reply.get("data") or []

    def detail(self, device_id, station_id):
        return self.request("POST", "/device-s/device/v3/detail", None,
                            {"language": "en", "deviceId": int(device_id),
                             "needRealTimeDataFlag": True, "siteId": int(station_id)})

    def day(self, device_id, day):
        """One local day's 5-minute series: [{name, unit, detailList[{collectionTime, value}]}]."""
        reply = self.request("GET", f"/device-s/device/{int(device_id)}/stats/day",
                             {"day": day.strftime("%Y/%m/%d"), "lan": "en"})
        return reply if isinstance(reply, list) else []
