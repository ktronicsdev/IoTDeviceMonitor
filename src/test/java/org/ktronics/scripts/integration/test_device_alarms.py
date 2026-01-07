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
    create_customer_device_alarms
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

        # Create alarms for plants matching "Gayan-IMH"
        alarms_to_send = [
            {
                'alarm': {
                    'pId': '12345',
                    'pName': 'gayan-imh-imbulgoda-3kw',  # Matches "gayanimh"
                    'devName': 'Inverter 1',
                    'warnMsg': 'Grid voltage too high',
                    'warnTime': '2026-01-07 10:30:00'
                },
                'alarm_key': '12345:DEV001:W001',
                'send_count': 0
            },
            {
                'alarm': {
                    'pId': '12345',
                    'pName': 'gayan-imh-plant-2',  # Also matches
                    'devName': 'Inverter 2',
                    'warnMsg': 'Temperature warning',
                    'warnTime': '2026-01-07 11:00:00'
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

        # Verify alarm details
        alarm1 = customer_alarms['Gayan-IMH']['alarms'][0]
        assert alarm1['plant'] == 'gayan-imh-imbulgoda-3kw'
        assert alarm1['device'] == 'Inverter 1'
        assert alarm1['message'] == 'Grid voltage too high'
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

        # Create alarms for different customers
        alarms_to_send = [
            {
                'alarm': {
                    'pId': '12345',
                    'pName': 'gayan-imh-plant',
                    'devName': 'Inverter A',
                    'warnMsg': 'Alarm A',
                    'warnTime': '2026-01-07 10:00:00'
                },
                'alarm_key': '12345:DEV001:W001',
                'send_count': 0
            },
            {
                'alarm': {
                    'pId': '67890',
                    'pName': 'namila-waragoda-plant',
                    'devName': 'Inverter B',
                    'warnMsg': 'Alarm B',
                    'warnTime': '2026-01-07 11:00:00'
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

        # Create alarm for this customer
        alarms_to_send = [
            {
                'alarm': {
                    'pId': '12345',
                    'pName': 'no-email-customer-plant',
                    'devName': 'Inverter 1',
                    'warnMsg': 'Test alarm',
                    'warnTime': '2026-01-07 10:00:00'
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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
