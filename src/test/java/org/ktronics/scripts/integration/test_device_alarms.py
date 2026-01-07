#!/usr/bin/env python3
"""
Integration tests for device alarm monitoring system
Tests UC3: Verify device alarms are fetched, processed, and sent correctly
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
SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent.parent.parent / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from generate_device_alarms import (
    parse_alarm_files,
    create_alarm_key,
    filter_alarms_to_send,
    format_device_alarms_email,
    update_alarm_state,
    load_alarm_state,
    save_alarm_state
)


class TestDeviceAlarmSystem:
    """Test device alarm monitoring and notification system"""

    @pytest.fixture
    def test_alarms_dir(self):
        """Create temporary alarms directory"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def test_state_file(self, tmp_path):
        """Create temporary state file path"""
        return tmp_path / "device_alarms_state.json"

    def create_alarm_json(self, alarms_dir, customer_label, alarms_data):
        """Helper: Create alarm JSON file for a customer"""
        alarm_file = alarms_dir / f"{customer_label}-alarms.json"

        data = {
            "err": 0,
            "dat": alarms_data
        }

        with open(alarm_file, 'w') as f:
            json.dump(data, f)

        return alarm_file

    def test_parse_alarm_files_single_customer(self, test_alarms_dir):
        """Parse alarm files from single customer"""
        alarms_data = [
            {
                "pId": "12345",
                "pName": "Test Plant 1",
                "devId": "DEV001",
                "devName": "Inverter 1",
                "warnId": "W001",
                "warnMsg": "Grid voltage too high",
                "warnTime": "2026-01-07 10:30:00",
                "status": 0  # UNHANDLED
            },
            {
                "pId": "12345",
                "pName": "Test Plant 1",
                "devId": "DEV002",
                "devName": "Inverter 2",
                "warnId": "W002",
                "warnMsg": "Temperature warning",
                "warnTime": "2026-01-07 11:00:00",
                "status": 0  # UNHANDLED
            }
        ]

        self.create_alarm_json(test_alarms_dir, "customer1", alarms_data)

        # Parse alarms
        all_alarms = parse_alarm_files(test_alarms_dir)

        assert len(all_alarms) == 2
        assert all_alarms[0]['customer_label'] == 'customer1'
        assert all_alarms[0]['warnMsg'] == 'Grid voltage too high'
        assert all_alarms[1]['warnMsg'] == 'Temperature warning'

    def test_parse_alarm_files_multiple_customers(self, test_alarms_dir):
        """Parse alarm files from multiple customers"""
        # Customer 1 alarms
        customer1_alarms = [
            {
                "pId": "12345",
                "pName": "Plant A",
                "devId": "DEV001",
                "devName": "Inverter A1",
                "warnId": "W001",
                "warnMsg": "Grid voltage too high",
                "warnTime": "2026-01-07 10:30:00",
                "status": 0
            }
        ]

        # Customer 2 alarms
        customer2_alarms = [
            {
                "pId": "67890",
                "pName": "Plant B",
                "devId": "DEV002",
                "devName": "Inverter B1",
                "warnId": "W002",
                "warnMsg": "Low production",
                "warnTime": "2026-01-07 11:00:00",
                "status": 0
            }
        ]

        self.create_alarm_json(test_alarms_dir, "customer1", customer1_alarms)
        self.create_alarm_json(test_alarms_dir, "customer2", customer2_alarms)

        # Parse alarms
        all_alarms = parse_alarm_files(test_alarms_dir)

        assert len(all_alarms) == 2

        customer_labels = [a['customer_label'] for a in all_alarms]
        assert 'customer1' in customer_labels
        assert 'customer2' in customer_labels

    def test_create_alarm_key_unique(self):
        """Verify alarm keys are unique per plant/device/warning"""
        alarm1 = {
            "pId": "12345",
            "devId": "DEV001",
            "warnId": "W001"
        }

        alarm2 = {
            "pId": "12345",
            "devId": "DEV002",  # Different device
            "warnId": "W001"
        }

        alarm3 = {
            "pId": "67890",  # Different plant
            "devId": "DEV001",
            "warnId": "W001"
        }

        key1 = create_alarm_key(alarm1)
        key2 = create_alarm_key(alarm2)
        key3 = create_alarm_key(alarm3)

        # All keys should be different
        assert key1 != key2
        assert key1 != key3
        assert key2 != key3

        # Same alarm should generate same key
        assert key1 == create_alarm_key(alarm1)

    def test_filter_alarms_first_send(self, test_alarms_dir, test_state_file):
        """NEW FEATURE TEST: First time seeing alarm - should send"""
        alarms = [
            {
                "pId": "12345",
                "devId": "DEV001",
                "warnId": "W001",
                "pName": "Test Plant",
                "devName": "Inverter 1",
                "warnMsg": "Grid voltage too high",
                "warnTime": "2026-01-07 10:30:00"
            }
        ]

        state = {}
        alarms_to_send = filter_alarms_to_send(alarms, state, max_sends=3)

        assert len(alarms_to_send) == 1
        assert alarms_to_send[0]['send_count'] == 0  # First send

    def test_filter_alarms_max_sends_reached(self, test_alarms_dir, test_state_file):
        """NEW FEATURE TEST: Alarm already sent 3 times - should NOT send"""
        alarm = {
            "pId": "12345",
            "devId": "DEV001",
            "warnId": "W001",
            "pName": "Test Plant",
            "devName": "Inverter 1",
            "warnMsg": "Grid voltage too high",
            "warnTime": "2026-01-07 10:30:00"
        }

        alarm_key = create_alarm_key(alarm)

        # State shows already sent 3 times
        state = {
            alarm_key: {
                'send_count': 3,
                'last_sent': (datetime.now() - timedelta(hours=5)).isoformat(),
                'first_seen': (datetime.now() - timedelta(days=1)).isoformat(),
                'ignored': False
            }
        }

        alarms_to_send = filter_alarms_to_send([alarm], state, max_sends=3)

        assert len(alarms_to_send) == 0  # Should NOT send
        assert state[alarm_key]['ignored'] == True  # Should be auto-ignored

    def test_filter_alarms_4hour_interval(self, test_alarms_dir, test_state_file):
        """NEW FEATURE TEST: Alarm sent 2 hours ago - should NOT send (4-hour interval)"""
        alarm = {
            "pId": "12345",
            "devId": "DEV001",
            "warnId": "W001",
            "pName": "Test Plant",
            "devName": "Inverter 1",
            "warnMsg": "Grid voltage too high",
            "warnTime": "2026-01-07 10:30:00"
        }

        alarm_key = create_alarm_key(alarm)

        # State shows sent 2 hours ago (too soon)
        state = {
            alarm_key: {
                'send_count': 1,
                'last_sent': (datetime.now() - timedelta(hours=2)).isoformat(),
                'first_seen': (datetime.now() - timedelta(hours=2)).isoformat(),
                'ignored': False
            }
        }

        alarms_to_send = filter_alarms_to_send([alarm], state, max_sends=3)

        assert len(alarms_to_send) == 0  # Should NOT send (too soon)

    def test_filter_alarms_4hour_interval_passed(self, test_alarms_dir, test_state_file):
        """NEW FEATURE TEST: Alarm sent 5 hours ago - should send (4-hour interval passed)"""
        alarm = {
            "pId": "12345",
            "devId": "DEV001",
            "warnId": "W001",
            "pName": "Test Plant",
            "devName": "Inverter 1",
            "warnMsg": "Grid voltage too high",
            "warnTime": "2026-01-07 10:30:00"
        }

        alarm_key = create_alarm_key(alarm)

        # State shows sent 5 hours ago (enough time passed)
        state = {
            alarm_key: {
                'send_count': 1,
                'last_sent': (datetime.now() - timedelta(hours=5)).isoformat(),
                'first_seen': (datetime.now() - timedelta(hours=5)).isoformat(),
                'ignored': False
            }
        }

        alarms_to_send = filter_alarms_to_send([alarm], state, max_sends=3)

        assert len(alarms_to_send) == 1  # Should send (2nd time)
        assert alarms_to_send[0]['send_count'] == 1  # Previous send count

    def test_format_email_no_alarms(self):
        """Format email when no alarms (all clear)"""
        email_body = format_device_alarms_email([])

        assert "DEVICE ALARMS - ALL CLEAR" in email_body
        assert "ALL SYSTEMS OPERATIONAL" in email_body
        assert "No UNHANDLED device alarms detected" in email_body

    def test_format_email_with_alarms(self):
        """Format email with device alarms"""
        alarms_to_send = [
            {
                'alarm': {
                    'pName': 'Test Plant A',
                    'devName': 'Inverter 1',
                    'warnMsg': 'Grid voltage too high',
                    'warnTime': '2026-01-07 10:30:00',
                    'customer_label': 'customer1'
                },
                'alarm_key': '12345:DEV001:W001',
                'send_count': 0  # First send
            },
            {
                'alarm': {
                    'pName': 'Test Plant B',
                    'devName': 'Inverter 2',
                    'warnMsg': 'Temperature warning',
                    'warnTime': '2026-01-07 11:00:00',
                    'customer_label': 'customer2'
                },
                'alarm_key': '67890:DEV002:W002',
                'send_count': 1  # Second send
            }
        ]

        email_body = format_device_alarms_email(alarms_to_send)

        # Verify email contains alarm details
        assert "DEVICE ALARMS DETECTED" in email_body
        assert "Total UNHANDLED Alarms: 2" in email_body
        assert "Affected Customers:     2" in email_body

        # Verify customer breakdown
        assert "customer1" in email_body
        assert "customer2" in email_body

        # Verify alarm details
        assert "Test Plant A" in email_body
        assert "Inverter 1" in email_body
        assert "Grid voltage too high" in email_body
        assert "Send #1/3" in email_body  # First send

        assert "Test Plant B" in email_body
        assert "Inverter 2" in email_body
        assert "Temperature warning" in email_body
        assert "Send #2/3" in email_body  # Second send

    def test_update_alarm_state_increment_send_count(self):
        """Update state after sending alarms - increment send count"""
        alarm_key = "12345:DEV001:W001"

        alarms_to_send = [
            {
                'alarm': {
                    'pName': 'Test Plant',
                    'devName': 'Inverter 1',
                    'warnMsg': 'Test alarm',
                    'warnTime': '2026-01-07 10:30:00'
                },
                'alarm_key': alarm_key,
                'send_count': 1  # Previous count
            }
        ]

        state = {
            alarm_key: {
                'send_count': 1,
                'last_sent': (datetime.now() - timedelta(hours=5)).isoformat(),
                'first_seen': (datetime.now() - timedelta(hours=5)).isoformat(),
                'ignored': False
            }
        }

        updated_state = update_alarm_state(state, alarms_to_send)

        assert updated_state[alarm_key]['send_count'] == 2  # Incremented
        assert updated_state[alarm_key]['ignored'] == False  # Not yet ignored (< 3 sends)

    def test_update_alarm_state_auto_ignore_after_3_sends(self):
        """Update state after 3rd send - should auto-ignore"""
        alarm_key = "12345:DEV001:W001"

        alarms_to_send = [
            {
                'alarm': {
                    'pName': 'Test Plant',
                    'devName': 'Inverter 1',
                    'warnMsg': 'Test alarm',
                    'warnTime': '2026-01-07 10:30:00'
                },
                'alarm_key': alarm_key,
                'send_count': 2  # About to be 3rd send
            }
        ]

        state = {
            alarm_key: {
                'send_count': 2,
                'last_sent': (datetime.now() - timedelta(hours=5)).isoformat(),
                'first_seen': (datetime.now() - timedelta(days=1)).isoformat(),
                'ignored': False
            }
        }

        updated_state = update_alarm_state(state, alarms_to_send)

        assert updated_state[alarm_key]['send_count'] == 3  # 3rd send
        assert updated_state[alarm_key]['ignored'] == True  # Auto-ignored after 3 sends

    def test_state_persistence(self, test_state_file):
        """Test alarm state save and load"""
        alarm_key = "12345:DEV001:W001"

        state = {
            alarm_key: {
                'send_count': 2,
                'last_sent': '2026-01-07T10:30:00',
                'first_seen': '2026-01-06T10:30:00',
                'ignored': False
            }
        }

        # Save state
        save_alarm_state(test_state_file, state)

        # Load state
        loaded_state = load_alarm_state(test_state_file)

        assert loaded_state == state
        assert loaded_state[alarm_key]['send_count'] == 2
        assert loaded_state[alarm_key]['ignored'] == False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
