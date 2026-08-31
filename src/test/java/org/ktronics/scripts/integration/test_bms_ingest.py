#!/usr/bin/env python3
"""Tests for the ESP32 JK-BMS module's cloud ingest (bms-module/scripts/ingest_bms_reading.py).

This is the DIY ESP32 -> repository_dispatch path, not the PH1000/PACEEX BMS
(see test_bms_cell_frame.py for that one). No network and no SMTP: the mailer is
monkeypatched, and data/state are redirected to tmp_path."""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[7]
BMS_MODULE = REPO_ROOT / "bms-module"
sys.path.insert(0, str(BMS_MODULE / "scripts"))

import ingest_bms_reading as ing  # noqa: E402

NORMAL = {"device": "kt-bms-test", "voltage": 52.4, "current": -12.6, "power": -660,
          "soc": 78, "temp": 28.5, "min_cell": 3.29, "max_cell": 3.31, "delta_cell": 0.02}
BAD = {"device": "kt-bms-test", "voltage": 47.0, "current": -120, "power": -5600,
       "soc": 9, "temp": 55, "min_cell": 3.00, "max_cell": 3.30, "delta_cell": 0.30}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect the module's data/state to tmp_path and capture emails instead of sending."""
    data, state = tmp_path / "data", tmp_path / "state"
    monkeypatch.setattr(ing, "DATA_DIR", data)
    monkeypatch.setattr(ing, "STATE_DIR", state)
    monkeypatch.setattr(ing, "LATEST_FILE", state / "bms_latest.json")
    monkeypatch.setattr(ing, "ALERT_STATE_FILE", state / "bms_alerts_state.json")

    sent = []

    def fake_send(to_addr, subject, body, *a, **kw):
        sent.append({"to": to_addr, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(ing, "send_email_smtp", fake_send)
    return {"tmp": tmp_path, "data": data, "state": state, "sent": sent}


def run(payload, monkeypatch):
    monkeypatch.setenv("BMS_PAYLOAD", json.dumps(payload))
    ing.main()


class TestIngest:
    def test_appends_csv_with_header_then_rows(self, sandbox, monkeypatch):
        run(NORMAL, monkeypatch)
        run(NORMAL, monkeypatch)
        lines = (sandbox["data"] / "kt-bms-test.csv").read_text(encoding="utf-8").strip().splitlines()
        assert lines[0].split(",") == ing.CSV_FIELDS      # header written once
        assert len(lines) == 3                            # header + 2 readings

    def test_latest_snapshot_per_device(self, sandbox, monkeypatch):
        run(NORMAL, monkeypatch)
        run({**NORMAL, "device": "kt-bms-other", "soc": 42}, monkeypatch)
        latest = json.loads((sandbox["state"] / "bms_latest.json").read_text(encoding="utf-8"))
        assert set(latest) == {"kt-bms-test", "kt-bms-other"}
        assert latest["kt-bms-other"]["soc"] == 42
        assert latest["kt-bms-test"]["voltage"] == 52.4

    def test_normal_reading_sends_nothing(self, sandbox, monkeypatch):
        run(NORMAL, monkeypatch)
        assert sandbox["sent"] == []
        assert not (sandbox["state"] / "bms_alerts_state.json").exists()

    def test_payload_without_device_exits(self, sandbox, monkeypatch):
        with pytest.raises(SystemExit) as e:
            run({"voltage": 52.4}, monkeypatch)
        assert e.value.code == 1


class TestThresholds:
    def test_all_bad_reading_thresholds_fire(self):
        codes = [c for c, _ in ing.evaluate_alerts("d", BAD)]
        assert codes == ["low_soc", "under_voltage", "over_temp", "over_current", "cell_imbalance"]

    def test_over_voltage_fires_separately(self):
        codes = [c for c, _ in ing.evaluate_alerts("d", {**NORMAL, "voltage": 59.0})]
        assert codes == ["over_voltage"]

    def test_missing_fields_are_skipped_not_crashed(self):
        assert ing.evaluate_alerts("d", {"device": "d"}) == []

    def test_alert_email_lists_each_issue(self, sandbox, monkeypatch):
        run(BAD, monkeypatch)
        assert len(sandbox["sent"]) == 1
        body = sandbox["sent"][0]["body"]
        assert "Low battery" in body and "Over-temperature" in body and "Cell imbalance" in body
        assert "kt-bms-test" in body


class TestCooldown:
    def test_second_reading_within_cooldown_does_not_resend(self, sandbox, monkeypatch):
        run(BAD, monkeypatch)
        run(BAD, monkeypatch)
        assert len(sandbox["sent"]) == 1          # still just the first email

    def test_resends_after_cooldown_expires(self, sandbox, monkeypatch):
        run(BAD, monkeypatch)
        stale = (datetime.now(timezone.utc)
                 - timedelta(hours=ing.REALERT_COOLDOWN_HOURS + 1)).isoformat(timespec="seconds")
        path = sandbox["state"] / "bms_alerts_state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        for k in state:
            state[k]["last_sent"] = stale
        path.write_text(json.dumps(state), encoding="utf-8")

        run(BAD, monkeypatch)
        assert len(sandbox["sent"]) == 2

    def test_failed_send_does_not_start_cooldown(self, sandbox, monkeypatch):
        """REGRESSION: a failed SMTP send used to record last_sent anyway, silently
        swallowing the alert for the whole cooldown window. It must now exit non-zero
        (so the workflow run goes red) and leave the state untouched, so the very next
        reading retries the alert."""
        monkeypatch.setattr(ing, "send_email_smtp", lambda *a, **kw: False)
        with pytest.raises(SystemExit) as e:
            run(BAD, monkeypatch)
        assert e.value.code == 1
        assert not (sandbox["state"] / "bms_alerts_state.json").exists()

        # ...and the next reading retries rather than sitting in a phantom cooldown
        retried = []

        def send_ok(to_addr, subject, body, *a, **kw):
            retried.append(subject)
            return True

        monkeypatch.setattr(ing, "send_email_smtp", send_ok)
        run(BAD, monkeypatch)
        assert len(retried) == 1

    def test_reading_is_still_recorded_when_the_email_fails(self, sandbox, monkeypatch):
        """The CSV row must survive a failed send - the workflow commits it with if: always()."""
        monkeypatch.setattr(ing, "send_email_smtp", lambda *a, **kw: False)
        with pytest.raises(SystemExit):
            run(BAD, monkeypatch)
        assert (sandbox["data"] / "kt-bms-test.csv").exists()
        latest = json.loads((sandbox["state"] / "bms_latest.json").read_text(encoding="utf-8"))
        assert latest["kt-bms-test"]["soc"] == 9


class TestFirmwareWiring:
    """The firmware's cloud push is untestable without hardware, but the values that
    silently break it are checkable here."""

    FIRMWARE = BMS_MODULE / "firmware" / "jk-bms-monitor.yaml"

    def test_dispatch_url_targets_the_real_repo(self):
        """REGRESSION: the firmware posted to ktronicsdev/IOT, but this repo is
        ktronicsdev/IoTDeviceMonitor - every push would have 404'd."""
        text = self.FIRMWARE.read_text(encoding="utf-8")
        assert "ktronicsdev/IoTDeviceMonitor" in text
        assert "repos/ktronicsdev/IOT/" not in text

    def test_event_type_matches_the_workflow_trigger(self):
        fw = self.FIRMWARE.read_text(encoding="utf-8")
        wf = (REPO_ROOT / ".github/workflows/trigger-bms-ingest.yml").read_text(encoding="utf-8")
        assert 'event_type' in fw and 'bms_reading' in fw
        assert "types: [bms_reading]" in wf

    def test_payload_keys_match_what_the_ingest_reads(self):
        """Every key the firmware sends must be one the ingest stores, or data is dropped."""
        fw = self.FIRMWARE.read_text(encoding="utf-8")
        for field in ing.CSV_FIELDS:
            if field == "timestamp":
                continue                       # added server-side, not sent by the device
            assert field in fw, f"firmware never sends '{field}'"

    def test_no_wifi_password_is_compiled_in(self):
        """One .bin must work at every site; customer WiFi is entered on site."""
        fw = self.FIRMWARE.read_text(encoding="utf-8")
        assert "wifi_password" not in fw and "wifi_ssid" not in fw
        assert "improv_serial:" in fw and "captive_portal:" in fw
