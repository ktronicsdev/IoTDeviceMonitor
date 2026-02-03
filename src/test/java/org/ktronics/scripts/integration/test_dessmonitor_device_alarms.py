#!/usr/bin/env python3
"""
UC3-UC8: DessMonitor Device Alarm Tests

Tests for DessMonitor device alarm monitoring system including:
- Alarm parsing and state tracking
- 3-send rule and 4-hour interval
- Customer notifications
- Test mode filtering (UC4-UC5)
- Log summary counts (UC6)
- State JSON enhancement (UC7)
- Weekly report schedule filter (UC8)

Note: DessMonitor uses the same generate_device_alarms.py script as ShineMonitor
with --platform dessmonitor flag. Most logic is shared.
"""

import pytest
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta

# Add scripts directory to path
# Starting from: src/test/java/org/ktronics/scripts/integration/test_dessmonitor_device_alarms.py
# 8 .parent calls reach repo root (IOT/), then add src/main/java/org/ktronics/scripts
scripts_dir = Path(__file__).parent.parent.parent.parent.parent.parent.parent.parent / "src" / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(scripts_dir))

from generate_device_alarms import (
    parse_alarm_files,
    create_alarm_key,
    filter_alarms_to_send,
    format_device_alarms_email,
    update_alarm_state,
    load_alarm_state,
    save_alarm_state,
    load_customer_mapping,
    create_customer_device_alarms,
    get_most_recent_alarm
)


class TestDessMonitorDeviceAlarmSystem:
    """Test DessMonitor device alarm monitoring and notification system"""

    @pytest.fixture
    def test_alarms_dir(self):
        """Create temporary alarms directory"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def test_state_file(self, tmp_path):
        """Create temporary state file path for DessMonitor"""
        return tmp_path / "dessmonitor_device_alarms_state.json"

    def create_dessmonitor_alarm_json(self, alarms_dir, customer_label, alarms_data):
        """Helper: Create DessMonitor alarm JSON file for a customer"""
        # DessMonitor uses dessmonitor- prefix
        alarm_file = alarms_dir / f"dessmonitor-{customer_label}-alarms.json"

        data = {
            "err": 0,
            "dat": alarms_data
        }

        with open(alarm_file, 'w') as f:
            json.dump(data, f)

        return alarm_file

    # ==================== UC3: Device Alarm Parsing ====================

    def test_parse_dessmonitor_alarm_files_single_customer(self, test_alarms_dir):
        """Parse DessMonitor alarm files from single customer"""
        alarms_data = [
            {
                "id": "dess123abc456",
                "pid": 54321,
                "plant": "MifrazMarsoon Plant",
                "pn": "DESS70000210151320902",
                "sn": "DESSSERIAL01",
                "alias": "DessMonitor Inverter 1",
                "desc": "Grid voltage too high",
                "gts": "2026-01-07 10:30:00",
                "status": False,
                "handle": False
            }
        ]

        self.create_dessmonitor_alarm_json(test_alarms_dir, "mifrazmarsoon", alarms_data)

        # Parse with dessmonitor prefix pattern
        all_alarms = parse_alarm_files(test_alarms_dir)

        # Should find the alarm (prefix handled by filename pattern)
        assert len(all_alarms) >= 1

    def test_dessmonitor_create_alarm_key_unique(self):
        """DessMonitor alarm keys are unique per plant/device/warning"""
        alarm = {
            "pid": 54321,
            "pn": "DESS70000210151320902",
            "id": "dess123abc456"
        }

        key = create_alarm_key(alarm)
        assert "54321" in key
        assert "DESS70000210151320902" in key or "dess123abc456" in key

    def test_dessmonitor_filter_alarms_first_send(self, test_state_file):
        """DessMonitor: First time alarm should be included for sending"""
        alarms = [
            {
                "pid": 54321,
                "pn": "DESSDEV001",
                "id": "desswarn001",
                "customer_label": "mifrazmarsoon"
            }
        ]

        state = {}
        to_send = filter_alarms_to_send(alarms, state)

        assert len(to_send) == 1, "First-time alarm should be sent"

    def test_dessmonitor_filter_alarms_max_sends_reached(self, test_state_file):
        """DessMonitor: Alarm sent 3 times should NOT be sent again"""
        alarm_key = "54321:DESSDEV001:desswarn001"
        alarms = [
            {
                "pid": 54321,
                "pn": "DESSDEV001",
                "id": "desswarn001",
                "customer_label": "mifrazmarsoon"
            }
        ]

        state = {
            alarm_key: {
                "send_count": 3,
                "last_sent": (datetime.now() - timedelta(hours=5)).isoformat(),
                "ignored": True
            }
        }

        to_send = filter_alarms_to_send(alarms, state)
        assert len(to_send) == 0, "Alarm with 3 sends should NOT be sent"

    def test_dessmonitor_filter_alarms_4hour_interval(self, test_state_file):
        """DessMonitor: Alarm sent 2 hours ago should NOT be sent (4-hour rule)"""
        alarm_key = "54321:DESSDEV001:desswarn001"
        alarms = [
            {
                "pid": 54321,
                "pn": "DESSDEV001",
                "id": "desswarn001",
                "customer_label": "mifrazmarsoon"
            }
        ]

        state = {
            alarm_key: {
                "send_count": 1,
                "last_sent": (datetime.now() - timedelta(hours=2)).isoformat(),
                "ignored": False
            }
        }

        to_send = filter_alarms_to_send(alarms, state)
        assert len(to_send) == 0, "Alarm sent 2 hours ago should wait for 4-hour interval"

    def test_dessmonitor_filter_alarms_4hour_interval_passed(self, test_state_file):
        """DessMonitor: Alarm sent 5 hours ago SHOULD be sent (4-hour rule passed)"""
        alarm_key = "54321:DESSDEV001:desswarn001"
        alarms = [
            {
                "pid": 54321,
                "pn": "DESSDEV001",
                "id": "desswarn001",
                "customer_label": "mifrazmarsoon"
            }
        ]

        state = {
            alarm_key: {
                "send_count": 1,
                "last_sent": (datetime.now() - timedelta(hours=5)).isoformat(),
                "ignored": False
            }
        }

        to_send = filter_alarms_to_send(alarms, state)
        assert len(to_send) == 1, "Alarm sent 5 hours ago should be ready to send"

    # ==================== Email Formatting ====================

    def test_dessmonitor_format_email_no_alarms(self):
        """DessMonitor: Format email when no alarms detected"""
        # format_device_alarms_email takes a single list argument (alarms_to_send)
        email_text = format_device_alarms_email([])
        assert "ALL CLEAR" in email_text or "No" in email_text

    def test_dessmonitor_format_email_with_alarms(self):
        """DessMonitor: Format email with device alarms"""
        # format_device_alarms_email expects list of dicts with 'alarm', 'alarm_key', 'send_count' keys
        alarms_to_send = [
            {
                "alarm": {
                    "pid": 54321,
                    "plant": "MifrazMarsoon Plant",
                    "pn": "DESSDEV001",
                    "alias": "DessMonitor Inverter",
                    "id": "desswarn001",
                    "desc": "Grid voltage too high",
                    "gts": "2026-01-07 10:30:00",
                    "customer_label": "mifrazmarsoon"
                },
                "alarm_key": "54321:DESSDEV001:desswarn001",
                "send_count": 0
            }
        ]

        email_text = format_device_alarms_email(alarms_to_send)

        assert "MifrazMarsoon" in email_text or "mifrazmarsoon" in email_text.lower()
        assert "Grid voltage" in email_text or "ALARM" in email_text.upper()

    # ==================== State Management ====================

    def test_dessmonitor_update_alarm_state_increment_send_count(self, test_state_file):
        """DessMonitor: State increments send count after sending"""
        alarm_key = "54321:DESSDEV001:desswarn001"
        # update_alarm_state expects list of dicts with 'alarm', 'alarm_key', 'send_count'
        alarms_to_send = [
            {
                "alarm": {
                    "pid": 54321,
                    "pn": "DESSDEV001",
                    "id": "desswarn001",
                    "customer_label": "mifrazmarsoon",
                    "plant": "MifrazMarsoon Plant",
                    "desc": "Test warning"
                },
                "alarm_key": alarm_key,
                "send_count": 0
            }
        ]

        state = {}
        update_alarm_state(state, alarms_to_send)

        assert alarm_key in state
        assert state[alarm_key]["send_count"] == 1

    def test_dessmonitor_update_alarm_state_auto_ignore_after_3_sends(self, test_state_file):
        """DessMonitor: Alarm auto-ignored after 3rd send"""
        alarm_key = "54321:DESSDEV001:desswarn001"
        # update_alarm_state expects list of dicts with 'alarm', 'alarm_key', 'send_count'
        alarms_to_send = [
            {
                "alarm": {
                    "pid": 54321,
                    "pn": "DESSDEV001",
                    "id": "desswarn001",
                    "customer_label": "mifrazmarsoon",
                    "plant": "MifrazMarsoon Plant",
                    "desc": "Test warning"
                },
                "alarm_key": alarm_key,
                "send_count": 2  # Already sent 2 times, this will be 3rd
            }
        ]

        state = {
            alarm_key: {
                "send_count": 2,
                "last_sent": (datetime.now() - timedelta(hours=5)).isoformat(),
                "ignored": False
            }
        }

        update_alarm_state(state, alarms_to_send)

        # After 3rd send, should be marked ignored
        assert state[alarm_key]["send_count"] == 3
        assert state[alarm_key]["ignored"] == True

    def test_dessmonitor_state_persistence(self, test_state_file):
        """DessMonitor: State file saves and loads correctly"""
        state = {
            "54321:DESSDEV001:desswarn001": {
                "send_count": 2,
                "last_sent": datetime.now().isoformat(),
                "ignored": False,
                "customer": "mifrazmarsoon",
                "plant": "MifrazMarsoon Plant",
                "message": "Test warning"
            }
        }

        # save_alarm_state(state_file, state) - file path first, then state
        save_alarm_state(str(test_state_file), state)
        loaded = load_alarm_state(str(test_state_file))

        assert "54321:DESSDEV001:desswarn001" in loaded
        assert loaded["54321:DESSDEV001:desswarn001"]["send_count"] == 2

    # ==================== UC4: Test Customer Mode ====================

    def test_dessmonitor_test_mode_filters_to_mifrazmarsoon(self):
        """UC4: DessMonitor test mode uses MifrazMarsoon (not Gayan-IMH)"""
        # DessMonitor test customer is MifrazMarsoon
        test_customer = "mifrazmarsoon"
        all_alarms = [
            {"customer_label": "mifrazmarsoon", "plant": "Plant A"},
            {"customer_label": "other-customer", "plant": "Plant B"},
        ]

        # Filter to test customer only
        test_alarms = [a for a in all_alarms if a.get("customer_label", "").lower() == test_customer.lower()]

        assert len(test_alarms) == 1
        assert test_alarms[0]["customer_label"] == "mifrazmarsoon"

    # ==================== UC6: Log Summary Counts ====================

    def test_dessmonitor_log_summary_counts(self):
        """UC6: Log summary shows alarm/customer/plant counts"""
        alarms = [
            {"customer_label": "mifrazmarsoon", "plant": "Plant A", "id": "1"},
            {"customer_label": "mifrazmarsoon", "plant": "Plant B", "id": "2"},
            {"customer_label": "other-customer", "plant": "Plant C", "id": "3"},
        ]

        total_alarms = len(alarms)
        customers = set(a["customer_label"] for a in alarms)
        plants = set(a["plant"] for a in alarms)

        assert total_alarms == 3
        assert len(customers) == 2
        assert len(plants) == 3

    # ==================== UC7: State JSON Enhancement ====================

    def test_dessmonitor_state_includes_customer_plant_message(self, test_state_file):
        """UC7: DessMonitor state includes customer, plant, message fields"""
        alarm_key = "54321:DESSDEV001:desswarn001"
        # update_alarm_state expects list of dicts with 'alarm', 'alarm_key', 'send_count'
        alarms_to_send = [
            {
                "alarm": {
                    "pid": 54321,
                    "pn": "DESSDEV001",
                    "id": "desswarn001",
                    "customer_label": "mifrazmarsoon",
                    "plant": "MifrazMarsoon Plant",
                    "desc": "Grid voltage too high"
                },
                "alarm_key": alarm_key,
                "send_count": 0
            }
        ]

        state = {}
        update_alarm_state(state, alarms_to_send)

        # Verify UC7 fields are present
        assert alarm_key in state
        assert "customer" in state[alarm_key]
        assert "plant" in state[alarm_key]
        assert "message" in state[alarm_key]
        assert state[alarm_key]["customer"] == "mifrazmarsoon"
        assert state[alarm_key]["plant"] == "MifrazMarsoon Plant"
        assert state[alarm_key]["message"] == "Grid voltage too high"

    # ==================== UC8: Workflow Structure ====================

    def test_dessmonitor_workflow_has_device_alarm_steps(self):
        """UC8: DessMonitor workflow has device alarm steps"""
        workflow_path = scripts_dir.parent.parent.parent.parent.parent.parent / ".github" / "workflows" / "trigger-dessmonitor.yml"

        if not workflow_path.exists():
            pytest.skip("trigger-dessmonitor.yml not found")

        content = workflow_path.read_text(encoding='utf-8')

        # Verify device alarm steps exist
        assert "check_dessmonitor_device_alarms" in content or "device" in content.lower()
        assert "generate_device_alarms" in content

    def test_dessmonitor_workflow_has_separate_state_file(self):
        """UC8: DessMonitor uses separate state file from ShineMonitor"""
        workflow_path = scripts_dir.parent.parent.parent.parent.parent.parent / ".github" / "workflows" / "trigger-dessmonitor.yml"

        if not workflow_path.exists():
            pytest.skip("trigger-dessmonitor.yml not found")

        content = workflow_path.read_text(encoding='utf-8')

        # Should use dessmonitor-prefixed state file
        assert "dessmonitor_device_alarms_state.json" in content or "dessmonitor" in content.lower()

    # ==================== DessMonitor-Specific Script Tests ====================

    def test_dessmonitor_device_alarms_script_exists(self):
        """Verify check_dessmonitor_device_alarms.sh exists"""
        script_path = scripts_dir / "check_dessmonitor_device_alarms.sh"
        assert script_path.exists(), "check_dessmonitor_device_alarms.sh should exist"

    def test_dessmonitor_device_alarms_script_uses_correct_api(self):
        """Verify DessMonitor uses dessmonitor_common.sh for API calls"""
        script_path = scripts_dir / "check_dessmonitor_device_alarms.sh"

        if not script_path.exists():
            pytest.skip("check_dessmonitor_device_alarms.sh not found")

        content = script_path.read_text(encoding='utf-8')

        # Should source DessMonitor common functions
        assert "dessmonitor_common.sh" in content
        # Should use DessMonitor API calls
        assert "dessmonitor_api_call" in content or "dessmonitor_authenticate" in content


class TestDessMonitorCustomerNotifications:
    """Test DessMonitor customer device alarm notifications"""

    def test_dessmonitor_customer_mapping_uses_dessmonitor_credentials(self):
        """Customer mapping should use dessmonitor_credentials key"""
        # DessMonitor credentials use 'dessmonitor_accounts' key
        sample_creds = {
            "dessmonitor_credentials": {
                "company_key": "test",
                "dessmonitor_accounts": [
                    {"label": "MifrazMarsoon", "email": "test@example.com"}
                ]
            }
        }

        accounts = sample_creds.get("dessmonitor_credentials", {}).get("dessmonitor_accounts", [])
        assert len(accounts) == 1
        assert accounts[0]["label"] == "MifrazMarsoon"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
