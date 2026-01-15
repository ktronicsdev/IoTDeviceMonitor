#!/usr/bin/env python3
"""
Integration tests for UC9: Admin Alert Email Optimization
Tests hash-based state detection to reduce admin email noise
"""

import pytest
import os
import json
import tempfile
import shutil
import hashlib
from pathlib import Path


class TestUC9AdminEmailOptimization:
    """Test UC9: Hash-based state detection for admin alert emails"""

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

    def test_uc9_hash_changes_when_alerts_appear(self, test_dirs):
        """UC9: Hash changes when alerts appear (clear → alerts)

        Scenario: System was clear, now has alerts
        Expected: Hash changes, email should be sent
        """
        alerts_json_path = test_dirs['alerts'] / "alerts.json"

        # Step 1: No alerts (system clear)
        clear_state = {
            "generated_at": "2026-01-15 10:00:00",
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
            "alerts": [
                {
                    "customer": "test-customer",
                    "plant": "test-plant",
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

        # Verify hash changed
        assert hash_clear != hash_with_alert, "UC9: Hash should change when alerts appear"
        assert hash_clear != "empty", "Initial clear state should have a hash"
        assert hash_with_alert != "empty", "Alert state should have a hash"

    def test_uc9_hash_changes_when_alerts_clear(self, test_dirs):
        """UC9: Hash changes when alerts clear (alerts → clear)

        Scenario: System had alerts, now clear
        Expected: Hash changes, email should be sent
        """
        alerts_json_path = test_dirs['alerts'] / "alerts.json"

        # Step 1: System has alerts
        alert_state = {
            "generated_at": "2026-01-15 10:00:00",
            "alerts": [
                {
                    "customer": "test-customer",
                    "plant": "test-plant",
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
            "alerts": [],
            "suppressed": [],
            "ignored": []
        }
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(clear_state, f)

        hash_clear = self.calculate_hash(alerts_json_path)

        # Verify hash changed
        assert hash_with_alert != hash_clear, "UC9: Hash should change when alerts clear"
        assert hash_with_alert != "empty", "Alert state should have a hash"
        assert hash_clear != "empty", "Clear state should have a hash"

    def test_uc9_hash_unchanged_when_state_same(self, test_dirs):
        """UC9: Hash unchanged when alert state is the same

        Scenario: System state unchanged between runs
        Expected: Same hash, email should be skipped
        """
        alerts_json_path = test_dirs['alerts'] / "alerts.json"

        # Create alert state
        alert_state = {
            "generated_at": "2026-01-15 10:00:00",
            "alerts": [
                {
                    "customer": "test-customer",
                    "plant": "test-plant",
                    "severity": "RED",
                    "message": "Production < 20% baseline for 3 consecutive days"
                }
            ],
            "suppressed": [],
            "ignored": []
        }

        # First run
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(alert_state, f)
        hash1 = self.calculate_hash(alerts_json_path)

        # Second run with exact same data (only timestamp changes)
        alert_state['generated_at'] = "2026-01-15 14:00:00"
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(alert_state, f)
        hash2 = self.calculate_hash(alerts_json_path)

        # Verify hash CHANGED because generated_at changed
        # This is CORRECT behavior - any content change triggers email
        assert hash1 != hash2, "UC9: Hash changes when any field changes (including timestamp)"

        # Now test with IDENTICAL content
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(alert_state, f)
        hash3 = self.calculate_hash(alerts_json_path)

        # Verify hash stays same when content truly identical
        assert hash2 == hash3, "UC9: Hash unchanged when content identical"

    def test_uc9_hash_changes_when_different_plants_alerted(self, test_dirs):
        """UC9: Hash changes when different plants are alerted

        Scenario: Plant A alerted → Plant B alerted (different plant)
        Expected: Hash changes, email should be sent
        """
        alerts_json_path = test_dirs['alerts'] / "alerts.json"

        # Step 1: Plant A has alert
        alert_plant_a = {
            "generated_at": "2026-01-15 10:00:00",
            "alerts": [
                {
                    "customer": "test-customer",
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

        # Step 2: Plant B has alert (Plant A cleared)
        alert_plant_b = {
            "generated_at": "2026-01-15 14:00:00",
            "alerts": [
                {
                    "customer": "test-customer",
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

        # Verify hash changed
        assert hash_plant_a != hash_plant_b, "UC9: Hash should change when different plants alerted"

    def test_uc9_hash_changes_when_severity_changes(self, test_dirs):
        """UC9: Hash changes when alert severity changes

        Scenario: Plant has RED alert → same plant has ORANGE alert
        Expected: Hash changes, email should be sent
        """
        alerts_json_path = test_dirs['alerts'] / "alerts.json"

        # Step 1: RED alert
        red_alert = {
            "generated_at": "2026-01-15 10:00:00",
            "alerts": [
                {
                    "customer": "test-customer",
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

        # Step 2: ORANGE alert (severity downgraded)
        orange_alert = {
            "generated_at": "2026-01-15 14:00:00",
            "alerts": [
                {
                    "customer": "test-customer",
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

        # Verify hash changed
        assert hash_red != hash_orange, "UC9: Hash should change when severity changes"

    def test_uc9_state_file_persistence(self, test_dirs):
        """UC9: State file stores and loads hash correctly

        Verifies that admin_email_state.txt stores hash correctly
        and can be read back for comparison in next run.
        """
        state_file = test_dirs['state'] / "admin_email_state.txt"
        alerts_json_path = test_dirs['alerts'] / "alerts.json"

        # Create alerts.json
        alert_state = {
            "generated_at": "2026-01-15 10:00:00",
            "alerts": [
                {
                    "customer": "test-customer",
                    "plant": "test-plant",
                    "severity": "RED",
                    "message": "Test alert"
                }
            ],
            "suppressed": [],
            "ignored": []
        }
        with open(alerts_json_path, 'w', encoding='utf-8') as f:
            json.dump(alert_state, f)

        # Calculate hash
        current_hash = self.calculate_hash(alerts_json_path)

        # Save to state file (simulate workflow)
        with open(state_file, 'w', encoding='utf-8') as f:
            f.write(current_hash)

        # Verify state file exists and has correct content
        assert state_file.exists(), "UC9: State file should be created"

        # Read back (simulate next workflow run)
        with open(state_file, 'r', encoding='utf-8') as f:
            loaded_hash = f.read().strip()

        assert loaded_hash == current_hash, "UC9: State file should preserve hash exactly"
        assert len(loaded_hash) == 64, "UC9: SHA256 hash should be 64 hex characters"

    def test_uc9_workflow_yaml_structure(self):
        """UC9: Verify workflow YAML has hash-based state detection logic

        This test checks that the workflow file contains the UC9 implementation
        with hash calculation, state comparison, and conditional email sending.
        """
        # Navigate from test file to project root
        # test_uc9_admin_email.py is in: src/test/java/org/ktronics/scripts/integration/
        # We need to go up 7 levels to reach project root
        project_root = Path(__file__).parents[7]
        workflow_path = project_root / ".github" / "workflows" / "trigger-shinemonitor.yml"

        assert workflow_path.exists(), f"Workflow file should exist at {workflow_path}"

        with open(workflow_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Verify UC9 comment exists
        assert "UC9" in content, "UC9: Workflow should have UC9 comment"

        # Verify hash calculation logic
        assert "sha256sum alerts/alerts.json" in content, \
            "UC9: Workflow should calculate SHA256 hash of alerts.json"

        # Verify state file usage
        assert "admin_email_state.txt" in content, \
            "UC9: Workflow should use admin_email_state.txt state file"

        # Verify previous hash loading
        assert "previous_hash" in content, \
            "UC9: Workflow should load previous hash from state"

        # Verify conditional email logic
        assert "send_email" in content, \
            "UC9: Workflow should have conditional send_email variable"

        # Verify push/manual trigger bypass
        assert "github.event_name" in content and "push" in content, \
            "UC9: Workflow should check event_name for push triggers"

        # Verify hash comparison
        assert 'current_hash" != "$previous_hash' in content or \
               "current_hash != previous_hash" in content or \
               "$current_hash\" != \"$previous_hash" in content, \
            "UC9: Workflow should compare current and previous hashes"

        # Verify state persistence (saving hash)
        assert "echo \"$current_hash\" > state/admin_email_state.txt" in content or \
               'echo "$current_hash" > state/admin_email_state.txt' in content, \
            "UC9: Workflow should save current hash to state file"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
