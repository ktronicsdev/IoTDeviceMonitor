#!/usr/bin/env python3
"""
SOLARMAN alarms through the six-hour gate (UC22) - tests.

No network. The day history is shaped like SOLARMAN's live
/device-s/device/{id}/stats/day of 10 Nov 2026: one item per series, epoch-second
collectionTime, values as strings, names with no-break spaces and doubled
spaces ("Load Voltage  L1L2"). The readings are the real ones of that week:
an SRNE idling at 10-16 W with its house on the changeover, an SRNE that lost
its battery (SoC exactly 0, DC bus at 470 V), a Deye-type riding a 10-hour cut
on its battery, and an inverter that has sent nothing since 24 May.
Plant names are made up.
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

SRC = Path(__file__).resolve().parents[6]
ROOT = SRC.parent
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_solarman_alarms as sa  # noqa: E402
import generate_device_alarms as gda  # noqa: E402
import solarman_cloud as sc  # noqa: E402

TS = sa.TS_FMT
TZ = 330
CFG = sa.load_config()
SRNE, DEYE, DEYE3 = "0_2421_1", "0_5407_1", "0_5411_1"
SRNE_PROFILE = CFG["profiles"]["srne"]
DEYE_PROFILE = CFG["profiles"]["deye"]


def epoch(local):
    return int((datetime.strptime(local, TS) - timedelta(minutes=TZ))
               .replace(tzinfo=timezone.utc).timestamp())


def utc(local):
    return (datetime.strptime(local, TS) - timedelta(minutes=TZ)).replace(tzinfo=timezone.utc)


def srne(ts, grid=229.0, out=240.0, load=280.0, soc=45.0, bus=391.0):
    return {"ts": ts, "grid_v": grid, "out_v": out, "load_w": load, "soc": soc, "bus_v": bus,
            "batt_v": None}


def deye(ts, grid=235.0, out=238.0, load=300.0, soc=90.0, batt=49.6):
    return {"ts": ts, "grid_v": grid, "out_v": out, "load_w": load, "soc": soc, "bus_v": None,
            "batt_v": batt}


def every5(start, end, make, **kw):
    out, t = [], datetime.strptime(start, TS)
    while t <= datetime.strptime(end, TS):
        out.append(make(t.strftime(TS), **kw))
        t += timedelta(minutes=5)
    return out


# The names exactly as the live history spells them, spacing quirks included.
SRNE_NAMES = {"grid_v": "Grid Voltage", "out_v": "AC Voltage R/U/A", "load_w": "Load Power-Local",
              "soc": "SoC", "bus_v": "Bus Voltage"}
DEYE_NAMES = {"grid_v": "Grid Voltage L1L2", "out_v": "Load Voltage  L1L2",
              "load_w": "Total Consumption Power", "soc": "SoC", "batt_v": "Battery Voltage"}


def api_day(rows, names):
    """Rows -> the stats/day reply: one item per series, plus an empty series as
    the SRNE's Battery Voltage is."""
    out = []
    for field, name in names.items():
        out.append({"name": name, "unit": "V", "detailList": [
            {"collectionTime": epoch(r["ts"]), "value": f"{r[field]:.2f}"}
            for r in rows if r.get(field) is not None]})
    out.append({"name": "Battery Voltage" if "batt_v" not in names else "Bus Voltage",
                "unit": "V", "detailList": []})
    return out


@pytest.fixture(autouse=True)
def gate_on(monkeypatch):
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "on")


def run(dev, rows, now, profile=SRNE_PROFILE, last_data=None, sid=64690226):
    """advance -> open alarms, as read_station runs them for one inverter."""
    sa.advance(dev, [r for r in rows if r["ts"] <= now], profile, CFG)
    last = last_data or (dev["last_row"]["ts"] if dev.get("last_row") else None)
    return sa.open_alarms(dev, now, last, CFG, sid)


def kinds(specs):
    return [s["kind"] for s in specs]


# --------------------------------------------------------------------------- #
# The client: read-only, the sign-in and its write-back
# --------------------------------------------------------------------------- #
def jwt(days_left, now=1_794_000_000.0, tag="a"):
    import base64
    body = base64.urlsafe_b64encode(json.dumps(
        {"exp": int(now + days_left * 86400), "tag": tag}).encode()).decode().rstrip("=")
    return f"h.{body}.sig{tag}"


NOW_S = 1_794_000_000.0


class Transport:
    """Records every request; answers the token path and the read paths."""

    def __init__(self, token_reply=None, replies=None):
        self.calls = []
        self.token_reply = token_reply or (200, {"access_token": jwt(60, tag="new"),
                                                 "refresh_token": jwt(180, tag="newr")})
        self.replies = list(replies or [])

    def __call__(self, method, url, headers, form=None, body=None, timeout=30):
        self.calls.append({"method": method, "url": url, "headers": headers, "form": form,
                           "body": body})
        if url.endswith(sc.TOKEN_PATH):
            return self.token_reply
        if self.replies:
            return self.replies.pop(0)
        return (200, {"data": [], "total": 0}) if "/maintain-s/" in url else (200, [])


def client(transport, access_days=50, refresh_days=170, **kw):
    return sc.SolarmanClient({"access_token": jwt(access_days, tag="old"),
                              "refresh_token": jwt(refresh_days, tag="oldr")},
                             transport=transport, now=lambda: NOW_S, sleep=lambda s: None,
                             min_gap_s=0, **kw)


class TestClient:
    def test_order_paths_are_refused_before_any_request(self):
        t = Transport()
        c = client(t)
        for method, path in (("POST", "/order-s/order/action/sendOrder"),
                             ("POST", "/maintain-s/operating/station/v2/search/../../order-s/x"),
                             ("GET", "/device-s/device/1/stats/day/../setting")):
            with pytest.raises(sc.SolarmanError, match="read-only"):
                c.request(method, path)
        assert t.calls == []

    def test_reads_send_the_bearer_token(self):
        t = Transport()
        c = client(t)
        c.day(254051661, date(2026, 11, 10))
        call = t.calls[0]
        assert call["headers"]["Authorization"] == "Bearer " + c.tokens["access_token"]
        assert "day=2026%2F11%2F10" in call["url"]

    def test_weeks_left_means_no_renewal(self):
        t = Transport()
        client(t, access_days=8).stations()
        assert not any(c["url"].endswith(sc.TOKEN_PATH) for c in t.calls)

    def test_under_seven_days_renews_and_hands_over_the_new_pair_first(self):
        t = Transport(replies=[(200, {"data": [], "total": 0})])
        seen = []
        c = client(t, access_days=6, on_refresh=lambda tok: seen.append((tok, len(t.calls))))
        c.stations()
        assert t.calls[0]["form"]["grant_type"] == "refresh_token"
        assert t.calls[0]["form"]["refresh_token"] == jwt(170, tag="oldr")
        # handed over after the renewal, before the read that uses it
        assert seen == [({"access_token": jwt(60, tag="new"),
                          "refresh_token": jwt(180, tag="newr")}, 1)]
        assert t.calls[1]["headers"]["Authorization"] == "Bearer " + jwt(60, tag="new")

    def test_a_refresh_token_near_its_end_is_not_renewed(self):
        """A renewal keeps the refresh token's end date (checked 11 Nov 2026), so
        renewing for it would only rewrite the secret every hour; main() mails instead."""
        t = Transport()
        client(t, access_days=40, refresh_days=20).stations()
        assert not any(c["url"].endswith(sc.TOKEN_PATH) for c in t.calls)

    def test_a_refused_renewal_is_tried_once_and_the_old_token_kept(self):
        t = Transport(token_reply=(400, {"code": "invalid_grant"}))
        c = client(t, access_days=3)
        c.stations()
        c.inverters(1)
        assert sum(x["url"].endswith(sc.TOKEN_PATH) for x in t.calls) == 1
        assert "invalid_grant" in c.refresh_error
        assert t.calls[-1]["headers"]["Authorization"] == "Bearer " + jwt(3, tag="old")

    def test_a_run_out_session_is_a_sign_in_error(self):
        t = Transport(token_reply=(401, None))
        with pytest.raises(sc.SignInError, match="browser login"):
            client(t, access_days=-1).stations()

    def test_a_401_renews_once(self):
        t = Transport(replies=[(401, {}), (200, {"data": [], "total": 0})])
        c = client(t)
        c.stations()
        assert [x["url"].endswith(sc.TOKEN_PATH) for x in t.calls] == [False, True, False]

    def test_a_server_error_is_retried_once(self):
        t = Transport(replies=[(502, None), (200, [])])
        client(t).day(1, date(2026, 11, 10))
        assert len(t.calls) == 2

    def test_errors_never_carry_a_token(self):
        t = Transport(replies=[(403, {"code": "FORBIDDEN"})])
        c = client(t)
        with pytest.raises(sc.SolarmanError) as e:
            c.inverters(5)
        assert "FORBIDDEN" in str(e.value)
        for tok in c.tokens.values():
            assert tok not in str(e.value) and tok.split(".")[1] not in str(e.value)

    def test_a_failed_save_is_kept_and_the_run_goes_on(self):
        def fail(tokens):
            raise sc.SolarmanError("gh secret set failed (exit 1)")
        t = Transport(replies=[(200, {"data": [], "total": 0})])
        c = client(t, access_days=2, on_refresh=fail)
        assert c.stations() == []
        assert c.refreshed and c.save_error == "gh secret set failed (exit 1)"

    def test_no_tokens_is_a_sign_in_error(self):
        with pytest.raises(sc.SignInError):
            sc.SolarmanClient(None)


class TestWriteBack:
    TOKENS = {"access_token": "A.very.secret", "refresh_token": "R.very.secret"}

    def test_both_new_tokens_are_masked_in_actions_only(self):
        lines = []
        sc.mask_in_actions(self.TOKENS, emit=lines.append, env={"GITHUB_ACTIONS": "true"})
        assert lines == ["::add-mask::A.very.secret", "::add-mask::R.very.secret"]
        lines.clear()
        sc.mask_in_actions(self.TOKENS, emit=lines.append, env={})
        assert lines == []

    def test_the_secret_is_written_from_stdin_never_the_command_line(self):
        seen = {}

        class Done:
            returncode = 0

        def run(cmd, **kw):
            seen.update(cmd=cmd, **kw)
            return Done()
        sc.publish_tokens(self.TOKENS, env={"GH_TOKEN": "ghp_x", "GITHUB_REPOSITORY": "o/r"}, run=run)
        assert seen["cmd"] == ["gh", "secret", "set", "SOLARMAN_TOKENS_JSON", "--repo", "o/r"]
        assert json.loads(seen["input"]) == self.TOKENS
        assert not any("secret" in part and "very" in part for part in seen["cmd"])
        assert seen["env"]["GH_TOKEN"] == "ghp_x"

    def test_no_writer_token_is_an_error(self):
        with pytest.raises(sc.SolarmanError, match="GH_TOKEN"):
            sc.publish_tokens(self.TOKENS, env={})

    def test_a_failed_gh_says_its_exit_code_not_its_output(self):
        class Done:
            returncode = 1
            stdout = "A.very.secret"
            stderr = "R.very.secret"
        with pytest.raises(sc.SolarmanError) as e:
            sc.publish_tokens(self.TOKENS, env={"GH_TOKEN": "x"}, run=lambda cmd, **kw: Done())
        assert "exit 1" in str(e.value) and "very" not in str(e.value)

    def test_the_tokens_file_round_trips(self, tmp_path):
        path = tmp_path / "t.json"
        sc.save_tokens(path, self.TOKENS)
        assert sc.load_tokens(path) == self.TOKENS
        assert sc.load_tokens(tmp_path / "missing.json") is None


# --------------------------------------------------------------------------- #
# Readings
# --------------------------------------------------------------------------- #
class TestRows:
    def test_srne_history_parses_with_no_battery_voltage(self):
        rows = every5("2026-11-10 00:04:19", "2026-11-10 00:14:19", srne, load=16.0)
        parsed = sa.parse_day(api_day(rows, SRNE_NAMES), SRNE_PROFILE, TZ)
        assert [r["ts"] for r in parsed] == [r["ts"] for r in rows]
        assert parsed[0] == {**rows[0], "batt_v": None}

    def test_spacing_quirks_in_names_are_ignored(self):
        rows = [deye("2026-11-10 11:45:00", grid=3.9, out=234.1)]
        series = api_day(rows, DEYE_NAMES)
        series[1]["name"] = "Load\xa0Voltage  L1L2"
        parsed = sa.parse_day(series, DEYE_PROFILE, TZ)
        assert parsed[0]["out_v"] == 234.1 and parsed[0]["grid_v"] == 3.9

    def test_three_phases_take_the_lowest_voltage(self):
        t = epoch("2026-11-10 16:04:00")
        names = {"Grid\xa0Voltage\xa0L1": 216.3, "Grid\xa0Voltage\xa0L2": 229.6,
                 "Grid\xa0Voltage\xa0L3": 0.0, "Load Voltage  L1": 218.0,
                 "Load Voltage  L2": 231.2, "Load Voltage  L3": 227.0,
                 "Total Consumption Power": 825.0, "SoC": 100.0, "Battery Voltage": 53.93}
        series = [{"name": n, "detailList": [{"collectionTime": t, "value": str(v)}]}
                  for n, v in names.items()]
        row = sa.parse_day(series, CFG["profiles"]["deye3"], TZ)[0]
        assert row["grid_v"] == 0.0 and row["out_v"] == 218.0 and row["load_w"] == 825.0

    def test_a_row_without_a_grid_reading_is_dropped(self):
        rows = [srne("2026-11-10 00:04:19")]
        series = api_day(rows, SRNE_NAMES)
        series[0]["detailList"] = []
        assert sa.parse_day(series, SRNE_PROFILE, TZ) == []

    def test_the_products_map_to_profiles(self):
        assert sa.profile_for(SRNE, CFG)[0] == "srne"
        assert sa.profile_for(DEYE, CFG)[0] == "deye"
        assert sa.profile_for(DEYE3, CFG)[0] == "deye3"
        assert sa.profile_for("0_9999_1", CFG) == (None, None)

    def test_days_read_first_look_then_from_the_last_reading(self):
        today = date(2026, 11, 11)
        dev = sa.new_device_state()
        assert sa.days_to_read(dev, "2026-11-11 17:35:00", today) == [date(2026, 11, 10), today]
        dev["last_ts"] = "2026-11-11 16:35:00"
        assert sa.days_to_read(dev, "2026-11-11 17:35:00", today) == [today]
        dev["last_ts"] = "2026-11-02 10:00:00"      # the job was down a week
        assert sa.days_to_read(dev, "2026-11-11 17:35:00", today) == [date(2026, 11, 10), today]

    def test_a_long_dead_inverter_reads_no_history(self):
        dev = sa.new_device_state()
        assert sa.days_to_read(dev, "2026-05-24 10:08:42", date(2026, 11, 11)) == []


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #
class TestRules:
    def test_house_on_the_changeover_opens_load_off_timed_from_the_first_reading(self):
        dev = sa.new_device_state()
        rows = every5("2026-11-10 06:00:00", "2026-11-10 06:15:00", srne, load=300.0)
        rows += every5("2026-11-10 06:20:00", "2026-11-10 13:00:00", srne, load=14.0)
        specs = run(dev, rows, "2026-11-10 13:02:00")
        assert kinds(specs) == ["load-off"]
        assert specs[0]["start"] == "2026-11-10 06:20:00"
        assert "or earlier" not in specs[0]["message"]

    def test_one_quiet_reading_opens_nothing(self):
        dev = sa.new_device_state()
        rows = [srne("2026-11-10 06:00:00", load=300.0), srne("2026-11-10 06:05:00", load=12.0),
                srne("2026-11-10 06:10:00", load=300.0)]
        assert run(dev, rows, "2026-11-10 06:12:00") == []

    def test_a_house_back_on_the_inverter_ends_it(self):
        dev = sa.new_device_state()
        run(dev, every5("2026-11-10 06:00:00", "2026-11-10 08:00:00", srne, load=14.0),
            "2026-11-10 08:01:00")
        assert "load-off" in dev["episodes"]
        run(dev, [srne("2026-11-10 08:05:00", load=48.0)], "2026-11-10 08:06:00")
        assert "load-off" in dev["episodes"]      # under 50 W keeps it
        run(dev, [srne("2026-11-10 08:10:00", load=55.0)], "2026-11-10 08:11:00")
        assert "load-off" not in dev["episodes"]

    def test_a_first_look_into_an_episode_says_or_earlier(self):
        dev = sa.new_device_state()
        specs = run(dev, every5("2026-11-10 00:04:19", "2026-11-10 08:00:00", srne, load=10.0),
                    "2026-11-10 08:01:00")
        assert specs[0]["message"].startswith(
            "House load not on the inverter since 2026-11-10 00:04 or earlier")

    def test_an_srne_that_lost_its_battery(self):
        """SoC exactly 0 with the DC bus at ~470 V: the battery has cut out."""
        dev = sa.new_device_state()
        rows = every5("2026-10-20 19:00:00", "2026-10-20 19:10:00", srne, soc=40.0, bus=392.0)
        rows += every5("2026-10-20 19:15:00", "2026-10-21 02:00:00", srne, soc=0.0, bus=470.0)
        specs = run(dev, rows, "2026-10-21 02:03:00")
        assert kinds(specs) == ["battery-open"]
        assert specs[0]["start"] == "2026-10-20 19:15:00"
        assert gda.alarm_triage.classify(specs[0]["message"], gda.ALARM_TRIAGE_CONFIG) == "HARDWARE"
        run(dev, [srne("2026-10-21 02:05:00", soc=12.0, bus=396.0)], "2026-10-21 02:06:00")
        assert "battery-open" not in dev["episodes"]

    def test_an_srne_bus_at_its_night_high_is_not_a_lost_battery(self):
        dev = sa.new_device_state()
        assert run(dev, every5("2026-11-10 19:00:00", "2026-11-10 23:00:00", srne,
                               soc=0.0, bus=416.6), "2026-11-10 23:01:00") == []

    def test_a_deye_that_lost_its_battery(self):
        dev = sa.new_device_state()
        specs = run(dev, every5("2026-11-10 10:00:00", "2026-11-10 10:30:00", deye, batt=0.4),
                    "2026-11-10 10:31:00", profile=DEYE_PROFILE)
        assert kinds(specs) == ["battery-open"]

    def test_output_off_with_the_grid_present(self):
        dev = sa.new_device_state()
        specs = run(dev, every5("2026-11-10 10:00:00", "2026-11-10 10:30:00", deye,
                                out=0.0, load=0.0), "2026-11-10 10:31:00", profile=DEYE_PROFILE)
        # a dead output is not also read as a house off the inverter
        assert kinds(specs) == ["output-off"]
        assert gda.alarm_triage.classify(specs[0]["message"], gda.ALARM_TRIAGE_CONFIG) == "HARDWARE"

    def test_a_ten_hour_cut_ridden_on_the_battery_is_one_grid_loss(self):
        """Hasika-style, 10 Nov 2026: grid 3.9 V from 11:45 to 21:45, output held."""
        dev = sa.new_device_state()
        rows = every5("2026-11-10 11:30:00", "2026-11-10 11:40:00", deye)
        rows += every5("2026-11-10 11:45:00", "2026-11-10 21:40:00", deye, grid=3.9, out=234.0)
        specs = run(dev, rows, "2026-11-10 18:00:00", profile=DEYE_PROFILE)
        assert kinds(specs) == ["grid-loss"]
        assert specs[0]["start"] == "2026-11-10 11:45:00"
        assert "SoC 90%" in specs[0]["detail"] and "battery 49.6 V" in specs[0]["detail"]
        run(dev, rows + [deye("2026-11-10 21:45:00")], "2026-11-10 21:46:00", profile=DEYE_PROFILE)
        assert dev["outage"] is None

    def test_a_dead_output_in_a_cut_is_backup_failed_on_the_outage_clock(self):
        dev = sa.new_device_state()
        rows = every5("2026-11-10 11:30:00", "2026-11-10 11:40:00", srne)
        rows += every5("2026-11-10 11:45:00", "2026-11-10 17:00:00", srne, grid=0.0, out=230.0)
        rows += every5("2026-11-10 17:05:00", "2026-11-10 18:30:00", srne, grid=0.0, out=0.0,
                       load=0.0)
        specs = run(dev, rows, "2026-11-10 18:32:00")
        backup = [s for s in specs if s["kind"] == "backup"][0]
        assert backup["start"] == "2026-11-10 11:45:00" and backup["clock"] == "outage"

    def test_an_inverter_that_has_sent_nothing(self):
        dev = sa.new_device_state()
        specs = sa.open_alarms(dev, "2026-11-11 17:40:00", "2026-05-24 10:08:42", CFG, 65430172)
        assert kinds(specs) == ["silent"]
        assert specs[0]["id"] == "silent@2026-05-24T100842"
        assert specs[0]["start"] == "2026-05-24 10:08:42"
        assert gda.alarm_triage.classify(specs[0]["message"], gda.ALARM_TRIAGE_CONFIG) == "SILENT"

    def test_silence_in_a_cut_rides_the_outage_clock(self):
        dev = sa.new_device_state()
        rows = every5("2026-11-10 11:30:00", "2026-11-10 11:40:00", deye)
        rows += every5("2026-11-10 11:45:00", "2026-11-10 20:00:00", deye, grid=0.0)
        specs = run(dev, rows, "2026-11-10 21:00:00", profile=DEYE_PROFILE,
                    last_data="2026-11-10 20:00:00")
        assert kinds(specs) == ["silent"]
        assert specs[0]["start"] == "2026-11-10 11:45:00" and specs[0]["clock"] == "outage"
        assert "shut down" in specs[0]["message"]
        assert gda.alarm_triage.classify(specs[0]["message"], gda.ALARM_TRIAGE_CONFIG) == "SILENT"

    def test_stale_readings_claim_nothing(self):
        """The history lags the upload: a reading 40 minutes old proves nothing."""
        dev = sa.new_device_state()
        run(dev, every5("2026-11-10 06:00:00", "2026-11-10 12:00:00", srne, load=10.0),
            "2026-11-10 12:01:00")
        assert sa.open_alarms(dev, "2026-11-10 12:40:00", "2026-11-10 12:25:00", CFG, 1) == []

    def test_an_inverter_fault_status(self):
        detail = {"paramCategoryList": [{"fieldList": [
            {"key": "Flag Bit", "value": ""},
            {"key": "Inverter status", "value": "Fault mode"}]}]}
        status = sa.fault_status(detail, CFG)
        assert status == "Inverter status: Fault mode"
        dev = sa.new_device_state()
        run(dev, [srne("2026-11-10 08:00:00"), srne("2026-11-10 08:05:00")], "2026-11-10 08:06:00")
        sa.note_fault(dev, status, "2026-11-10 08:05:00")
        specs = sa.open_alarms(dev, "2026-11-10 08:06:00", "2026-11-10 08:05:00", CFG, 1)
        assert kinds(specs) == ["fault"]
        assert gda.alarm_triage.classify(specs[0]["message"], gda.ALARM_TRIAGE_CONFIG) == "HARDWARE"
        sa.note_fault(dev, sa.fault_status({"paramCategoryList": [{"fieldList": [
            {"key": "Inverter status", "value": "Mains operation"}]}]}, CFG), "2026-11-10 09:00:00")
        assert dev["fault"] is None

    def test_deye_relay_and_battery_states_are_not_faults(self):
        detail = {"paramCategoryList": [{"fieldList": [
            {"key": "Battery Status", "value": "Discharging"},
            {"key": "Grid Relay Status", "value": "Break"}]}]}
        assert sa.fault_status(detail, CFG) is None

    def test_an_alerts_tab_item_is_keyed_on_the_plant(self):
        items = sa.alert_alarms({"id": 63371506}, [
            {"id": 77, "alertName": "Grid Overvoltage", "alertTime": epoch("2026-11-10 09:00:00"),
             "deviceSn": "2310262088"},
            {"id": 78}], TZ)
        assert len(items) == 1
        device, spec = items[0]
        assert device["pn"] == "station" and spec["id"] == "alert-77"
        assert spec["start"] == "2026-11-10 09:00:00" and "2310262088" in spec["detail"]

    def test_a_rule_can_be_switched_off_for_one_plant(self):
        cfg = {**CFG, "rules_off": {"64690226": ["load-off"]}}
        dev = sa.new_device_state()
        sa.advance(dev, every5("2026-11-10 06:00:00", "2026-11-10 13:00:00", srne, load=14.0),
                   SRNE_PROFILE, cfg)
        assert sa.open_alarms(dev, "2026-11-10 13:01:00", "2026-11-10 13:00:00", cfg, 64690226) == []
        assert sa.open_alarms(dev, "2026-11-10 13:01:00", "2026-11-10 13:00:00", cfg, 1) != []

    @pytest.mark.parametrize("kind,cls", [("battery-open", "HARDWARE"), ("output-off", "HARDWARE"),
                                          ("load-off", "LOAD")])
    def test_every_episode_message_classifies(self, kind, cls):
        for since in ("2026-11-10 06:20", "2026-11-10 06:20 or earlier"):
            text = sa.EPISODE_TEXT[kind].format(since=since)
            assert gda.alarm_triage.classify(text, gda.ALARM_TRIAGE_CONFIG) == cls

    def test_no_message_uses_a_comms_word(self):
        """A COMMS word would win the class and the alarm would never be mailed."""
        texts = list(sa.EPISODE_TEXT.values()) + [
            "Inverter has sent nothing to SOLARMAN since x; its last reading showed no grid, so "
            "it has most likely shut down on a flat battery, or the router lost power"]
        comms = gda.ALARM_TRIAGE_CONFIG["match"]["COMMS"]
        for text in texts:
            assert not any(w in text.casefold() for w in comms), text


# --------------------------------------------------------------------------- #
# The fleet, the gate and main()
# --------------------------------------------------------------------------- #
STATION = {"id": 64690226, "name": "Test Home"}
INVERTER = {"id": 254051661, "deviceSn": "SR-0000000000-000001", "productId": SRNE,
            "name": "test inverter"}


class FakeClient:
    def __init__(self, rows, last_data, status="Mains operation", alerts=()):
        self.rows, self.last_data, self.status, self.alerts = rows, last_data, status, list(alerts)
        self.refreshed, self.refresh_error, self.save_error = False, None, None
        self.calls = []

    def access_token(self, force_refresh=False):
        return "t"

    def days_left(self):
        return {"access": 50.0, "refresh": 170.0}

    def stations(self):
        return [STATION]

    def inverters(self, sid):
        return [{**INVERTER, "collectionTime": epoch(self.last_data)}]

    def open_alerts(self, sid):
        return self.alerts

    def detail(self, device_id, sid):
        self.calls.append("detail")
        return {"paramCategoryList": [{"fieldList": [{"key": "Inverter status",
                                                       "value": self.status}]}]}

    def day(self, device_id, day):
        self.calls.append(day.isoformat())
        return api_day([r for r in self.rows if r["ts"][:10] == day.isoformat()
                        and r["ts"] <= self.last_data], SRNE_NAMES)


def gate(client, state, now):
    alarms, read, keep, _ = sa.read_fleet(client, state, CFG, utc(now), TZ)
    sa.must.prune_alarm_state(state, read, keep)
    passed = gda.triage_alarms(alarms, state, sa.PLATFORM, utc(now))
    return gda.filter_alarms_to_send(passed, state)


CHANGEOVER = every5("2026-11-10 06:00:00", "2026-11-10 06:15:00", srne, load=300.0) + \
    every5("2026-11-10 06:20:00", "2026-11-10 23:55:00", srne, load=14.0)


class TestGate:
    def test_held_under_six_hours_mailed_after_with_a_stable_key(self):
        state = {}
        assert gate(FakeClient(CHANGEOVER, "2026-11-10 12:15:00"), state, "2026-11-10 12:18:00") == []
        key = "64690226:SR-0000000000-000001:load-off@2026-11-10T062000"
        assert state[key]["class"] == "LOAD"
        sent = gate(FakeClient(CHANGEOVER, "2026-11-10 12:25:00"), state, "2026-11-10 12:28:00")
        assert [s["alarm_key"] for s in sent] == [key]

    def test_a_second_run_reads_only_what_is_new(self):
        state = {}
        c = FakeClient(CHANGEOVER, "2026-11-10 12:15:00")
        gate(c, state, "2026-11-10 12:18:00")
        assert c.calls[:2] == ["2026-11-09", "2026-11-10"]
        c2 = FakeClient(CHANGEOVER, "2026-11-10 13:15:00")
        gate(c2, state, "2026-11-10 13:18:00")
        assert c2.calls == ["2026-11-10", "detail"]
        c3 = FakeClient(CHANGEOVER, "2026-11-10 13:15:00")
        gate(c3, state, "2026-11-10 13:19:00")
        assert "2026-11-10" not in c3.calls       # nothing new uploaded: no history read

    def test_three_sends_then_quiet_until_the_episode_ends(self):
        state = {}
        gate(FakeClient(CHANGEOVER, "2026-11-10 12:15:00"), state, "2026-11-10 12:18:00")
        key = next(k for k in state if "load-off@" in k)
        state[key].update(send_count=3, last_sent="2026-11-01T00:00:00")
        assert gate(FakeClient(CHANGEOVER, "2026-11-10 23:55:00"), state, "2026-11-10 23:58:00") == []
        assert state[key]["ignored"] is True

    def test_the_key_is_pruned_when_the_episode_ends(self):
        state = {}
        gate(FakeClient(CHANGEOVER, "2026-11-10 12:15:00"), state, "2026-11-10 12:18:00")
        assert any("load-off@" in k for k in state)
        rows = CHANGEOVER + [srne("2026-11-11 00:00:00", load=400.0),
                             srne("2026-11-11 00:05:00", load=400.0)]
        gate(FakeClient(rows, "2026-11-11 00:05:00"), state, "2026-11-11 00:08:00")
        assert not any("load-off@" in k for k in state)

    def test_a_plant_that_failed_keeps_its_keys(self):
        state = {}
        gate(FakeClient(CHANGEOVER, "2026-11-10 12:15:00"), state, "2026-11-10 12:18:00")
        assert any("load-off@" in k for k in state)
        before = json.loads(json.dumps(state))

        class Broken(FakeClient):
            def inverters(self, sid):
                raise sc.SolarmanError("x failed (HTTP 502)")
        gate(Broken(CHANGEOVER, "2026-11-10 14:15:00"), state, "2026-11-10 14:18:00")
        assert state == before


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    tokens = tmp_path / "tokens.json"
    sc.save_tokens(tokens, {"access_token": "a", "refresh_token": "r"})
    monkeypatch.setattr(sa, "load_credentials", lambda: ({}, "admin@example.com"))
    return tmp_path, tokens


def main(workdir, client=None, factory=None, now="2026-11-10 23:58:00", extra=()):
    tmp, tokens = workdir
    factory = factory or (lambda t: client)
    return sa.main(["--tokens", str(tokens), "--state-file", str(tmp / "state.json"), *extra],
                   client_factory=factory, utc_now=utc(now))


class TestMain:
    def test_the_public_log_has_keys_and_counts_never_names(self, workdir, capsys, monkeypatch):
        monkeypatch.setenv("KT_FEATURE_EMAILS_SOLARMAN_ALARMS_ADMIN", "false")
        assert main(workdir, FakeClient(CHANGEOVER, "2026-11-10 23:55:00")) == 0
        out = capsys.readouterr()
        text = out.out + out.err
        assert "64690226:SR-0000000000-000001:load-off@2026-11-10T062000" in text
        assert "1/1 plants read" in text
        for name in ("Test Home", "test inverter", "not on the inverter"):
            assert name not in text
        assert json.loads((workdir[0] / "state.json").read_text())

    def test_no_session_is_mailed_at_once(self, workdir, capsys):
        def factory(tokens):
            raise sc.SignInError("no SOLARMAN sign-in")
        assert main(workdir, factory=factory, extra=["--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "solarman:signin:no-session" in out
        assert "browser login" in out

    def test_a_refused_renewal_is_mailed_while_the_plants_are_still_read(self, workdir, capsys):
        c = FakeClient(CHANGEOVER, "2026-11-10 23:55:00")
        c.refresh_error = "SOLARMAN refused to renew the sign-in (HTTP 400)"
        main(workdir, c, extra=["--dry-run"])
        out = capsys.readouterr().out
        assert "solarman:signin:renew-refused" in out and "1/1 plants read" in out

    def test_a_sign_in_near_its_end_is_mailed_with_its_end_date(self, workdir, capsys):
        class Ending(FakeClient):
            def days_left(self):
                return {"access": 40.0, "refresh": 15.0}
        main(workdir, Ending(CHANGEOVER, "2026-11-10 23:55:00"), extra=["--dry-run"])
        out = capsys.readouterr().out
        assert "solarman:signin:login-due" in out and "1/1 plants read" in out
        assert "ends in 15 days (2026-11-25)" in out and "browser login" in out

    def test_a_fixed_sign_in_forgets_its_alarm(self, workdir, monkeypatch):
        monkeypatch.setenv("KT_FEATURE_EMAILS_SOLARMAN_ALARMS_ADMIN", "false")
        state_file = workdir[0] / "state.json"
        state_file.write_text(json.dumps({"solarman:signin:no-session": {"send_count": 1}}))
        main(workdir, FakeClient(CHANGEOVER, "2026-11-10 08:00:00"), now="2026-11-10 08:03:00")
        assert "solarman:signin:no-session" not in json.loads(state_file.read_text())

    def test_a_failed_plant_list_saves_nothing(self, workdir):
        class Down(FakeClient):
            def stations(self):
                raise sc.SolarmanError("/maintain-s/operating/station/v2/search failed (HTTP 503)")
        assert main(workdir, Down([], "2026-11-10 08:00:00")) == 1
        assert not (workdir[0] / "state.json").exists()

    def test_the_email_names_the_plant_the_alarm_and_the_first_check(self, workdir, capsys):
        main(workdir, FakeClient(CHANGEOVER, "2026-11-10 23:55:00"), extra=["--dry-run"])
        out = capsys.readouterr().out
        assert "Test Home - test inverter (SR-0000000000-000001)" in out
        assert "House load not on the inverter since 2026-11-10 06:20" in out
        assert "class LOAD, send 1/3" in out
        assert "First check: Load alarm open 6h+" in out


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
WORKFLOW = ROOT / ".github" / "workflows" / "trigger-solarman-alarms.yml"


class TestWiring:
    def test_the_workflow_runs_hourly_alone_and_keeps_its_state(self):
        doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        on = doc.get("on") or doc.get(True)
        assert on["schedule"][0]["cron"] == "53 * * * *"
        assert doc["concurrency"]["group"] == "solarman-alarms"
        text = WORKFLOW.read_text(encoding="utf-8")
        assert "--tokens solarman_tokens.json --publish-secret" in text
        assert "state/solarman_alarms_state.json" in text
        assert "push_data_commit.sh --resolve-conflicts" in text

    def test_both_sign_in_files_are_masked(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        assert "mask_credentials.sh src/main/java/org/ktronics/config/credentials.json" in text
        assert "mask_credentials.sh solarman_tokens.json" in text
        assert "GH_TOKEN: ${{ secrets.GH_PAT }}" in text

    def test_there_is_no_dry_run_input(self):
        """A dry run prints plant names, and this repo's logs are public."""
        doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        on = doc.get("on") or doc.get(True)
        assert "dry_run" not in (on["workflow_dispatch"].get("inputs") or {})
        assert "--dry-run" not in WORKFLOW.read_text(encoding="utf-8")

    def test_the_tokens_file_is_never_committed(self):
        assert "solarman_tokens.json" in (ROOT / ".gitignore").read_text(encoding="utf-8").split("\n")

    def test_the_email_channel_has_a_flag(self):
        flags = json.loads((SRC / "main" / "java" / "org" / "ktronics" / "config" / "features.json")
                           .read_text(encoding="utf-8"))
        assert flags["emails"]["solarman_alarms_admin"] is True
        assert sa.EMAIL_CHANNEL == "emails.solarman_alarms_admin"

    def test_the_gate_knows_the_vendor_and_the_silent_class(self):
        import inspect
        assert "'solarman': 'SOLARMAN'" in inspect.getsource(gda.triage_alarms)
        silent = gda.ALARM_TRIAGE_CONFIG["classes"]["SILENT"]
        assert silent["gate"] == "clock" and silent["dwell_hours"] == 6

    def test_the_state_file_is_one_flat_map(self):
        """push_data_commit.sh merges state files key by key."""
        assert isinstance(json.loads((ROOT / "state" / "solarman_alarms_state.json").read_text()), dict)
