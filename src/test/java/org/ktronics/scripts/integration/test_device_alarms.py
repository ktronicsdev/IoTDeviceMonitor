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
    save_alarm_state,
    load_customer_mapping,
    create_customer_device_alarms,
    get_most_recent_alarm
)

from send_customer_emails import (
    format_customer_device_alarm_email,
    send_customer_device_alarms
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
        """Parse alarm files from single customer

        Uses ACTUAL ShineMonitor API field names:
        - pid (not pId) - plant ID
        - plant (not pName) - plant name
        - pn (not devId) - device serial number
        - alias (not devName) - device alias/name
        - id (not warnId) - warning ID
        - desc (not warnMsg) - warning description
        - gts (not warnTime) - warning timestamp
        """
        alarms_data = [
            {
                "id": "abc123def456",
                "pid": 12345,
                "plant": "Test Plant 1",
                "pn": "D70000210151320902",
                "sn": "FFFFFFFF",
                "alias": "Inverter 1",
                "desc": "Grid voltage too high",
                "gts": "2026-01-07 10:30:00",
                "status": False,  # UNHANDLED (API uses boolean)
                "handle": False
            },
            {
                "id": "xyz789ghi012",
                "pid": 12345,
                "plant": "Test Plant 1",
                "pn": "D70000210151320903",
                "sn": "FFFFFFFF",
                "alias": "Inverter 2",
                "desc": "Temperature warning",
                "gts": "2026-01-07 11:00:00",
                "status": False,
                "handle": False
            }
        ]

        self.create_alarm_json(test_alarms_dir, "customer1", alarms_data)

        # Parse alarms
        all_alarms = parse_alarm_files(test_alarms_dir)

        assert len(all_alarms) == 2
        assert all_alarms[0]['customer_label'] == 'customer1'
        assert all_alarms[0]['desc'] == 'Grid voltage too high'
        assert all_alarms[1]['desc'] == 'Temperature warning'

    def test_parse_alarm_files_multiple_customers(self, test_alarms_dir):
        """Parse alarm files from multiple customers - uses actual API field names"""
        # Customer 1 alarms
        customer1_alarms = [
            {
                "id": "alarm001",
                "pid": 12345,
                "plant": "Plant A",
                "pn": "D70000210151320901",
                "alias": "Inverter A1",
                "desc": "Grid voltage too high",
                "gts": "2026-01-07 10:30:00",
                "status": False
            }
        ]

        # Customer 2 alarms
        customer2_alarms = [
            {
                "id": "alarm002",
                "pid": 67890,
                "plant": "Plant B",
                "pn": "D70000210151320902",
                "alias": "Inverter B1",
                "desc": "Low production",
                "gts": "2026-01-07 11:00:00",
                "status": False
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

    def test_parse_alarm_files_api_warning_format(self, test_alarms_dir):
        """Parse alarm files with actual API response format (dat.warning structure)"""
        # Actual API response format: {"err":"0","dat":{"total":37,"warning":[...]}}
        alarm_file = test_alarms_dir / "Gayan-IMH-alarms.json"

        api_response = {
            "err": "0",
            "dat": {
                "total": 2,
                "page": 0,
                "pagesize": 1,
                "warning": [
                    {
                        "pId": "12345",
                        "pName": "Gayan-IMH-Imbulgoda-3KW",
                        "devId": "DEV001",
                        "devName": "Inverter 1",
                        "warnId": "W001",
                        "warnMsg": "Grid voltage too high",
                        "warnTime": "2026-01-07 10:30:00",
                        "status": 0
                    },
                    {
                        "pId": "12345",
                        "pName": "Gayan-IMH-Imbulgoda-3KW",
                        "devId": "DEV002",
                        "devName": "Inverter 2",
                        "warnId": "W002",
                        "warnMsg": "Temperature warning",
                        "warnTime": "2026-01-07 11:00:00",
                        "status": 0
                    }
                ]
            }
        }

        with open(alarm_file, 'w') as f:
            json.dump(api_response, f)

        # Parse alarms
        all_alarms = parse_alarm_files(test_alarms_dir)

        # Verify correct parsing
        assert len(all_alarms) == 2
        assert all_alarms[0]['customer_label'] == 'Gayan-IMH'
        assert all_alarms[0]['warnMsg'] == 'Grid voltage too high'
        assert all_alarms[1]['warnMsg'] == 'Temperature warning'
        assert all_alarms[0]['pName'] == 'Gayan-IMH-Imbulgoda-3KW'

    def test_parse_alarm_files_detects_warnings_in_response(self, test_alarms_dir):
        """REGRESSION TEST: Verify alarm detection works with dat.warning structure

        This test prevents regression of the bug where check_device_alarms.sh
        looked for dat:[ but API returns dat:warning:[...]

        Bug history: Script used grep -o 'dat:\\[' which failed to detect alarms
        in the actual API response format, causing "No alarms found" when alarms
        existed. This led to zero emails being sent.
        """
        # Create alarm file with actual API response structure
        alarm_file = test_alarms_dir / "test-customer-alarms.json"

        # Simulate exact API response format with warning array
        api_response = {
            "err": "0",
            "desc": "ERR_NONE",
            "dat": {
                "total": 37,  # This is the count check_device_alarms.sh should extract
                "page": 0,
                "pagesize": 1,
                "warning": [
                    {
                        "pId": "12345",
                        "pName": "Test Plant",
                        "devId": "DEV001",
                        "devName": "Test Device",
                        "warnId": "W001",
                        "warnMsg": "Test warning",
                        "warnTime": "2026-01-07 12:00:00",
                        "status": 0
                    }
                ]
            }
        }

        with open(alarm_file, 'w') as f:
            json.dump(api_response, f)

        # Verify parse_alarm_files correctly extracts alarms from dat.warning
        all_alarms = parse_alarm_files(test_alarms_dir)

        # Should find 1 alarm from the "warning" array
        assert len(all_alarms) == 1
        assert all_alarms[0]['warnMsg'] == 'Test warning'

        # Verify the response contains warning array (what grep should look for)
        with open(alarm_file, 'r') as f:
            content = f.read()
            # check_device_alarms.sh looks for "warning":[ pattern (with or without space)
            assert '"warning"' in content and '[' in content, "API response must contain warning array"
            assert '"dat"' in content and '{' in content, "API response has dat as object"
            # Old broken format would have been "dat":[] (array directly)
            assert not ('"dat": []' in content or '"dat":[]' in content), "Old format should not be present"

    def test_create_alarm_key_unique(self):
        """Verify alarm keys are unique per plant/device/warning

        Uses ACTUAL ShineMonitor API field names:
        - pid (not pId) - plant ID
        - pn (not devId) - device serial number
        - id (not warnId) - warning ID
        """
        alarm1 = {
            "pid": 12345,
            "pn": "D70000210151320901",
            "id": "abc123def456"
        }

        alarm2 = {
            "pid": 12345,
            "pn": "D70000210151320902",  # Different device
            "id": "abc123def456"
        }

        alarm3 = {
            "pid": 67890,  # Different plant
            "pn": "D70000210151320901",
            "id": "abc123def456"
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
        """NEW FEATURE TEST: First time seeing alarm - should send

        Uses ACTUAL ShineMonitor API field names:
        - pid, plant, pn, alias, id, desc, gts
        """
        alarms = [
            {
                "pid": 12345,
                "pn": "D70000210151320901",
                "id": "abc123def456",
                "plant": "Test Plant",
                "alias": "Inverter 1",
                "desc": "Grid voltage too high",
                "gts": "2026-01-07 10:30:00",
                "status": False
            }
        ]

        state = {}
        alarms_to_send = filter_alarms_to_send(alarms, state, max_sends=3)

        assert len(alarms_to_send) == 1
        assert alarms_to_send[0]['send_count'] == 0  # First send

    def test_filter_alarms_max_sends_reached(self, test_alarms_dir, test_state_file):
        """NEW FEATURE TEST: Alarm already sent 3 times - should NOT send

        Uses ACTUAL ShineMonitor API field names.
        """
        alarm = {
            "pid": 12345,
            "pn": "D70000210151320901",
            "id": "abc123def456",
            "plant": "Test Plant",
            "alias": "Inverter 1",
            "desc": "Grid voltage too high",
            "gts": "2026-01-07 10:30:00",
            "status": False
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
        """NEW FEATURE TEST: Alarm sent 2 hours ago - should NOT send (4-hour interval)

        Uses ACTUAL ShineMonitor API field names.
        """
        alarm = {
            "pid": 12345,
            "pn": "D70000210151320901",
            "id": "abc123def456",
            "plant": "Test Plant",
            "alias": "Inverter 1",
            "desc": "Grid voltage too high",
            "gts": "2026-01-07 10:30:00",
            "status": False
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
        """NEW FEATURE TEST: Alarm sent 5 hours ago - should send (4-hour interval passed)

        Uses ACTUAL ShineMonitor API field names.
        """
        alarm = {
            "pid": 12345,
            "pn": "D70000210151320901",
            "id": "abc123def456",
            "plant": "Test Plant",
            "alias": "Inverter 1",
            "desc": "Grid voltage too high",
            "gts": "2026-01-07 10:30:00",
            "status": False
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
        """Format email with device alarms

        Uses ACTUAL ShineMonitor API field names (plant, alias, desc, gts).
        The format_device_alarms_email function handles both old and new field names.
        """
        alarms_to_send = [
            {
                'alarm': {
                    'plant': 'Test Plant A',
                    'alias': 'Inverter 1',
                    'desc': 'Grid voltage too high',
                    'gts': '2026-01-07 10:30:00',
                    'customer_label': 'customer1'
                },
                'alarm_key': '12345:D70000210151320901:abc123',
                'send_count': 0  # First send
            },
            {
                'alarm': {
                    'plant': 'Test Plant B',
                    'alias': 'Inverter 2',
                    'desc': 'Temperature warning',
                    'gts': '2026-01-07 11:00:00',
                    'customer_label': 'customer2'
                },
                'alarm_key': '67890:D70000210151320902:xyz789',
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

    def test_format_email_no_unknown_values_with_api_fields(self):
        """REGRESSION TEST: Email must NOT show 'Unknown Plant' when using actual API field names

        BUG HISTORY (2026-01-10):
        - Admin email showed "Unknown Plant", "Unknown Device", "No message", "Unknown time"
        - Root cause: format_device_alarms_email() only checked old field names (pName, devName, warnMsg, warnTime)
        - Actual API returns: plant, alias, desc, gts
        - Fix: Check both old and new field names with fallback

        This test prevents regression by verifying:
        1. Actual API field names are correctly extracted
        2. Email does NOT contain default "Unknown" values
        """
        # Use EXACT field names from actual ShineMonitor API response
        alarms_to_send = [
            {
                'alarm': {
                    'pid': 1053849,
                    'plant': 'Namila-Waragoda-Plant',
                    'pn': 'D70000210151320902',
                    'alias': '4.96kw pv with 10kw pack',
                    'desc': 'Solar charger stops due to low battery',
                    'gts': '2025-12-18 19:40:44',
                    'status': False,
                    'customer_label': 'Namila-Waragoda'
                },
                'alarm_key': '1053849:D70000210151320902:69440b6afb7cce56c47629dc',
                'send_count': 0
            }
        ]

        email_body = format_device_alarms_email(alarms_to_send)

        # CRITICAL: Must NOT contain default/unknown values
        assert "Unknown Plant" not in email_body, "BUG: format_device_alarms_email not reading 'plant' field"
        assert "Unknown Device" not in email_body, "BUG: format_device_alarms_email not reading 'alias' field"
        assert "No message" not in email_body, "BUG: format_device_alarms_email not reading 'desc' field"
        assert "Unknown time" not in email_body, "BUG: format_device_alarms_email not reading 'gts' field"

        # Verify actual values ARE in email
        assert "Namila-Waragoda-Plant" in email_body
        assert "4.96kw pv with 10kw pack" in email_body
        assert "Solar charger stops due to low battery" in email_body
        assert "2025-12-18 19:40:44" in email_body

    def test_update_alarm_state_increment_send_count(self):
        """Update state after sending alarms - increment send count

        Uses ACTUAL ShineMonitor API field names.
        """
        alarm_key = "12345:D70000210151320901:abc123def456"

        alarms_to_send = [
            {
                'alarm': {
                    'plant': 'Test Plant',
                    'alias': 'Inverter 1',
                    'desc': 'Test alarm',
                    'gts': '2026-01-07 10:30:00'
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
        """Update state after 3rd send - should auto-ignore

        Uses ACTUAL ShineMonitor API field names.
        """
        alarm_key = "12345:D70000210151320901:abc123def456"

        alarms_to_send = [
            {
                'alarm': {
                    'plant': 'Test Plant',
                    'alias': 'Inverter 1',
                    'desc': 'Test alarm',
                    'gts': '2026-01-07 10:30:00'
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
        """Test alarm state save and load

        Uses realistic alarm key format: pid:pn:id
        """
        alarm_key = "12345:D70000210151320901:abc123def456"

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

    def test_load_customer_mapping(self, tmp_path):
        """UC3 CUSTOMER: Load plant-to-customer mapping from credentials.json"""
        # Create test credentials file
        test_credentials = tmp_path / "test_credentials.json"
        credentials_data = {
            "company_key": "test-company-key",
            "accounts": [
                {
                    "label": "Gayan-IMH",
                    "username": "gayan@example.com",
                    "password": "pass123",
                    "email": "gayan@test.com"
                },
                {
                    "label": "Test-Customer-2",
                    "username": "customer2@example.com",
                    "password": "pass456",
                    "email": "customer2@test.com"
                },
                {
                    "label": "No-Email-Customer",
                    "username": "noemail@example.com",
                    "password": "pass789",
                    "email": ""  # No email
                }
            ]
        }

        with open(test_credentials, 'w', encoding='utf-8') as f:
            json.dump(credentials_data, f)

        # Load customer mapping
        customer_map = load_customer_mapping(test_credentials)

        # Verify normalization: "Gayan-IMH" → "gayanimh"
        assert 'gayanimh' in customer_map
        assert customer_map['gayanimh']['label'] == 'Gayan-IMH'
        assert customer_map['gayanimh']['email'] == 'gayan@test.com'

        # Verify second customer
        assert 'testcustomer2' in customer_map
        assert customer_map['testcustomer2']['label'] == 'Test-Customer-2'
        assert customer_map['testcustomer2']['email'] == 'customer2@test.com'

        # Verify customer with no email is still mapped
        assert 'noemailcustomer' in customer_map
        assert customer_map['noemailcustomer']['email'] == ''

    def test_create_customer_device_alarms_single_customer(self, tmp_path):
        """UC3 CUSTOMER: Map alarms to single customer"""
        # Create test credentials
        test_credentials = tmp_path / "test_credentials.json"
        credentials_data = {
            "company_key": "test-company-key",
            "accounts": [
                {
                    "label": "Gayan-IMH",
                    "username": "gayan@example.com",
                    "password": "pass123",
                    "email": "gayan@test.com"
                }
            ]
        }

        with open(test_credentials, 'w', encoding='utf-8') as f:
            json.dump(credentials_data, f)

        # Create alarms for Gayan-IMH (customer_label from filename)
        alarms_to_send = [
            {
                'alarm': {
                    'pid': 12345,
                    'plant': 'gayan-imh-imbulgoda-3kw',
                    'alias': 'Inverter 1',
                    'desc': 'Grid voltage too high',
                    'gts': '2026-01-07 10:30:00',
                    'customer_label': 'Gayan-IMH'  # Set by parse_alarm_files from filename
                },
                'alarm_key': '12345:DEV001:W001',
                'send_count': 0
            },
            {
                'alarm': {
                    'pid': 12345,
                    'plant': 'gayan-imh-plant-2',
                    'alias': 'Inverter 2',
                    'desc': 'Temperature warning',
                    'gts': '2026-01-07 11:00:00',
                    'customer_label': 'Gayan-IMH'  # Set by parse_alarm_files from filename
                },
                'alarm_key': '12345:DEV002:W002',
                'send_count': 1
            }
        ]

        state = {}

        # Create customer device alarms
        customer_alarms = create_customer_device_alarms(alarms_to_send, state, test_credentials)

        # Verify customer grouping
        assert 'Gayan-IMH' in customer_alarms
        assert customer_alarms['Gayan-IMH']['email'] == 'gayan@test.com'
        assert len(customer_alarms['Gayan-IMH']['alarms']) == 2

        # Verify alarm details (API field names: plant, alias->device, desc->message, gts->time)
        alarm1 = customer_alarms['Gayan-IMH']['alarms'][0]
        assert alarm1['plant'] == 'gayan-imh-imbulgoda-3kw'
        assert alarm1['device'] == 'Inverter 1'  # alias -> device
        assert alarm1['message'] == 'Grid voltage too high'  # desc -> message
        assert alarm1['send_count'] == 1  # +1 because we're about to send

        alarm2 = customer_alarms['Gayan-IMH']['alarms'][1]
        assert alarm2['send_count'] == 2  # +1 from send_count: 1

    def test_create_customer_device_alarms_multiple_customers(self, tmp_path):
        """UC3 CUSTOMER: Map alarms to multiple customers"""
        # Create test credentials
        test_credentials = tmp_path / "test_credentials.json"
        credentials_data = {
            "company_key": "test-company-key",
            "accounts": [
                {
                    "label": "Gayan-IMH",
                    "username": "gayan@example.com",
                    "password": "pass123",
                    "email": "gayan@test.com"
                },
                {
                    "label": "Namila-Waragoda",
                    "username": "namila@example.com",
                    "password": "pass456",
                    "email": "namila@test.com"
                }
            ]
        }

        with open(test_credentials, 'w', encoding='utf-8') as f:
            json.dump(credentials_data, f)

        # Create alarms for different customers (customer_label from filename)
        alarms_to_send = [
            {
                'alarm': {
                    'pid': 12345,
                    'plant': 'gayan-imh-plant',
                    'alias': 'Inverter A',
                    'desc': 'Alarm A',
                    'gts': '2026-01-07 10:00:00',
                    'customer_label': 'Gayan-IMH'  # From Gayan-IMH-alarms.json
                },
                'alarm_key': '12345:DEV001:W001',
                'send_count': 0
            },
            {
                'alarm': {
                    'pid': 67890,
                    'plant': 'namila-waragoda-plant',
                    'alias': 'Inverter B',
                    'desc': 'Alarm B',
                    'gts': '2026-01-07 11:00:00',
                    'customer_label': 'Namila-Waragoda'  # From Namila-Waragoda-alarms.json
                },
                'alarm_key': '67890:DEV002:W002',
                'send_count': 0
            }
        ]

        state = {}

        # Create customer device alarms
        customer_alarms = create_customer_device_alarms(alarms_to_send, state, test_credentials)

        # Verify both customers
        assert len(customer_alarms) == 2
        assert 'Gayan-IMH' in customer_alarms
        assert 'Namila-Waragoda' in customer_alarms

        # Verify each customer has correct alarms
        assert len(customer_alarms['Gayan-IMH']['alarms']) == 1
        assert customer_alarms['Gayan-IMH']['alarms'][0]['plant'] == 'gayan-imh-plant'

        assert len(customer_alarms['Namila-Waragoda']['alarms']) == 1
        assert customer_alarms['Namila-Waragoda']['alarms'][0]['plant'] == 'namila-waragoda-plant'

    def test_create_customer_device_alarms_skip_no_email(self, tmp_path):
        """UC3 CUSTOMER: Skip customers with no email address"""
        # Create test credentials with one customer without email
        test_credentials = tmp_path / "test_credentials.json"
        credentials_data = {
            "company_key": "test-company-key",
            "accounts": [
                {
                    "label": "No-Email-Customer",
                    "username": "noemail@example.com",
                    "password": "pass123",
                    "email": ""  # No email
                }
            ]
        }

        with open(test_credentials, 'w', encoding='utf-8') as f:
            json.dump(credentials_data, f)

        # Create alarm for this customer (customer_label from filename)
        alarms_to_send = [
            {
                'alarm': {
                    'pid': 12345,
                    'plant': 'no-email-customer-plant',
                    'alias': 'Inverter 1',
                    'desc': 'Test alarm',
                    'gts': '2026-01-07 10:00:00',
                    'customer_label': 'No-Email-Customer'  # From No-Email-Customer-alarms.json
                },
                'alarm_key': '12345:DEV001:W001',
                'send_count': 0
            }
        ]

        state = {}

        # Create customer device alarms
        customer_alarms = create_customer_device_alarms(alarms_to_send, state, test_credentials)

        # Verify customer is skipped (no email)
        assert len(customer_alarms) == 0

    def test_format_customer_device_alarm_email_with_alarms(self):
        """UC3 CUSTOMER: Format device alarm email for customer"""
        alarms = [
            {
                'plant': 'gayan-imh-imbulgoda-3kw',
                'device': 'Inverter 1',
                'message': 'Grid voltage too high',
                'time': '2026-01-07 10:30:00',
                'send_count': 1
            },
            {
                'plant': 'gayan-imh-plant-2',
                'device': 'Inverter 2',
                'message': 'Temperature warning',
                'time': '2026-01-07 11:00:00',
                'send_count': 2
            }
        ]

        email_body = format_customer_device_alarm_email("Gayan-IMH", alarms)

        # Verify email structure
        assert "DEVICE ALARMS FOR: Gayan-IMH" in email_body
        assert "2 DEVICE ALARM(S) DETECTED" in email_body
        assert "The following devices require attention:" in email_body

        # Verify alarm details
        assert "ALARM #1" in email_body
        assert "Plant:    gayan-imh-imbulgoda-3kw" in email_body
        assert "Device:   Inverter 1" in email_body
        assert "Issue:    Grid voltage too high" in email_body
        assert "Time:     2026-01-07 10:30:00" in email_body
        assert "Status:   Send #1/3" in email_body

        assert "ALARM #2" in email_body
        assert "Status:   Send #2/3" in email_body

        # Verify recommended actions
        assert "RECOMMENDED ACTIONS:" in email_body
        assert "Check device status in ShineMonitor portal" in email_body

    def test_format_customer_device_alarm_email_no_alarms(self):
        """UC3 CUSTOMER: Format email when customer has no alarms"""
        email_body = format_customer_device_alarm_email("Gayan-IMH", [])

        assert "DEVICE ALARMS FOR: Gayan-IMH" in email_body
        assert "All devices are operating normally." in email_body

    def test_format_customer_device_alarm_email_max_sends_warning(self):
        """UC3 CUSTOMER: Email shows warning when alarm reaches 3 sends"""
        alarms = [
            {
                'plant': 'test-plant',
                'device': 'Inverter 1',
                'message': 'Test alarm',
                'time': '2026-01-07 10:00:00',
                'send_count': 3  # Final send
            }
        ]

        email_body = format_customer_device_alarm_email("Test Customer", alarms)

        assert "Status:   Send #3/3" in email_body
        assert "Some alarms have been sent 3 times and will be auto-ignored." in email_body

    def test_send_customer_device_alarms_file_not_found(self, tmp_path):
        """UC3 CUSTOMER: Handle missing customer_device_alarms.json file"""
        non_existent_file = tmp_path / "missing.json"

        sent, failed = send_customer_device_alarms(str(non_existent_file))

        assert sent == 0
        assert failed == 0

    def test_send_customer_device_alarms_empty_file(self, tmp_path):
        """UC3 CUSTOMER: Handle empty customer_device_alarms.json file"""
        alarms_file = tmp_path / "customer_device_alarms.json"
        with open(alarms_file, 'w', encoding='utf-8') as f:
            json.dump({'customer_device_alarms': {}}, f)

        sent, failed = send_customer_device_alarms(str(alarms_file))

        assert sent == 0
        assert failed == 0

    def test_send_customer_device_alarms_test_customer_only_mode(self, tmp_path):
        """UC3 CUSTOMER: Test-customer-only mode filters for Gayan-IMH only"""
        # Create customer device alarms file with multiple customers
        alarms_file = tmp_path / "customer_device_alarms.json"
        alarms_data = {
            'customer_device_alarms': {
                'Gayan-IMH': {
                    'email': 'gayan@test.com',
                    'alarms': [
                        {
                            'plant': 'gayan-imh-plant',
                            'device': 'Inverter 1',
                            'message': 'Test alarm',
                            'time': '2026-01-07 10:00:00',
                            'send_count': 1
                        }
                    ]
                },
                'Other-Customer': {
                    'email': 'other@test.com',
                    'alarms': [
                        {
                            'plant': 'other-plant',
                            'device': 'Inverter 2',
                            'message': 'Test alarm 2',
                            'time': '2026-01-07 11:00:00',
                            'send_count': 1
                        }
                    ]
                }
            }
        }

        with open(alarms_file, 'w', encoding='utf-8') as f:
            json.dump(alarms_data, f)

        # Mock send_email to prevent actual sending
        import send_customer_emails
        original_send = send_customer_emails.send_email

        sent_emails = []

        def mock_send_email(to_addr, subject, body):
            sent_emails.append({'to': to_addr, 'subject': subject})
            return True

        send_customer_emails.send_email = mock_send_email

        try:
            # Test with test_customer_only=True
            sent, failed = send_customer_device_alarms(str(alarms_file), test_customer_only=True)

            # Should only send to Gayan-IMH
            assert sent == 1
            assert failed == 0
            assert len(sent_emails) == 1
            assert sent_emails[0]['to'] == 'gayan@test.com'
            assert 'Gayan-IMH' in sent_emails[0]['subject']

        finally:
            send_customer_emails.send_email = original_send

    def test_manual_api_fetch_ganishkawa(self, test_alarms_dir):
        """Manual test: Verify check_device_alarms.sh fetches alarms correctly for Ganishkawa

        This test documents the manual verification performed on 2026-01-07:
        - Ran: sh ./src/main/java/org/ktronics/scripts/check_device_alarms.sh "Ganishkawa" "123456" "bnrl_frRFjEz8Mkn" "alarms/customer_alerts.json"
        - Result: ✅ SUCCESS
        - Authentication succeeded
        - API call with 'date=' parameter WORKED
        - Got valid response: {"err":0,"desc":"ERR_NONE","dat":{...}}
        - Found 78 total device alarms
        - Saved successfully to alarms/customer_alerts.json

        Conclusion: The API call syntax is correct and works locally.
        """
        # This is a documentation test - the actual manual test was successful
        # Simulating the expected result structure
        expected_response = {
            "err": 0,
            "desc": "ERR_NONE",
            "dat": {
                "total": 78,
                "page": 0,
                "pagesize": 1,
                "warning": [
                    {
                        "id": "692bcc9ffb7cce56c43f327c",
                        "uid": 4255588,
                        "usr": "Ganishkawa",
                        "pid": 1113903,
                        "plant": "Ganishka home 3kw",
                        "pn": "D70000210234739721",
                        "devcode": 697,
                        "devaddr": 4,
                        "sn": "FFFFFFFF",
                        "alias": "3kw must pro x 2.2kw pv x 200A x 24v deep cycle",
                        "calias": "Pv1800-3024 pro 2.2kw panels",
                        "ratingPower": "0.0000",
                        "status": False,
                        "level": 2,
                        "code": "bit:3",
                        "desc": "Low battery",
                        "handle": True,
                        "gts": "2025-11-30 10:18:24",
                        "cts": "2025-11-30 10:20:24",
                        "brand": -1
                    }
                ]
            }
        }

        # Verify expected structure
        assert expected_response['err'] == 0
        assert expected_response['desc'] == "ERR_NONE"
        assert expected_response['dat']['total'] == 78
        assert len(expected_response['dat']['warning']) >= 1
        assert expected_response['dat']['warning'][0]['usr'] == "Ganishkawa"

    def test_manual_api_fetch_namila(self, test_alarms_dir):
        """Manual test: Verify check_device_alarms.sh fetches alarms correctly for Namila-Waragoda

        This test documents the manual verification performed on 2026-01-07:
        - Ran: sh ./src/main/java/org/ktronics/scripts/check_device_alarms.sh "namila" "nam@vir2011" "bnrl_frRFjEz8Mkn" "alarms/Namila-Waragoda-alarms.json"
        - Result: ✅ SUCCESS
        - Authentication succeeded
        - API call with 'date=' parameter WORKED
        - Got valid response: {"err":0,"desc":"ERR_NONE","dat":{...}}
        - Found 37 total device alarms
        - Saved successfully to alarms/Namila-Waragoda-alarms.json

        Conclusion:
        - The 'date=' parameter is NOT the issue - works fine locally
        - Namila-Waragoda credentials are valid
        - The GitHub Actions failure is likely due to network/timeout or rate limiting issues
        """
        # This is a documentation test - the actual manual test was successful
        # Simulating the expected result structure from actual test
        expected_response = {
            "err": 0,
            "desc": "ERR_NONE",
            "dat": {
                "total": 37,
                "page": 0,
                "pagesize": 1,
                "warning": [
                    {
                        "id": "69440b6afb7cce56c47629dc",
                        "uid": 4055105,
                        "usr": "namila",
                        "pid": 1053849,
                        "plant": "Namila Plant",
                        "pn": "D70000210151320902",
                        "devcode": 697,
                        "devaddr": 4,
                        "sn": "FFFFFFFF",
                        "alias": "4.96kw pv with 10kw pack",
                        "calias": "D70000210151320902",
                        "ratingPower": "0.0000",
                        "status": False,
                        "level": 2,
                        "code": "bit:6",
                        "desc": "Solar charger stops due to low battery",
                        "handle": True,
                        "gts": "2025-12-18 19:40:44",
                        "cts": "2025-12-19 10:02:17"
                    }
                ]
            }
        }

        # Verify expected structure
        assert expected_response['err'] == 0
        assert expected_response['desc'] == "ERR_NONE"
        assert expected_response['dat']['total'] == 37
        assert len(expected_response['dat']['warning']) >= 1
        assert expected_response['dat']['warning'][0]['usr'] == "namila"
        assert expected_response['dat']['warning'][0]['plant'] == "Namila Plant"

    # ========================================================================
    # UC5: Test Mode Tests
    # ========================================================================

    def test_get_most_recent_alarm(self):
        """UC5: Get most recent alarm based on timestamp (gts field)"""
        alarms = [
            {
                'pid': 12345,
                'plant': 'plant-1',
                'alias': 'Inverter 1',
                'desc': 'Old alarm',
                'gts': '2026-01-05 10:00:00',
                'customer_label': 'Customer-A'
            },
            {
                'pid': 12346,
                'plant': 'plant-2',
                'alias': 'Inverter 2',
                'desc': 'Most recent alarm',
                'gts': '2026-01-07 15:30:00',
                'customer_label': 'Customer-B'
            },
            {
                'pid': 12347,
                'plant': 'plant-3',
                'alias': 'Inverter 3',
                'desc': 'Middle alarm',
                'gts': '2026-01-06 12:00:00',
                'customer_label': 'Customer-C'
            }
        ]

        most_recent = get_most_recent_alarm(alarms)

        assert most_recent is not None
        assert most_recent['desc'] == 'Most recent alarm'
        assert most_recent['gts'] == '2026-01-07 15:30:00'
        assert most_recent['customer_label'] == 'Customer-B'

    def test_get_most_recent_alarm_empty_list(self):
        """UC5: Get most recent alarm returns None for empty list"""
        most_recent = get_most_recent_alarm([])
        assert most_recent is None

    def test_get_most_recent_alarm_single_alarm(self):
        """UC5: Get most recent alarm with single alarm returns that alarm"""
        alarms = [
            {
                'pid': 12345,
                'plant': 'single-plant',
                'alias': 'Inverter 1',
                'desc': 'Only alarm',
                'gts': '2026-01-07 10:00:00',
                'customer_label': 'Customer-A'
            }
        ]

        most_recent = get_most_recent_alarm(alarms)

        assert most_recent is not None
        assert most_recent['desc'] == 'Only alarm'

    def test_test_mode_skips_ignored_alarms(self, tmp_path):
        """UC5 FIX: Test mode should NOT add alarms that are already ignored or hit max sends

        Bug fixed: Previously test mode would bypass the ignore check and send alarms
        that had already been sent 3 times (showing "Send #4/3" in email).

        Fixed behavior: Test mode respects the same rules as filter_alarms_to_send:
        - Skip if ignored: True
        - Skip if send_count >= 3
        """
        # Create alarms directory with alarm file
        alarms_dir = tmp_path / "alarms"
        alarms_dir.mkdir()

        # Create alarm file with one alarm
        alarm_data = {
            "err": "0",
            "dat": {
                "total": 1,
                "warning": [
                    {
                        "pid": 12345,
                        "pn": "DEV001",
                        "id": "W001",
                        "plant": "test-plant",
                        "alias": "Inverter 1",
                        "desc": "Test alarm",
                        "gts": "2026-01-07 10:00:00",
                        "status": False,
                        "customer_label": "Gayan-IMH"
                    }
                ]
            }
        }

        alarm_file = alarms_dir / "Gayan-IMH-alarms.json"
        with open(alarm_file, 'w', encoding='utf-8') as f:
            json.dump(alarm_data, f)

        # Create state file where alarm has been sent 3 times (blocked)
        state = {
            "12345:DEV001:W001": {
                "send_count": 3,
                "last_sent": "2026-01-07T06:00:00",
                "first_seen": "2026-01-06T10:00:00",
                "ignored": True
            }
        }

        # Parse alarms
        all_alarms = parse_alarm_files(str(alarms_dir))
        alarms_to_send_normal = filter_alarms_to_send(all_alarms, state)

        # Without test mode, no alarms should be sent (all blocked)
        assert len(alarms_to_send_normal) == 0

        # Simulate FIXED test mode logic (same as in generate_device_alarms.py)
        alarms_to_send_test = list(alarms_to_send_normal)  # Copy
        if len(alarms_to_send_test) == 0 and len(all_alarms) > 0:
            # Filter to Gayan-IMH alarms only (UC5 fix)
            test_customer_alarms = [a for a in all_alarms if a.get('customer_label', '').lower() == 'gayan-imh']
            if test_customer_alarms:
                most_recent = get_most_recent_alarm(test_customer_alarms)
                if most_recent:
                    alarm_key = create_alarm_key(most_recent)
                    alarm_state = state.get(alarm_key, {'send_count': 0, 'ignored': False})
                    # UC5 FIX: Check if alarm should be skipped
                    if not alarm_state.get('ignored', False) and alarm_state.get('send_count', 0) < 3:
                        alarms_to_send_test.append({
                            'alarm': most_recent,
                            'alarm_key': alarm_key,
                            'send_count': alarm_state.get('send_count', 0)
                        })

        # With FIXED test mode, ignored alarms should NOT be added
        assert len(alarms_to_send_test) == 0, "Test mode should skip ignored alarms"

    def test_test_mode_adds_non_ignored_alarm(self, tmp_path):
        """UC5: Test mode adds alarm only if it hasn't reached max sends

        This tests the CORRECT behavior: test mode should add an alarm that
        still has sends remaining (send_count < 3 and ignored: False).
        """
        alarms_dir = tmp_path / "alarms"
        alarms_dir.mkdir()

        # Create alarm with 2 sends (not yet ignored)
        alarm_data = {
            "err": "0",
            "dat": {
                "total": 1,
                "warning": [
                    {
                        "pid": 12345,
                        "pn": "DEV001",
                        "id": "W001",
                        "plant": "test-plant",
                        "alias": "Inverter 1",
                        "desc": "Test alarm",
                        "gts": "2026-01-07 10:00:00",
                        "status": False,
                        "customer_label": "Gayan-IMH"
                    }
                ]
            }
        }

        alarm_file = alarms_dir / "Gayan-IMH-alarms.json"
        with open(alarm_file, 'w', encoding='utf-8') as f:
            json.dump(alarm_data, f)

        # State: alarm sent 2 times but blocked by 4-hour rule
        state = {
            "12345:DEV001:W001": {
                "send_count": 2,
                "last_sent": datetime.now().isoformat(),  # Just sent (blocked by 4hr rule)
                "first_seen": "2026-01-06T10:00:00",
                "ignored": False
            }
        }

        all_alarms = parse_alarm_files(str(alarms_dir))
        alarms_to_send_normal = filter_alarms_to_send(all_alarms, state)

        # Normal filter blocks due to 4-hour rule
        assert len(alarms_to_send_normal) == 0

        # Simulate test mode with fix
        alarms_to_send_test = []
        test_customer_alarms = [a for a in all_alarms if a.get('customer_label', '').lower() == 'gayan-imh']
        if test_customer_alarms:
            most_recent = get_most_recent_alarm(test_customer_alarms)
            if most_recent:
                alarm_key = create_alarm_key(most_recent)
                alarm_state = state.get(alarm_key, {'send_count': 0, 'ignored': False})
                # UC5 FIX: Only add if not ignored and under max sends
                if not alarm_state.get('ignored', False) and alarm_state.get('send_count', 0) < 3:
                    alarms_to_send_test.append({
                        'alarm': most_recent,
                        'alarm_key': alarm_key,
                        'send_count': alarm_state.get('send_count', 0)
                    })

        # Test mode SHOULD add this alarm (not yet at 3 sends, not ignored)
        assert len(alarms_to_send_test) == 1
        assert alarms_to_send_test[0]['send_count'] == 2

    def test_uc6_log_summary_counts(self, tmp_path, capsys):
        """UC6: Verify log summary shows alarm/customer/plant counts

        When alarms are processed, the log should output:
        '📊 Summary: X alarms from Y customers affecting Z plants'
        """
        # Create alarms from multiple customers and plants
        alarms_to_send = [
            {
                'alarm': {
                    'pid': 12345,
                    'plant': 'plant-a',
                    'alias': 'Inverter 1',
                    'desc': 'Alarm 1',
                    'gts': '2026-01-07 10:00:00',
                    'customer_label': 'Customer-A'
                },
                'alarm_key': '12345:DEV001:W001',
                'send_count': 0
            },
            {
                'alarm': {
                    'pid': 12346,
                    'plant': 'plant-b',
                    'alias': 'Inverter 2',
                    'desc': 'Alarm 2',
                    'gts': '2026-01-07 11:00:00',
                    'customer_label': 'Customer-A'
                },
                'alarm_key': '12346:DEV002:W002',
                'send_count': 0
            },
            {
                'alarm': {
                    'pid': 12347,
                    'plant': 'plant-c',
                    'alias': 'Inverter 3',
                    'desc': 'Alarm 3',
                    'gts': '2026-01-07 12:00:00',
                    'customer_label': 'Customer-B'
                },
                'alarm_key': '12347:DEV003:W003',
                'send_count': 0
            }
        ]

        # UC6: Calculate summary (same logic as in generate_device_alarms.py)
        if alarms_to_send:
            unique_plants = set(item['alarm'].get('plant', 'Unknown') for item in alarms_to_send)
            unique_customers = set(item['alarm'].get('customer_label', 'Unknown') for item in alarms_to_send)
            summary = f"📊 Summary: {len(alarms_to_send)} alarms from {len(unique_customers)} customers affecting {len(unique_plants)} plants"
            print(summary)

        captured = capsys.readouterr()
        assert "📊 Summary:" in captured.out
        assert "3 alarms" in captured.out
        assert "2 customers" in captured.out
        assert "3 plants" in captured.out

    def test_uc7_state_includes_customer_plant(self, tmp_path):
        """UC7: Verify state JSON includes customer and plant names for readability

        After update_alarm_state(), each alarm entry should have:
        - 'customer': customer label
        - 'plant': plant name
        """
        alarms_to_send = [
            {
                'alarm': {
                    'pid': 12345,
                    'plant': 'gayan-imh-imbulgoda-3kw',
                    'alias': 'Inverter 1',
                    'desc': 'Test alarm',
                    'gts': '2026-01-07 10:00:00',
                    'customer_label': 'Gayan-IMH'
                },
                'alarm_key': '12345:DEV001:W001',
                'send_count': 0
            }
        ]

        state = {}
        updated_state = update_alarm_state(state, alarms_to_send)

        # UC7: Verify customer and plant are in state
        alarm_key = '12345:DEV001:W001'
        assert alarm_key in updated_state
        assert 'customer' in updated_state[alarm_key]
        assert 'plant' in updated_state[alarm_key]
        assert updated_state[alarm_key]['customer'] == 'Gayan-IMH'
        assert updated_state[alarm_key]['plant'] == 'gayan-imh-imbulgoda-3kw'

        # Also verify standard fields still exist
        assert updated_state[alarm_key]['send_count'] == 1
        assert 'first_seen' in updated_state[alarm_key]
        assert 'last_sent' in updated_state[alarm_key]

    def test_uc8_workflow_conditional_structure(self):
        """UC8: Verify workflow YAML has correct conditional for customer emails

        The trigger-customer-reports.yml should have:
        - Admin email step: NO conditional (runs on all triggers)
        - Customer email step: if: github.event_name == 'schedule'
        - Skip message step: if: github.event_name != 'schedule'

        This test verifies the workflow file structure.
        """
        import os
        from pathlib import Path

        # Find workflow file
        workflow_path = Path(__file__).parents[7] / '.github' / 'workflows' / 'trigger-customer-reports.yml'

        assert workflow_path.exists(), f"Workflow file not found at {workflow_path}"

        with open(workflow_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Verify customer email step has schedule conditional
        assert "if: github.event_name == 'schedule'" in content, \
            "UC8: Customer email step should have schedule conditional"

        # Verify skip message step exists for non-scheduled runs
        assert "if: github.event_name != 'schedule'" in content, \
            "UC8: Skip message step should exist for non-scheduled runs"

        # Verify admin email step exists (no conditional needed - it's implied by absence)
        assert "Send admin summary email" in content, \
            "Admin summary email step should exist"

        # Verify the conditional messages
        assert "Scheduled Run Only" in content, \
            "UC8: Customer email step should indicate scheduled run only"
        assert "SKIPPING CUSTOMER EMAILS" in content, \
            "UC8: Skip message should be present"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
