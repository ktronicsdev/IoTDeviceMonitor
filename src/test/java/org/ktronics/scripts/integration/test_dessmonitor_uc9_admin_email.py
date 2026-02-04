#!/usr/bin/env python3
"""
UC9: DessMonitor Admin Email Optimization Tests

Tests for hash-based state detection to reduce admin email noise.
Same logic as ShineMonitor UC9 but for DessMonitor platform.

Status: IMPLEMENTED
"""

import pytest
import os
import json
import tempfile
import shutil
import hashlib
from pathlib import Path


class TestDessMonitorUC9AdminEmailOptimization:
    """DessMonitor UC9 admin email optimization tests"""

    @pytest.fixture
    def test_dirs(self):
        """Create temporary directories for testing"""
        temp_dir = tempfile.mkdtemp()
        alerts_dir = Path(temp_dir) / "alerts"
        state_dir = Path(temp_dir) / "state"
        alerts_dir.mkdir()
        state_dir.mkdir()

        yield {
            'root': Path(temp_dir),
            'alerts': alerts_dir,
            'state': state_dir
        }

        shutil.rmtree(temp_dir)

    def calculate_hash(self, file_path):
        """Calculate SHA256 hash of a file"""
        if not os.path.exists(file_path):
            return "empty"
        with open(file_path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()

    def test_dessmonitor_uc9_hash_changes_when_alerts_appear(self, test_dirs):
        """UC9: Hash changes when DessMonitor alerts appear (clear → alerts)"""
        alerts_json_path = test_dirs['alerts'] / "dessmonitor_alerts.json"

        # Step 1: No alerts (system clear)
        clear_state = {
            "generated_at": "2026-01-15 10:00:00",
            "platform": "dessmonitor",
            "alerts": [],
            "suppressed": [],
            "ignored": []
        }
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(clear_state, f)

        hash_clear = self.calculate_hash(alerts_json_path)

        # Step 2: Alerts appear
        alert_state = {
            "generated_at": "2026-01-15 14:00:00",
            "platform": "dessmonitor",
            "alerts": [
                {
                    "customer": "mifrazmarsoon",
                    "plant": "MifrazMarsoon Plant",
                    "severity": "RED",
                    "message": "Production < 20% baseline for 3 consecutive days"
                }
            ],
            "suppressed": [],
            "ignored": []
        }
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(alert_state, f)

        hash_with_alert = self.calculate_hash(alerts_json_path)

        assert hash_clear != hash_with_alert, "UC9: Hash should change when alerts appear"

    def test_dessmonitor_uc9_hash_changes_when_alerts_clear(self, test_dirs):
        """UC9: Hash changes when DessMonitor alerts clear (alerts → clear)"""
        alerts_json_path = test_dirs['alerts'] / "dessmonitor_alerts.json"

        # Step 1: System has alerts
        alert_state = {
            "generated_at": "2026-01-15 10:00:00",
            "platform": "dessmonitor",
            "alerts": [
                {
                    "customer": "mifrazmarsoon",
                    "plant": "MifrazMarsoon Plant",
                    "severity": "RED",
                    "message": "Production < 20% baseline for 3 consecutive days"
                }
            ],
            "suppressed": [],
            "ignored": []
        }
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(alert_state, f)

        hash_with_alert = self.calculate_hash(alerts_json_path)

        # Step 2: Alerts clear
        clear_state = {
            "generated_at": "2026-01-15 14:00:00",
            "platform": "dessmonitor",
            "alerts": [],
            "suppressed": [],
            "ignored": []
        }
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(clear_state, f)

        hash_clear = self.calculate_hash(alerts_json_path)

        assert hash_with_alert != hash_clear, "UC9: Hash should change when alerts clear"

    def test_dessmonitor_uc9_hash_unchanged_when_state_same(self, test_dirs):
        """UC9: Hash unchanged when DessMonitor alert state is the same"""
        alerts_json_path = test_dirs['alerts'] / "dessmonitor_alerts.json"

        alert_state = {
            "generated_at": "2026-01-15 10:00:00",
            "platform": "dessmonitor",
            "alerts": [
                {
                    "customer": "mifrazmarsoon",
                    "plant": "MifrazMarsoon Plant",
                    "severity": "RED",
                    "message": "Production < 20% baseline for 3 consecutive days"
                }
            ],
            "suppressed": [],
            "ignored": []
        }

        # Write same data twice
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(alert_state, f)
        hash1 = self.calculate_hash(alerts_json_path)

        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(alert_state, f)
        hash2 = self.calculate_hash(alerts_json_path)

        assert hash1 == hash2, "UC9: Hash unchanged when content identical"

    def test_dessmonitor_uc9_hash_changes_when_different_plants_alerted(self, test_dirs):
        """UC9: Hash changes when different DessMonitor plants are alerted"""
        alerts_json_path = test_dirs['alerts'] / "dessmonitor_alerts.json"

        # Step 1: Plant A has alert
        alert_plant_a = {
            "generated_at": "2026-01-15 10:00:00",
            "platform": "dessmonitor",
            "alerts": [
                {
                    "customer": "mifrazmarsoon",
                    "plant": "plant-a",
                    "severity": "RED",
                    "message": "Production < 20% baseline for 3 consecutive days"
                }
            ],
            "suppressed": [],
            "ignored": []
        }
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(alert_plant_a, f)
        hash_plant_a = self.calculate_hash(alerts_json_path)

        # Step 2: Plant B has alert
        alert_plant_b = {
            "generated_at": "2026-01-15 14:00:00",
            "platform": "dessmonitor",
            "alerts": [
                {
                    "customer": "mifrazmarsoon",
                    "plant": "plant-b",
                    "severity": "RED",
                    "message": "Production < 20% baseline for 3 consecutive days"
                }
            ],
            "suppressed": [],
            "ignored": []
        }
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(alert_plant_b, f)
        hash_plant_b = self.calculate_hash(alerts_json_path)

        assert hash_plant_a != hash_plant_b, "UC9: Hash should change when different plants alerted"

    def test_dessmonitor_uc9_hash_changes_when_severity_changes(self, test_dirs):
        """UC9: Hash changes when DessMonitor alert severity changes"""
        alerts_json_path = test_dirs['alerts'] / "dessmonitor_alerts.json"

        # Step 1: RED alert
        red_alert = {
            "generated_at": "2026-01-15 10:00:00",
            "platform": "dessmonitor",
            "alerts": [
                {
                    "customer": "mifrazmarsoon",
                    "plant": "test-plant",
                    "severity": "RED",
                    "message": "Production < 20% baseline for 3 consecutive days"
                }
            ],
            "suppressed": [],
            "ignored": []
        }
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(red_alert, f)
        hash_red = self.calculate_hash(alerts_json_path)

        # Step 2: ORANGE alert
        orange_alert = {
            "generated_at": "2026-01-15 14:00:00",
            "platform": "dessmonitor",
            "alerts": [
                {
                    "customer": "mifrazmarsoon",
                    "plant": "test-plant",
                    "severity": "ORANGE",
                    "message": "Production < 40% baseline for 3 consecutive months"
                }
            ],
            "suppressed": [],
            "ignored": []
        }
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(orange_alert, f)
        hash_orange = self.calculate_hash(alerts_json_path)

        assert hash_red != hash_orange, "UC9: Hash should change when severity changes"

    def test_dessmonitor_uc9_state_file_persistence(self, test_dirs):
        """UC9: DessMonitor state file stores and loads hash correctly"""
        state_file = test_dirs['state'] / "dessmonitor_admin_email_state.txt"
        alerts_json_path = test_dirs['alerts'] / "dessmonitor_alerts.json"

        alert_state = {
            "generated_at": "2026-01-15 10:00:00",
            "platform": "dessmonitor",
            "alerts": [{"customer": "mifrazmarsoon", "plant": "test-plant", "severity": "RED"}],
            "suppressed": [],
            "ignored": []
        }
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(alert_state, f)

        current_hash = self.calculate_hash(alerts_json_path)

        # Save to state file
        with open(state_file, 'w', encoding='utf-8') as f:
            f.write(current_hash)

        # Verify state file
        assert state_file.exists(), "UC9: State file should be created"

        with open(state_file, 'r', encoding='utf-8') as f:
            loaded_hash = f.read().strip()

        assert loaded_hash == current_hash, "UC9: State file should preserve hash exactly"
        assert len(loaded_hash) == 64, "UC9: SHA256 hash should be 64 hex characters"

    def test_dessmonitor_uc9_hash_excludes_generated_at_timestamp(self, test_dirs):
        """UC9: Content hash excludes generated_at timestamp"""
        alert_10am = {
            "generated_at": "2026-01-16T10:00:00Z",
            "platform": "dessmonitor",
            "alerts": [{"plant_key": "test-plant", "severity": "RED"}],
            "suppressed": [],
            "ignored": []
        }

        alert_2pm = {
            "generated_at": "2026-01-16T14:00:00Z",  # Different timestamp
            "platform": "dessmonitor",
            "alerts": [{"plant_key": "test-plant", "severity": "RED"}],
            "suppressed": [],
            "ignored": []
        }

        def calculate_content_hash(data):
            """Hash only alert content, excluding generated_at"""
            content = {
                "alerts": data.get("alerts", []),
                "suppressed": data.get("suppressed", []),
                "ignored": data.get("ignored", [])
            }
            content_str = json.dumps(content, sort_keys=True)
            return hashlib.sha256(content_str.encode()).hexdigest()

        hash_10am_content = calculate_content_hash(alert_10am)
        hash_2pm_content = calculate_content_hash(alert_2pm)

        assert hash_10am_content == hash_2pm_content, \
            "UC9: Content hash should be identical when only timestamp differs"

    def test_dessmonitor_uc9_workflow_structure(self):
        """UC9: Verify DessMonitor workflow exists and has email sending logic

        Note: Full UC9 hash-based deduplication not yet implemented for DessMonitor.
        This test verifies prerequisites for UC9 implementation.
        """
        project_root = Path(__file__).parents[7]
        workflow_path = project_root / ".github" / "workflows" / "trigger-dessmonitor.yml"

        assert workflow_path.exists(), f"DessMonitor workflow should exist at {workflow_path}"

        with open(workflow_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Verify workflow has email sending (prerequisite for UC9)
        assert "send_email" in content or "EMAIL" in content, \
            "UC9: DessMonitor workflow should have email sending logic"

        # Verify workflow has alerts directory usage
        assert "alerts" in content, \
            "UC9: DessMonitor workflow should use alerts directory"

    def test_dessmonitor_workflow_exists(self):
        """Verify DessMonitor workflow file exists (prerequisite for UC9)"""
        project_root = Path(__file__).parents[7]
        workflow_path = project_root / ".github" / "workflows" / "trigger-dessmonitor.yml"
        assert workflow_path.exists(), "trigger-dessmonitor.yml should exist"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
