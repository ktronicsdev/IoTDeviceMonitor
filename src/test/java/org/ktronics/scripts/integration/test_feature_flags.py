#!/usr/bin/env python3
"""
Build Verification Test (BVT) for the email/alert feature flags.

Every outbound email is gated by a flag in
src/main/java/org/ktronics/config/features.json so channels can be switched off
without deleting code.

The regression test that matters here is
test_every_email_step_is_gated_by_a_flag: it walks the workflow YAML and fails
if anyone adds a new email-sending step without a flag condition. Without it,
a new ungated channel would silently bypass the whole mechanism.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[7]
SCRIPTS_DIR = PROJECT_ROOT / "src" / "main" / "java" / "org" / "ktronics" / "scripts"
CONFIG_DIR = PROJECT_ROOT / "src" / "main" / "java" / "org" / "ktronics" / "config"
WORKFLOWS_DIR = PROJECT_ROOT / ".github" / "workflows"
FEATURES_FILE = CONFIG_DIR / "features.json"

sys.path.insert(0, str(SCRIPTS_DIR))

import features  # noqa: E402

# Scripts whose presence in a `run:` block means the step sends mail.
EMAIL_SENDERS = (
    "send_email.py",
    "send_customer_emails.py",
    "send_customer_device_alarms",
)


@pytest.fixture(autouse=True)
def _clean_flag_env():
    """Strip KT_FEATURE_* overrides so tests see features.json, not the shell."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("KT_FEATURE_")}
    for k in saved:
        del os.environ[k]
    features.reset_cache()
    yield
    for k, v in saved.items():
        os.environ[k] = v
    features.reset_cache()


class TestFeaturesFile:
    """The flags file itself."""

    def test_features_file_exists(self):
        assert FEATURES_FILE.exists(), "features.json must exist"

    def test_features_file_is_valid_json(self):
        json.loads(FEATURES_FILE.read_text(encoding="utf-8"))

    def test_features_file_is_committed(self):
        """Unlike credentials.json, the flags file must NOT be gitignored."""
        result = subprocess.run(
            ["git", "check-ignore", str(FEATURES_FILE)],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        )
        assert result.returncode != 0, \
            "features.json must be tracked in git — flags are reviewable config, not secrets"

    def test_every_email_channel_has_a_flag(self):
        data = json.loads(FEATURES_FILE.read_text(encoding="utf-8"))
        expected = {
            "weekly_customer_reports", "weekly_admin_summary",
            "device_alarms_admin", "device_alarms_customer",
            "production_alerts_admin", "production_alerts_customer",
            "build_verification", "test_regression",
        }
        assert expected <= set(data["emails"]), \
            f"missing email flags: {expected - set(data['emails'])}"

    def test_severity_flags_present(self):
        data = json.loads(FEATURES_FILE.read_text(encoding="utf-8"))
        assert "production_red_3day" in data["alerts"]
        assert "production_orange_3month" in data["alerts"]


class TestFeatureReader:
    """features.py behaviour."""

    def test_reads_a_real_flag(self):
        assert isinstance(features.is_enabled("emails.device_alarms_admin"), bool)

    def test_env_override_turns_a_flag_on(self):
        os.environ["KT_FEATURE_EMAILS_WEEKLY_CUSTOMER_REPORTS"] = "true"
        features.reset_cache()
        assert features.is_enabled("emails.weekly_customer_reports") is True

    def test_env_override_turns_a_flag_off(self):
        os.environ["KT_FEATURE_EMAILS_DEVICE_ALARMS_ADMIN"] = "false"
        features.reset_cache()
        assert features.is_enabled("emails.device_alarms_admin") is False

    def test_unparseable_override_is_ignored(self):
        os.environ["KT_FEATURE_EMAILS_DEVICE_ALARMS_ADMIN"] = "maybe"
        features.reset_cache()
        assert features.is_enabled("emails.device_alarms_admin") is True

    def test_unknown_flag_fails_open(self):
        """A flag nobody defined must default ON — never silently mute a channel."""
        assert features.is_enabled("emails.channel_that_does_not_exist") is True

    def test_missing_file_fails_open(self, monkeypatch, tmp_path):
        """A broken/absent flags file must not silence alerting."""
        monkeypatch.setattr(features, "FEATURES_PATH", tmp_path / "nope.json")
        features.reset_cache()
        assert features.is_enabled("emails.device_alarms_admin") is True

    def test_corrupt_file_fails_open(self, monkeypatch, tmp_path):
        bad = tmp_path / "features.json"
        bad.write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(features, "FEATURES_PATH", bad)
        features.reset_cache()
        assert features.is_enabled("emails.device_alarms_admin") is True

    def test_env_name_mapping(self):
        assert features.env_name("emails.weekly_customer_reports") == \
            "KT_FEATURE_EMAILS_WEEKLY_CUSTOMER_REPORTS"

    def test_all_flags_is_flat_and_skips_metadata(self):
        flat = features.all_flags()
        assert "emails.device_alarms_admin" in flat
        assert all(isinstance(v, bool) for v in flat.values())
        assert not any(k.startswith("_") or k == "version" for k in flat)

    def test_github_output_uses_underscores(self):
        import io
        buf = io.StringIO()
        features._emit_github_output(buf)
        lines = buf.getvalue().strip().splitlines()
        assert lines, "expected at least one output line"
        for line in lines:
            name, _, value = line.partition("=")
            assert "." not in name, f"'.' is not legal in a workflow output name: {name}"
            assert value in ("true", "false")


class TestWorkflowGating:
    """The regression guard: no ungated email may exist in any workflow."""

    @staticmethod
    def _email_steps():
        for wf in sorted(WORKFLOWS_DIR.glob("*.yml")):
            doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
            for job_name, job in (doc.get("jobs") or {}).items():
                job_if = str(job.get("if", ""))
                for step in (job.get("steps") or []):
                    blob = str(step.get("run", "")) + str(step.get("with", ""))
                    if any(sender in blob for sender in EMAIL_SENDERS):
                        yield wf.name, job_name, step, job_if

    def test_every_email_step_is_gated_by_a_flag(self):
        ungated = [
            f"{wf} :: {step.get('name', '?')}"
            for wf, _job, step, job_if in self._email_steps()
            if "flags.outputs" not in str(step.get("if", "")) and "flags.outputs" not in job_if
        ]
        assert not ungated, (
            "These email steps send mail with no feature flag gating them:\n  "
            + "\n  ".join(ungated)
            + "\nAdd an `if:` referencing steps.flags.outputs.* (or needs.flags.outputs.*)."
        )

    def test_every_gated_job_can_resolve_its_flags(self):
        """A step referencing steps.flags.* needs a `flags` step in the same job."""
        problems = []
        for wf in sorted(WORKFLOWS_DIR.glob("*.yml")):
            doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
            for job_name, job in (doc.get("jobs") or {}).items():
                steps = job.get("steps") or []
                has_flags_step = any(s.get("id") == "flags" for s in steps)
                for step in steps:
                    cond = str(step.get("if", ""))
                    if "steps.flags.outputs" in cond and not has_flags_step:
                        problems.append(f"{wf.name} :: {job_name} :: {step.get('name','?')}")
        assert not problems, \
            "steps reference steps.flags.* but their job has no `id: flags` step:\n  " \
            + "\n  ".join(problems)

    def test_weekly_workflows_short_circuit_at_job_level(self):
        """Weekly report jobs must no-op entirely, not generate reports nobody gets."""
        for name in ("trigger-customer-reports.yml", "trigger-dessmonitor-weekly-reports.yml"):
            doc = yaml.safe_load((WORKFLOWS_DIR / name).read_text(encoding="utf-8"))
            jobs = doc["jobs"]
            assert "flags" in jobs, f"{name} must have a `flags` job"
            main = jobs["generate-and-send-reports"]
            assert "needs.flags.outputs.any_weekly" in str(main.get("if", "")), \
                f"{name}: report generation must be skipped when no weekly email is enabled"

    def test_failure_emails_fail_open(self):
        """Build-failure mail must use != 'false' so an early crash still notifies."""
        doc = yaml.safe_load((WORKFLOWS_DIR / "trigger-shinemonitor.yml").read_text(encoding="utf-8"))
        checked = 0
        for job in doc["jobs"].values():
            for step in (job.get("steps") or []):
                cond = str(step.get("if", ""))
                if "failure()" in cond and "flags.outputs" in cond:
                    assert "!= 'false'" in cond, (
                        f"{step.get('name','?')}: failure-path mail must use != 'false' "
                        "so it still sends when the flags step never ran"
                    )
                    checked += 1
        assert checked >= 2, "expected at least the two failure-path email steps"


class TestAnomalySeverityFlags:
    """check_anomaly.py must honour the severity flags."""

    def test_cli_switches_exist(self):
        content = (SCRIPTS_DIR / "check_anomaly.py").read_text(encoding="utf-8")
        assert "--no-orange" in content
        assert "--no-red" in content

    def test_defaults_come_from_features(self):
        content = (SCRIPTS_DIR / "check_anomaly.py").read_text(encoding="utf-8")
        assert 'is_enabled("alerts.production_orange_3month")' in content
        assert 'is_enabled("alerts.production_red_3day")' in content

    def test_red_gate_does_not_break_zero_run_detection(self):
        """
        REGRESSION: an early draft disabled RED by emptying window_days, which
        also killed the 0-kWh suppression rule and IndexError'd on window_days[0].
        The gate must sit on the alert append instead.
        """
        content = (SCRIPTS_DIR / "check_anomaly.py").read_text(encoding="utf-8")
        assert "window_days = []" not in content, \
            "RED must not be disabled by emptying window_days — zero-run detection shares it"
        assert "and not args.no_red:" in content


class TestBashAccessor:
    """common_config.sh exposes the same flags to the bash scripts."""

    def test_feature_enabled_function_exists(self):
        content = (SCRIPTS_DIR / "common_config.sh").read_text(encoding="utf-8")
        assert "feature_enabled()" in content
        assert "FEATURES_FILE=" in content

    def test_bash_accessor_fails_open(self):
        content = (SCRIPTS_DIR / "common_config.sh").read_text(encoding="utf-8")
        assert '[ -f "$FEATURES_FILE" ] || return 0' in content, \
            "a missing flags file must be treated as 'all channels on'"


class TestCurrentProfile:
    """
    Documents the profile the flags are SET to today. Change these assertions
    deliberately when the profile changes — they exist so a flag flip is never
    silent.

    Reviewed 13 Sep 2026: UC2 customer weekly reports re-enabled; the admin
    weekly summary stays off; ORANGE stays off by decision.
    """

    def test_customer_weekly_reports_are_on(self):
        """UC2 is live again — customers get their Sunday production summary."""
        assert features.is_enabled("emails.weekly_customer_reports") is True

    def test_admin_weekly_summary_is_off(self):
        assert features.is_enabled("emails.weekly_admin_summary") is False

    def test_three_month_orange_is_off(self):
        assert features.is_enabled("alerts.production_orange_3month") is False

    def test_critical_channels_are_on(self):
        assert features.is_enabled("emails.device_alarms_admin") is True
        assert features.is_enabled("emails.production_alerts_admin") is True
        assert features.is_enabled("alerts.production_red_3day") is True
