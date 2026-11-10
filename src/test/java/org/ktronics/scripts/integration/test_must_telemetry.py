#!/usr/bin/env python3
"""
MUST telemetry alarms through the six-hour gate (UC20) - tests.

No network. Rows are shaped like the fleet's live queryDeviceDataOneDayPaging
of 8-10 Nov 2026: plant-local timestamps, newest first, 100 rows a page, grid
voltage that reads 26-27 V (not 0) on some inverters with the mains gone, and
an output voltage that stays at 229.8 V whether or not anything draws on it.
The battery readings of 9 Nov 04:02-04:46 are Ravi Botheju's own, the night his
inverter shut down twice and no alarm reached the admin.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

SRC = Path(__file__).resolve().parents[6]
ROOT = SRC.parent
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_must_telemetry as m  # noqa: E402
import generate_device_alarms as gda  # noqa: E402
import merge_alarm_state  # noqa: E402

TS = m.TS_FMT
CFG = m.load_config()
DEVICE = {"pid": 1082897, "pn": "D70000210249972877", "devcode": 697, "devaddr": 1,
          "sn": "FFFFFFFF", "alias": "test inverter"}
KEY = f"{DEVICE['pid']}:{DEVICE['pn']}"
TITLES = ["id", "Timestamp", "Battery Voltage", "PV Voltage", "Inverter Voltage", "Batt Current",
          "Charger Current", "Charger Power", "PLoad", "PGrid", "work state", "rated power",
          "Grid Voltage", "PInverter"]

# Ravi Botheju, 9 Nov 2026: the battery's last readings before the inverter went silent.
RAVI_NIGHT = [("04:02:56", 22.6), ("04:07:46", 22.4), ("04:12:35", 22.2), ("04:17:24", 22.1),
              ("04:22:14", 21.9), ("04:27:03", 22.2), ("04:31:52", 22.8), ("04:36:41", 22.7),
              ("04:41:31", 22.3), ("04:46:20", 21.9)]


def row(ts, grid, batt, out=229.8, load=280.0):
    return {"ts": ts, "grid_v": grid, "batt_v": batt, "out_v": out, "load_w": load}


def series(start, end, fn, step_min=5):
    """Rows every step_min minutes from start to end; fn(t) -> (grid, batt[, out, load])."""
    out, t = [], datetime.strptime(start, TS)
    while t <= datetime.strptime(end, TS):
        out.append(row(t.strftime(TS), *fn(t)))
        t += timedelta(minutes=step_min)
    return out


def ravi_rows():
    """8 Nov: grid until 06:05, gone at 06:10. The battery runs down through the
    night, reads flat from 04:07, and the logger goes silent after 04:46 and again
    after 06:08. The grid is back at 07:40 on 9 Nov."""
    rows = series("2026-11-08 00:00:00", "2026-11-08 06:05:00", lambda t: (235.0, 26.5))
    start = datetime(2026, 11, 8, 6, 10)
    span = (datetime(2026, 11, 9, 4, 0) - start).total_seconds()
    rows += series("2026-11-08 06:10:00", "2026-11-09 03:58:00",
                   lambda t: (26.9, round(26.0 - 3.3 * (t - start).total_seconds() / span, 2)))
    rows += [row(f"2026-11-09 {hms}", 26.6, v) for hms, v in RAVI_NIGHT]
    rows += [row("2026-11-09 06:03:32", 0.0, 23.1), row("2026-11-09 06:08:28", 0.0, 22.2),
             row("2026-11-09 07:40:13", 239.7, 25.7, 239.4, 39.0),
             row("2026-11-09 07:45:07", 237.9, 25.5, 237.6, 228.0)]
    return rows


def upto(rows, now):
    return [r for r in rows if r["ts"] <= now]


def utc(local):
    return (datetime.strptime(local, TS) - timedelta(minutes=330)).replace(tzinfo=timezone.utc)


def run_once(dev, rows, now, state, cfg=CFG, grid_expected=True):
    """advance -> open alarms -> prune -> six-hour gate -> 3x/4h -> mail sent, as main() runs them.
    The 4-hour spacing reads the wall clock, so within one test a sent alarm is not sent again."""
    m.advance(dev, [r for r in upto(rows, now) if not dev["last_ts"] or r["ts"] > dev["last_ts"]], cfg)
    specs = m.open_alarms(dev, now, cfg, grid_expected)
    alarms = [m.to_gate_alarm(s, DEVICE, "Test", 330) for s in specs]
    m.prune_alarm_state(state, {KEY}, m.live_keys(DEVICE, dev, grid_expected))
    passed = gda.triage_alarms(alarms, state, m.PLATFORM, utc(now))
    sent = gda.filter_alarms_to_send(passed, state)
    gda.update_alarm_state(state, sent)
    return specs, sent


@pytest.fixture(autouse=True)
def gate_on(monkeypatch):
    monkeypatch.setenv("KT_FEATURE_ALERTS_SIX_HOUR_GATE", "on")


# --------------------------------------------------------------------------- #
# Reading the feed
# --------------------------------------------------------------------------- #
def api_page(rows, total=None):
    def field(r):
        values = {"Timestamp": r["ts"], "Battery Voltage": r["batt_v"], "Inverter Voltage": r["out_v"],
                  "PLoad": r["load_w"], "Grid Voltage": r["grid_v"], "work state": "OffGrid"}
        return [str(values.get(t, "0")) for t in TITLES]
    return {"total": total if total is not None else len(rows),
            "title": [{"title": t} for t in TITLES],
            "row": [{"field": field(r)} for r in rows]}


def test_parse_page_reads_the_live_titles_newest_first():
    rows = m.parse_page(api_page([row("2026-11-10 07:37:31", 236.2, 25.4, 236.0, 312.0),
                                  row("2026-11-10 07:32:40", 0.0, 24.9)]))
    assert [r["ts"] for r in rows] == ["2026-11-10 07:37:31", "2026-11-10 07:32:40"]
    assert rows[0] == {"ts": "2026-11-10 07:37:31", "grid_v": 236.2, "batt_v": 25.4,
                       "out_v": 236.0, "load_w": 312.0}


def test_a_feed_without_grid_or_battery_voltage_is_not_watched():
    page = api_page([row("2026-11-10 07:37:31", 236.2, 25.4)])
    page["title"] = [{"title": t} for t in TITLES if t != "Grid Voltage"]
    assert m.parse_page(page) is None


def test_a_row_without_its_readings_is_dropped():
    page = api_page([row("2026-11-10 07:37:31", 236.2, 25.4)])
    page["row"][0]["field"][TITLES.index("Grid Voltage")] = ""
    assert m.parse_page(page) == []


class FakeFeed:
    """day_page() over a dict of plant-local date -> rows, paged newest first."""

    def __init__(self, rows, page_size=m.PAGE_SIZE):
        self.days, self.calls, self.size = {}, [], page_size
        for r in sorted(rows, key=lambda r: r["ts"], reverse=True):
            self.days.setdefault(r["ts"][:10], []).append(r)

    def __call__(self, day, page):
        self.calls.append((day, page))
        items = self.days.get(day, [])[page * self.size:(page + 1) * self.size]
        return items, (page + 1) * self.size < len(self.days.get(day, []))


def test_fetch_reads_back_to_the_last_reading_and_no_further():
    feed = FakeFeed(series("2026-11-09 20:00:00", "2026-11-10 07:40:00", lambda t: (235.0, 26.0)))
    rows = m.fetch_new_rows(feed, datetime(2026, 11, 10).date(), "2026-11-10 06:00:00", 7, 180)
    assert rows[0]["ts"] == "2026-11-10 06:05:00" and rows[-1]["ts"] == "2026-11-10 07:40:00"
    assert feed.calls == [("2026-11-10", 0)]


def test_fetch_pages_through_a_day_and_into_the_one_before():
    feed = FakeFeed(series("2026-11-09 20:00:00", "2026-11-10 07:40:00", lambda t: (235.0, 26.0)))
    rows = m.fetch_new_rows(feed, datetime(2026, 11, 10).date(), "2026-11-09 22:00:00", 7, 180)
    assert rows[0]["ts"] == "2026-11-09 22:05:00"
    assert feed.calls == [("2026-11-10", 0), ("2026-11-09", 0)]
    assert len(rows) == len({r["ts"] for r in rows}) and rows == sorted(rows, key=lambda r: r["ts"])


def test_first_look_walks_back_until_a_reading_shows_grid():
    """Gayan-IMH on 10 Nov: no grid since 23:27 on 5 Nov. The first look must
    find that start, not time the outage from this morning."""
    rows = series("2026-11-05 22:00:00", "2026-11-05 23:22:00", lambda t: (230.0, 26.8))
    rows += series("2026-11-05 23:27:30", "2026-11-10 07:45:00", lambda t: (0.0, 25.5))
    feed = FakeFeed(rows)
    got = m.fetch_new_rows(feed, datetime(2026, 11, 10).date(), None, 7, 180)
    assert {d for d, _ in feed.calls} == {"2026-11-10", "2026-11-09", "2026-11-08",
                                          "2026-11-07", "2026-11-06", "2026-11-05"}
    dev = m.advance(m.new_device_state(), got, CFG)
    assert dev["outage"]["start"] == "2026-11-05 23:27:30"


def test_first_look_stops_at_the_look_back_limit():
    feed = FakeFeed(series("2026-10-20 00:00:00", "2026-11-10 07:45:00", lambda t: (0.0, 25.5), 60))
    m.fetch_new_rows(feed, datetime(2026, 11, 10).date(), None, 3, 180)
    assert sorted({d for d, _ in feed.calls}) == ["2026-11-07", "2026-11-08", "2026-11-09", "2026-11-10"]


def test_a_feed_that_is_not_a_must_returns_none():
    assert m.fetch_new_rows(lambda d, p: (None, False), datetime(2026, 11, 10).date(),
                            None, 7, 180) is None


# --------------------------------------------------------------------------- #
# Outages
# --------------------------------------------------------------------------- #
def test_two_readings_without_grid_open_an_outage_timed_from_the_first():
    dev = m.advance(m.new_device_state(), [row("2026-11-08 06:05:00", 235.0, 26.5),
                                           row("2026-11-08 06:10:00", 26.9, 26.4),
                                           row("2026-11-08 06:15:00", 0.0, 26.3)], CFG)
    assert dev["outage"]["start"] == "2026-11-08 06:10:00"
    assert dev["outage"]["start_known"] is True


def test_one_reading_without_grid_opens_nothing():
    dev = m.advance(m.new_device_state(), [row("2026-11-08 06:05:00", 235.0, 26.5),
                                           row("2026-11-08 06:10:00", 0.0, 26.4),
                                           row("2026-11-08 06:15:00", 233.0, 26.5)], CFG)
    assert dev["outage"] is None and dev["pending"] is None


def test_a_brownout_neither_opens_nor_closes_an_outage():
    dev = m.advance(m.new_device_state(), [row("2026-11-08 06:05:00", 0.0, 26.0),
                                           row("2026-11-08 06:10:00", 0.0, 26.0),
                                           row("2026-11-08 06:15:00", 150.0, 26.0)], CFG)
    assert dev["outage"]["start"] == "2026-11-08 06:05:00"
    dev = m.advance(dev, [row("2026-11-08 06:20:00", 185.0, 26.0)], CFG)
    assert dev["outage"] is None


def test_an_outage_opens_across_two_runs():
    dev = m.advance(m.new_device_state(), [row("2026-11-08 06:05:00", 235.0, 26.5),
                                           row("2026-11-08 06:10:00", 0.0, 26.4)], CFG)
    assert dev["outage"] is None and dev["pending"]["ts"] == "2026-11-08 06:10:00"
    dev = m.advance(dev, [row("2026-11-08 06:15:00", 0.0, 26.3)], CFG)
    assert dev["outage"]["start"] == "2026-11-08 06:10:00"


def test_a_first_look_into_an_outage_says_or_earlier():
    dev = m.advance(m.new_device_state(), series("2026-11-10 00:00:00", "2026-11-10 07:40:00",
                                                 lambda t: (0.0, 25.5)), CFG)
    assert dev["outage"]["start_known"] is False
    (spec,) = m.open_alarms(dev, "2026-11-10 07:43:00", CFG, True)
    assert spec["message"].endswith("since 2026-11-10 00:00 or earlier")


# --------------------------------------------------------------------------- #
# Grid loss through the gate
# --------------------------------------------------------------------------- #
def test_grid_loss_is_held_under_six_hours_and_mailed_after():
    rows, state, dev = ravi_rows(), {}, m.new_device_state()
    _, sent = run_once(dev, rows, "2026-11-08 11:13:00", state)
    assert sent == []
    assert "dwell" in state[f"{KEY}:grid-loss@2026-11-08T061000"]["suppressed_reason"]
    _, sent = run_once(dev, rows, "2026-11-08 12:13:00", state)
    assert [s["alarm_key"] for s in sent] == [f"{KEY}:grid-loss@2026-11-08T061000"]
    assert sent[0]["alarm"]["started_at_utc"] == "2026-11-08T00:40:00+00:00"


def test_logger_silence_never_counts_as_grid_still_off():
    """Two hours of no grid, then the Wi-Fi drops: 7 h later nothing may say the
    grid has been off 6 hours, because nothing has read it."""
    dev = m.advance(m.new_device_state(),
                    series("2026-11-08 06:00:00", "2026-11-08 08:00:00", lambda t: (0.0, 26.0)), CFG)
    assert m.open_alarms(dev, "2026-11-08 13:13:00", CFG, True) == []
    assert m.live_keys(DEVICE, dev, True) == {f"{KEY}:grid-loss@2026-11-08T060000"}


def test_a_plant_built_without_grid_raises_no_grid_loss_but_its_battery_is_watched():
    rows = series("2026-11-08 18:00:00", "2026-11-09 04:00:00",
                  lambda t: (0.0, 22.4 if t.hour < 12 else 25.0))
    dev = m.advance(m.new_device_state(), rows, CFG)
    (spec,) = m.open_alarms(dev, "2026-11-09 04:03:00", CFG, grid_expected=False)
    assert spec["kind"] == "backup" and spec["clock"] == "battery"
    assert spec["start"] == dev["backup"]["start"]


def test_the_no_grid_list_is_read_by_label(tmp_path):
    cfg = dict(CFG, no_grid_expected=[{"label": "Sandamal Silva", "reason": "x", "since": "2026-11-10"}])
    assert m.exclusions.normalize("SandamalSilva") in m.no_grid_labels(cfg)


@pytest.mark.parametrize("message, expected", [
    ("Grid loss: no AC input on the inverter since 2026-11-08 06:10", "GRID"),
    ("Battery flat at 22.2 V with no grid: the inverter will cut off soon", "SOC"),
    ("Backup failed: battery flat at 21.9 V with no grid, and no reading since 04:46; "
     "the inverter has most likely shut down", "SOC"),
    ("Backup failed: AC output at 0 V with no grid; the house has no power", "SOC"),
])
def test_each_message_lands_in_its_class(message, expected):
    assert gda.alarm_triage.classify(message, gda.ALARM_TRIAGE_CONFIG) == expected


# --------------------------------------------------------------------------- #
# Backup failed, on the outage's clock
# --------------------------------------------------------------------------- #
def test_ravi_8_9_nov_is_mailed_before_and_after_the_shutdown():
    rows, state, dev = ravi_rows(), {}, m.new_device_state()
    for now in ("2026-11-08 12:13:00", "2026-11-09 03:13:00"):
        run_once(dev, rows, now, state)

    specs, sent = run_once(dev, rows, "2026-11-09 04:13:00", state)
    backup = [s for s in sent if s["alarm"]["kind"] == "backup"]
    assert len(backup) == 1
    assert backup[0]["alarm"]["message"].startswith("Battery flat at 22.2 V")
    # The outage's clock, 22 h old, not the episode's six minutes.
    assert backup[0]["alarm"]["started_at_utc"] == "2026-11-08T00:40:00+00:00"
    key = backup[0]["alarm_key"]
    assert key == f"{KEY}:backup@2026-11-09T040746"

    specs, _ = run_once(dev, rows, "2026-11-09 05:13:00", state)
    shut = [s for s in specs if s["kind"] == "backup"]
    assert shut[0]["message"].startswith("Backup failed: battery flat at 21.9 V")
    assert "no reading since 04:46" in shut[0]["message"]
    assert shut[0]["id"] == key.split(":", 2)[2]           # one episode, one key

    run_once(dev, rows, "2026-11-09 07:13:00", state)      # silent again after 06:08
    assert key in state
    run_once(dev, rows, "2026-11-09 08:13:00", state)      # grid back at 07:40
    assert not [k for k in state if k.startswith(KEY)]


def test_a_single_flat_reading_is_not_enough_while_the_logger_is_alive():
    dev = m.advance(m.new_device_state(), [row("2026-11-09 04:00:00", 0.0, 23.0),
                                           row("2026-11-09 04:05:00", 0.0, 23.0),
                                           row("2026-11-09 04:10:00", 0.0, 22.4)], CFG)
    assert [s["kind"] for s in m.open_alarms(dev, "2026-11-09 04:12:00", CFG, True)] == ["grid-loss"]


def test_zero_watts_with_the_output_alive_is_not_a_failure():
    """Jeremy, 27-29 Oct 2026: no grid for 7-11 h, 0 W load, output 229.8 V,
    battery 24.4-25 V. Nothing was drawing on a working inverter."""
    rows = series("2026-10-28 18:30:00", "2026-10-29 05:00:00", lambda t: (0.0, 24.6, 229.8, 0.0))
    dev = m.advance(m.new_device_state(), rows, CFG)
    assert dev["backup"] is None
    assert [s["kind"] for s in m.open_alarms(dev, "2026-10-29 05:03:00", CFG, True)] == ["grid-loss"]


def test_two_readings_of_a_dead_output_are_a_failure():
    rows = [row("2026-11-09 01:00:00", 0.0, 25.0), row("2026-11-09 01:05:00", 0.0, 25.0),
            row("2026-11-09 01:10:00", 0.0, 25.0, 0.0, 0.0), row("2026-11-09 01:15:00", 0.0, 25.0, 0.0, 0.0)]
    dev = m.advance(m.new_device_state(), rows, CFG)
    backup = [s for s in m.open_alarms(dev, "2026-11-09 01:17:00", CFG, True) if s["kind"] == "backup"]
    assert backup[0]["message"].startswith("Backup failed: AC output at 0 V")


def test_a_backup_episode_ends_when_the_battery_is_recharged_or_the_grid_is_back():
    dev = m.advance(m.new_device_state(), [row("2026-11-09 04:00:00", 0.0, 22.4),
                                           row("2026-11-09 04:05:00", 0.0, 22.3)], CFG)
    assert dev["backup"]
    m.advance(dev, [row("2026-11-09 09:00:00", 0.0, 24.9)], CFG)
    assert dev["backup"], "23 V is not a recharged 24 V battery"
    m.advance(dev, [row("2026-11-09 10:00:00", 0.0, 25.0)], CFG)
    assert dev["backup"] is None
    m.advance(dev, [row("2026-11-09 20:00:00", 0.0, 22.4), row("2026-11-09 21:00:00", 236.0, 26.0)], CFG)
    assert dev["backup"] is None


@pytest.mark.parametrize("readings, nominal, flat", [
    ([25.0, 26.5, 24.1], 24, 22.5),
    ([51.0, 53.2, 49.5], 48, 45.5),
    ([12.6, 13.1], None, None),
])
def test_the_flat_marks_are_the_owners(readings, nominal, flat):
    dev = dict(m.new_device_state(), nominal_v=m.nominal_voltage(readings))
    assert dev["nominal_v"] == nominal
    assert m.flat_mark(dev, CFG) == flat


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def test_prune_forgets_a_closed_outage_but_not_an_unread_device():
    state = {f"{KEY}:grid-loss@2026-11-08T061000": {"send_count": 3},
             "1187499:D70000210184361276:grid-loss@2026-11-05T232730": {"send_count": 1},
             f"device:{KEY}": {"last_ts": "x"}}
    m.prune_alarm_state(state, {KEY}, set())
    assert sorted(state) == ["1187499:D70000210184361276:grid-loss@2026-11-05T232730", f"device:{KEY}"]


def test_the_state_is_one_flat_map_the_push_merge_can_union():
    ours = {f"{KEY}:grid-loss@2026-11-08T061000": {"send_count": 2, "ignored": False},
            f"device:{KEY}": {"last_ts": "2026-11-08 20:13:00"}}
    theirs = {f"{KEY}:grid-loss@2026-11-08T061000": {"send_count": 1, "ignored": False},
              "device:1:2": {"last_ts": "2026-11-08 20:12:00"}}
    merged = merge_alarm_state.merge_states(ours, theirs)
    assert merged[f"{KEY}:grid-loss@2026-11-08T061000"]["send_count"] == 2
    assert {f"device:{KEY}", "device:1:2"} <= set(merged)


# --------------------------------------------------------------------------- #
# main(): a fake fleet, no network
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, rows):
        self.feed = FakeFeed(rows)

    def devices(self):
        return [DEVICE]

    def day_page(self, device, day, page):
        return self.feed(day, page)


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"company_key": "k", "accounts": [
        {"label": "Ravi Test", "username": "ravi-user", "password": "s3cret-pw", "email": "x@y"}]}))
    monkeypatch.setattr(m, "CREDENTIALS_PATH", creds)
    monkeypatch.setenv("KT_FEATURE_EMAILS_MUST_TELEMETRY_ADMIN", "false")
    return tmp_path


def test_main_prints_keys_not_names_and_a_dry_run_saves_nothing(fleet, capsys):
    state_file = fleet / "state.json"
    rc = m.main(["--dry-run", "--state-file", str(state_file)],
                client_factory=lambda u, p, k: FakeClient(ravi_rows()), utc_now=utc("2026-11-08 12:13:00"))
    out = capsys.readouterr().out
    assert rc == 0 and not state_file.exists()
    log, _, body = out.partition("\n\n")
    assert f"{KEY}:grid-loss@2026-11-08T061000" in log
    for secret in ("Ravi Test", "ravi-user", "s3cret-pw"):
        assert secret not in log
    assert "Grid loss: no AC input on the inverter since 2026-11-08 06:10" in body
    assert "send 1/3" in body and "First check:" in body


def test_main_with_the_flag_off_mails_nothing_and_keeps_its_state(fleet, capsys):
    state_file = fleet / "state.json"
    assert m.main(["--state-file", str(state_file)], client_factory=lambda u, p, k: FakeClient(ravi_rows()),
                  utc_now=utc("2026-11-08 12:13:00")) == 0
    assert "nothing mailed" in capsys.readouterr().out
    state = json.loads(state_file.read_text())
    assert state[f"device:{KEY}"]["outage"]["start"] == "2026-11-08 06:10:00"
    # Switched off is not sent: nothing counts against the alarm's three sends.
    assert f"{KEY}:grid-loss@2026-11-08T061000" not in state


def test_an_account_that_cannot_be_read_keeps_its_state(fleet):
    state_file = fleet / "state.json"
    before = {f"device:{KEY}": {"last_ts": "2026-11-08 06:00:00"},
              f"{KEY}:grid-loss@2026-11-08T061000": {"send_count": 2, "last_sent": None, "ignored": False}}
    state_file.write_text(json.dumps(before))

    def refuse(u, p, k):
        raise m.FetchError("login failed")

    assert m.main(["--state-file", str(state_file)], client_factory=refuse,
                  utc_now=utc("2026-11-08 12:13:00")) == 1
    assert json.loads(state_file.read_text()) == before


def test_a_dark_logger_costs_one_call_a_run_after_its_first_look(fleet):
    clients = []

    def factory(u, p, k):
        clients.append(FakeClient([]))
        return clients[-1]

    state_file = fleet / "state.json"
    m.main(["--state-file", str(state_file)], client_factory=factory, utc_now=utc("2026-11-10 07:43:00"))
    assert len(clients[0].feed.calls) == CFG["first_look_back_days"] + 1
    m.main(["--state-file", str(state_file)], client_factory=factory, utc_now=utc("2026-11-10 08:43:00"))
    assert clients[1].feed.calls == [("2026-11-10", 0)]


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def test_the_config_ships_the_owners_marks_and_no_plant_off_grid():
    assert CFG["flat_battery_v"] == {"24": 22.5, "48": 45.5}
    assert CFG["no_grid_expected"] == []


def test_the_email_has_its_own_flag():
    flags = json.loads((SRC / "main" / "java" / "org" / "ktronics" / "config" / "features.json").read_text())
    assert flags["emails"]["must_telemetry_admin"] is True


def test_the_workflow_runs_hourly_and_commits_its_state():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "trigger-must-telemetry.yml").read_text(encoding="utf-8"))
    on = doc.get("on") or doc.get(True)
    assert on["schedule"][0]["cron"] == "43 * * * *"
    text = json.dumps(doc)
    assert "check_must_telemetry.py" in text
    assert "state/must_telemetry_state.json" in text
    assert "push_data_commit.sh" in text
