#!/usr/bin/env python3
"""
Integration tests for ShineMonitor API shared functions
Tests authentication and API calls using shinemonitor_common.sh
"""

import pytest
import shutil
import subprocess
import json
import tempfile
import platform
import sys
from pathlib import Path

# Add scripts directory to path to import centralized config
PROJECT_ROOT = Path(__file__).resolve().parents[7]
SCRIPTS_DIR = PROJECT_ROOT / "src" / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from config import CREDENTIALS_PATH

# Use centralized credentials path
CREDS_FILE = PROJECT_ROOT / CREDENTIALS_PATH

def load_test_credentials():
    """Load credentials for testing"""
    if not CREDS_FILE.exists():
        pytest.skip(f"Credentials file not found: {CREDS_FILE}")

    with open(CREDS_FILE) as f:
        data = json.load(f)

    return {
        'username': data['accounts'][0]['username'],
        'password': data['accounts'][0]['password'],
        'company_key': data['company_key']
    }


class TestShineMonitorAPI:
    """Test ShineMonitor API authentication and calls"""

    @pytest.fixture
    def test_creds(self):
        """Get test credentials"""
        return load_test_credentials()

    def run_bash_script(self, script_content, timeout=30):
        """
        Execute bash script and return output.

        These tests call web.shinemonitor.com for real, so they report on a
        third party as much as on this repository. Two of its moods are not
        our defect and must not read as one:

          * it does not answer inside the budget. One call is quick (~1.3s),
            but a whole-fleet fetch is dozens of sequential signed calls and
            the budget only has to be mildly optimistic.
          * it returns an empty body to this runner. Verified on 2026-10-21:
            the same authEmail call with the same credentials succeeded from a
            workstation and came back empty from GitHub's runners, so
            ShineMonitor is refusing the shared runner ranges, most likely
            rate-limiting. Production survives it because the alarm path falls
            back to authSource.

        Both skip. A wrong password does not look like either: it comes back
        with an error code in a body, so a real credential problem still fails.
        """
        # Skip on Windows - these tests require Unix environment
        if platform.system() == 'Windows':
            pytest.skip("Bash script tests require Unix environment (Linux/macOS). Run in GitHub Actions.")

        try:
            result = subprocess.run(
                ['bash', '-c', script_content],
                capture_output=True,
                text=True,
                timeout=timeout
            )
        except subprocess.TimeoutExpired:
            pytest.skip(
                f"ShineMonitor did not answer within {timeout}s; that is the "
                f"vendor's latency, not this repository's code")

        if result.returncode != 0 and "empty response" in (result.stderr or ""):
            pytest.skip(
                "ShineMonitor returned an empty body to this runner "
                "(authEmail refused). Reproduced as a runner-side refusal on "
                "2026-10-21: the same call succeeds from a workstation.")

        return result

    # Signatures of OUR code being broken, as opposed to the vendor being
    # unhelpful. Kept narrow on purpose: everything else is treated as the
    # vendor, and a real regression in the shell helpers shows up here.
    _SCRIPT_BROKEN = ("command not found", "No such file", "unbound variable",
                      "syntax error", "Permission denied", "cannot open")

    def _vendor_gave_nothing(self, result, what):
        """
        Skip for a vendor that answered with nothing; fail for our own bug.

        ShineMonitor has a third mood beyond a timeout and a refused auth: it
        exits 0 and hands back an empty or non-JSON body. Observed on
        2026-10-21 at 18:47 and 22:04, where the previous fix skipped
        test_check_monthly_script but test_query_plants_api still died on
        `json.loads("")` and test_query_month_energy_api on a pid grep that
        found nothing and tripped `pipefail`.
        """
        stderr = result.stderr or ""
        broken = [s for s in self._SCRIPT_BROKEN if s in stderr]
        if broken:
            pytest.fail(
                f"this is our script, not the vendor ({broken[0]}): {stderr}")
        pytest.skip(
            f"ShineMonitor returned no {what} to this runner on a zero exit; "
            f"that is the vendor's answer, not this repository's code")

    def parse_api_response(self, result):
        """Parse an API body, skipping when the vendor sent nothing usable."""
        body = (result.stdout or "").strip()
        if not body:
            self._vendor_gave_nothing(result, "response body")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            self._vendor_gave_nothing(result, "parseable JSON")

    def plant_id_or_skip(self, result):
        """
        A plant id, or a skip.

        The caller's script greps the pid out of queryPlants under
        `set -o pipefail`, so an empty body makes grep exit 1 and the whole
        pipeline non-zero with nothing on stderr to explain it.
        """
        pid = (result.stdout or "").strip()
        if result.returncode != 0 or not pid:
            self._vendor_gave_nothing(result, "plant id")
        return pid

    def test_auth_email_function(self, test_creds):
        """Test shinemonitor_auth_email authentication function"""
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        if shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}"; then
            echo "AUTH_SUCCESS"
            echo "TOKEN:${{SM_TOKEN:0:16}}"
            echo "SECRET:${{SM_SECRET:0:16}}"
            exit 0
        else
            echo "AUTH_FAILED"
            exit 1
        fi
        """

        result = self.run_bash_script(script)

        assert result.returncode == 0, f"Authentication failed: {result.stderr}"
        assert "AUTH_SUCCESS" in result.stdout
        assert "TOKEN:" in result.stdout
        assert "SECRET:" in result.stdout

    def test_query_plants_api(self, test_creds):
        """Test queryPlants API call"""
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        # Same fallback production uses (see check_device_alarms.sh): some
        # accounts need authSource, and authEmail is the method ShineMonitor
        # refuses from CI runners. Testing authEmail alone here asserted a
        # path the system deliberately does not rely on alone.
        if ! shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2; then
            shinemonitor_auth_source "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2
        fi

        response=$(shinemonitor_api_call "queryPlants" "")
        echo "$response"
        """

        result = self.run_bash_script(script)

        assert result.returncode == 0, f"API call failed: {result.stderr}"

        # Parse JSON response
        response = self.parse_api_response(result)
        assert response['err'] == 0, f"API returned error: {response.get('desc')}"
        assert 'dat' in response
        assert 'total' in response['dat']

    def test_query_month_energy_api(self, test_creds):
        """Test queryPlantEnergyMonth API call"""
        # First get a plant ID
        get_plant_script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        # Same fallback production uses (see check_device_alarms.sh): some
        # accounts need authSource, and authEmail is the method ShineMonitor
        # refuses from CI runners. Testing authEmail alone here asserted a
        # path the system deliberately does not rely on alone.
        if ! shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2; then
            shinemonitor_auth_source "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2
        fi

        plants=$(shinemonitor_api_call "queryPlants" "")
        echo "$plants" | grep -o '"pid":[0-9]*' | head -1 | grep -o '[0-9]*'
        """

        result = self.run_bash_script(get_plant_script)
        plant_id = self.plant_id_or_skip(result)

        # Now test monthly energy API
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        # Same fallback production uses (see check_device_alarms.sh): some
        # accounts need authSource, and authEmail is the method ShineMonitor
        # refuses from CI runners. Testing authEmail alone here asserted a
        # path the system deliberately does not rely on alone.
        if ! shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2; then
            shinemonitor_auth_source "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2
        fi

        response=$(shinemonitor_api_call "queryPlantEnergyMonth" "plantid={plant_id}&date=2026-01")
        echo "$response"
        """

        result = self.run_bash_script(script)

        assert result.returncode == 0, f"API call failed: {result.stderr}"

        response = self.parse_api_response(result)
        assert response['err'] == 0, f"API returned error: {response.get('desc')}"

    def test_query_year_energy_api(self, test_creds):
        """Test queryPlantEnergyYear API call"""
        # First get a plant ID
        get_plant_script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        # Same fallback production uses (see check_device_alarms.sh): some
        # accounts need authSource, and authEmail is the method ShineMonitor
        # refuses from CI runners. Testing authEmail alone here asserted a
        # path the system deliberately does not rely on alone.
        if ! shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2; then
            shinemonitor_auth_source "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2
        fi

        plants=$(shinemonitor_api_call "queryPlants" "")
        echo "$plants" | grep -o '"pid":[0-9]*' | head -1 | grep -o '[0-9]*'
        """

        result = self.run_bash_script(get_plant_script)
        plant_id = self.plant_id_or_skip(result)

        # Now test yearly energy API
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        # Same fallback production uses (see check_device_alarms.sh): some
        # accounts need authSource, and authEmail is the method ShineMonitor
        # refuses from CI runners. Testing authEmail alone here asserted a
        # path the system deliberately does not rely on alone.
        if ! shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2; then
            shinemonitor_auth_source "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2
        fi

        response=$(shinemonitor_api_call "queryPlantEnergyYear" "plantid={plant_id}&year=2026")
        echo "$response"
        """

        result = self.run_bash_script(script)

        assert result.returncode == 0, f"API call failed: {result.stderr}"

        response = self.parse_api_response(result)
        assert response['err'] == 0, f"API returned error: {response.get('desc')}"

    def test_query_plants_warning_api(self, test_creds):
        """Test webQueryPlantsWarning API call (device alarms)"""
        script = f"""
        set -euo pipefail
        SCRIPT_DIR="{SCRIPTS_DIR}"
        source "${{SCRIPT_DIR}}/shinemonitor_common.sh"

        # Same fallback production uses (see check_device_alarms.sh): some
        # accounts need authSource, and authEmail is the method ShineMonitor
        # refuses from CI runners. Testing authEmail alone here asserted a
        # path the system deliberately does not rely on alone.
        if ! shinemonitor_auth_email "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2; then
            shinemonitor_auth_source "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" >&2
        fi

        response=$(shinemonitor_api_call "webQueryPlantsWarning" "date=")
        echo "$response"
        """

        result = self.run_bash_script(script)

        assert result.returncode == 0, f"API call failed: {result.stderr}"

        response = self.parse_api_response(result)
        assert response['err'] == 0, f"API returned error: {response.get('desc')}"
        assert 'dat' in response

    def test_check_device_alarms_script(self, test_creds):
        """Test check_device_alarms.sh end-to-end"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            output_file = f.name

        try:
            script = f"""
            set -euo pipefail
            cd "{SCRIPTS_DIR}"
            bash check_device_alarms.sh "{test_creds['username']}" "{test_creds['password']}" "{test_creds['company_key']}" "{output_file}"
            """

            result = self.run_bash_script(script)

            assert result.returncode == 0, f"Script failed: {result.stderr}"
            assert Path(output_file).exists(), "Output file not created"

            # Verify JSON output
            with open(output_file) as f:
                data = json.load(f)

            assert data['err'] == 0, f"API error in output: {data.get('desc')}"
            assert 'dat' in data

        finally:
            Path(output_file).unlink(missing_ok=True)

    def test_check_monthly_script(self):
        """Test check_shinemonitor_monthly.sh"""
        script = f"""
        set -euo pipefail
        cd "{SCRIPTS_DIR}"
        bash check_shinemonitor_monthly.sh "{CREDS_FILE}" "2026-01" 2>&1 | grep -E "(AUTH OK|DONE)"
        """

        # Use 90-second timeout (processes 20+ accounts)
        result = self.run_bash_script(script, timeout=90)

        assert result.returncode == 0, f"Monthly script failed: {result.stderr}"
        assert "AUTH OK" in result.stdout or "DONE" in result.stdout

    def test_check_yearly_script(self):
        """Test check_shinemonitor_yearly.sh"""
        script = f"""
        set -euo pipefail
        cd "{SCRIPTS_DIR}"
        timeout 60 bash check_shinemonitor_yearly.sh "{CREDS_FILE}" "2026" 2>&1 | head -20 | grep -E "(AUTH OK|DONE)"
        """

        # Use 90-second timeout (processes 20+ accounts)
        result = self.run_bash_script(script, timeout=90)

        # Allow timeout (124) and SIGPIPE (141 from head closing pipe early)
        assert result.returncode in [0, 124, 141], f"Yearly script failed: {result.stderr}"
        assert "AUTH OK" in result.stdout or "DONE" in result.stdout


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])


class TestVendorConditionHandling:
    """
    The skip-or-fail decision, tested directly.

    It got this wrong twice in production. First version: a 90s timeout failed
    the build. Second version: a refused auth skipped, but an empty body on a
    zero exit still died on `json.loads("")` (21 Oct 18:47 and 22:04). Both
    times a red build said "something is broken" about a repository that was
    fine.

    These run everywhere - no bash, no network, no credentials - so the logic
    is checked on every machine rather than only when the vendor misbehaves.
    """

    def _result(self, stdout="", stderr="", returncode=0):
        return subprocess.CompletedProcess(
            args=["bash", "-c", "..."], returncode=returncode,
            stdout=stdout, stderr=stderr)

    def _outcome(self, fn, *args):
        """
        Report a helper's outcome as a plain string.

        Deliberately not `pytest.raises`. Skipped and Failed both derive from
        BaseException, so a test that expects a Failed and receives a Skipped
        does not go red - the skip propagates and the test quietly joins the
        skip count. Making the behaviour flip from fail to skip is exactly the
        regression worth catching, so the outcome is captured and asserted on
        instead of being allowed to escape.
        """
        try:
            fn(*args)
        except pytest.skip.Exception:
            return "skip"
        except pytest.fail.Exception:
            return "fail"
        return "returned"

    # --- the vendor answering with nothing ------------------------------

    def test_an_empty_body_skips(self):
        api = TestShineMonitorAPI()
        assert self._outcome(api.parse_api_response,
                             self._result(stdout="   \n")) == "skip"

    def test_a_non_json_body_skips(self):
        api = TestShineMonitorAPI()
        assert self._outcome(api.parse_api_response,
                             self._result(stdout="<html>502</html>")) == "skip"

    def test_a_missing_plant_id_skips(self):
        """`pipefail` makes the pid grep exit non-zero with a silent stderr."""
        api = TestShineMonitorAPI()
        assert self._outcome(api.plant_id_or_skip,
                             self._result(stdout="", returncode=1)) == "skip"

    # --- our own code being broken, which must NOT skip -----------------

    @pytest.mark.parametrize("stderr", [
        "shinemonitor_api_call: command not found",
        "bash: line 3: /x/shinemonitor_common.sh: No such file or directory",
        "bash: SM_TOKEN: unbound variable",
        "bash: -c: line 5: syntax error near unexpected token",
    ])
    def test_a_broken_script_fails_rather_than_skipping(self, stderr):
        """
        What makes the skips above safe. If the shell helpers were renamed or
        the file went missing, bash says so on stderr, and that is our bug -
        skipping it would hide a real regression behind a green build.
        """
        api = TestShineMonitorAPI()
        outcome = self._outcome(api.parse_api_response,
                                self._result(stdout="", stderr=stderr))
        assert outcome == "fail", (
            f"stderr {stderr!r} is our script breaking, so it must fail; "
            f"got {outcome!r}. A skip here would make a real regression "
            f"invisible behind a green build.")

    # --- the happy path still works -------------------------------------

    def test_a_good_body_is_returned(self):
        api = TestShineMonitorAPI()
        parsed = api.parse_api_response(
            self._result(stdout='{"err": 0, "dat": {"total": 3}}'))
        assert parsed["err"] == 0 and parsed["dat"]["total"] == 3

    def test_a_good_plant_id_is_returned(self):
        api = TestShineMonitorAPI()
        assert api.plant_id_or_skip(self._result(stdout="12345\n")) == "12345"

    # --- the call sites, not just the helper ----------------------------

    def _live_test_source(self):
        """Only the live-API tests: this class's own regexes would self-match."""
        return Path(__file__).read_text(encoding="utf-8").split(
            "class TestVendorConditionHandling")[0]

    def test_no_test_parses_the_body_itself(self):
        """
        The helpers are only worth having if every call site uses them. A
        reverted site reintroduces exactly the 21 Oct failure - json.loads("")
        on an empty body - and nothing else here would catch it, because the
        helper would still be correct and still be tested.
        """
        import re
        offenders = [
            f"line {n}: {line.strip()}"
            for n, line in enumerate(self._live_test_source().splitlines(), 1)
            if re.search(r"json\.loads\(\s*result\.stdout", line)
        ]
        assert not offenders, (
            "a test parses the API body directly instead of via "
            "parse_api_response():\n  " + "\n  ".join(offenders))

    def test_no_test_asserts_on_a_raw_plant_id(self):
        """The same for the pid path, which trips `pipefail` on an empty body."""
        import re
        offenders = [
            f"line {n}: {line.strip()}"
            for n, line in enumerate(self._live_test_source().splitlines(), 1)
            if re.search(r"get plant ID|No plant ID found", line)
        ]
        assert not offenders, (
            "a test asserts on the plant id directly instead of via "
            "plant_id_or_skip():\n  " + "\n  ".join(offenders))


class TestVendorConditionReproduced:
    """
    Force the vendor's bad answers instead of waiting for them.

    TestVendorConditionHandling checks the helpers against hand-built results.
    That leaves the premise untested: that an empty body really does surface
    the way the helpers assume. The premise came from reading a CI log, and
    reading a log is how the second fix came to be incomplete.

    So these run the real pipeline in real bash with `shinemonitor_api_call`
    stubbed to answer the way ShineMonitor did on 21 October - a zero exit and
    nothing in the body - and assert both the shape of the failure and that
    the helper skips on it. Verified shape: returncode 1, empty stdout, and an
    empty stderr, because `grep` matching nothing exits 1 under `pipefail`
    and says nothing about why.
    """

    BASH = shutil.which("bash")

    pytestmark = pytest.mark.skipif(
        BASH is None, reason="needs bash; present in CI and in Git Bash")

    def _run(self, body):
        """The real pid pipeline, against a stubbed vendor answer."""
        script = (
            "set -euo pipefail\n"
            f"shinemonitor_api_call() {{ printf '%s' '{body}'; }}\n"
            'plants=$(shinemonitor_api_call "queryPlants" "")\n'
            "echo \"$plants\" | grep -o '\"pid\":[0-9]*' | head -1 | grep -o '[0-9]*'\n"
        )
        return subprocess.run([self.BASH, "--noprofile", "--norc", "-c", script],
                              capture_output=True, text=True, timeout=60)

    def test_an_empty_body_fails_the_pipeline_silently(self):
        """
        The premise behind plant_id_or_skip, pinned. If bash or pipefail ever
        behaved differently here, the helper would be guarding a shape that no
        longer occurs and the real failure would come back unhandled.
        """
        result = self._run("")

        assert result.returncode != 0, (
            "an empty body no longer fails the pid pipeline; plant_id_or_skip "
            "is guarding a shape that does not happen any more")
        assert not result.stdout.strip(), f"unexpected stdout: {result.stdout!r}"
        assert not result.stderr.strip(), (
            f"stderr is no longer silent: {result.stderr!r}. If bash now "
            f"explains this, the helper could discriminate on it instead of "
            f"treating a quiet non-zero exit as the vendor.")

    def test_the_helper_skips_on_that_real_result(self):
        """End to end: the real failure, through the real helper."""
        result = self._run("")
        api = TestShineMonitorAPI()

        try:
            api.plant_id_or_skip(result)
        except pytest.skip.Exception:
            return
        except pytest.fail.Exception as exc:
            pytest.fail(f"the vendor's empty body was treated as our bug: {exc}")
        pytest.fail("plant_id_or_skip returned a plant id from an empty body")

    def test_a_real_body_still_yields_a_plant_id(self):
        """The counterpart, so the skip is not hiding a pipeline that never works."""
        result = self._run('{"dat":{"plant":[{"pid":4242,"name":"x"}]}}')

        assert result.returncode == 0, f"a good body failed: {result.stderr!r}"
        assert TestShineMonitorAPI().plant_id_or_skip(result) == "4242"

    def _run_plain_call(self, body):
        """An API call on its own, the way the query tests make it."""
        script = (
            "set -euo pipefail\n"
            f"shinemonitor_api_call() {{ printf '%s' '{body}'; }}\n"
            'shinemonitor_api_call "queryPlants" ""\n'
        )
        return subprocess.run([self.BASH, "--noprofile", "--norc", "-c", script],
                              capture_output=True, text=True, timeout=60)

    def test_an_empty_body_exits_zero(self):
        """
        The assumption that made the 21 October fix incomplete, pinned.

        That fix skipped only on a NON-zero exit carrying "empty response" on
        stderr. An empty body does neither: the call exits **0** and says
        nothing, so the test went on to parse "" and died on JSONDecodeError.
        Asserting the exit code here means nobody rebuilds the guard on the
        same wrong premise.
        """
        result = self._run_plain_call("")

        assert result.returncode == 0, (
            "an empty body now exits non-zero; a guard may discriminate on "
            "the exit code after all, which it could not before")
        assert not result.stdout.strip()
        assert not result.stderr.strip()

    def test_the_helper_skips_that_rather_than_parsing_it(self):
        result = self._run_plain_call("")
        api = TestShineMonitorAPI()

        try:
            api.parse_api_response(result)
        except pytest.skip.Exception:
            return
        except pytest.fail.Exception as exc:
            pytest.fail(f"the vendor's empty body was treated as our bug: {exc}")
        except json.JSONDecodeError:
            pytest.fail(
                "parse_api_response let JSONDecodeError escape - this is the "
                "21 October failure, unchanged")
        pytest.fail("parse_api_response returned something from an empty body")

    def test_a_good_body_still_parses(self):
        result = self._run_plain_call('{"err":0,"dat":{"total":7}}')
        assert TestShineMonitorAPI().parse_api_response(result)["dat"]["total"] == 7
