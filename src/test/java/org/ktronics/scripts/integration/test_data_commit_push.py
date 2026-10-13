"""
Behaviour the data-commit step must have (issue #18).

Three workflows end by committing what the run produced and pushing it:
ShineMonitor, DessMonitor and the BMS ingest. These tests do not assert on the
YAML text. They lift the step's real shell script out of the workflow file and
run it against a throwaway git remote, so they hold whatever shape the fix
takes. That includes moving the push logic into one shared script: the
throwaway repository carries a copy of the shell scripts in
`src/main/java/org/ktronics/scripts/`, so a step that calls one still runs.

Each one encodes a finding from the review of PR #28, or a behaviour that fix
must keep. The ones that matter most are about what a run is allowed to do to
`main` when it is not the only writer:

- `data/` and `alerts/` are regenerated every run. Losing a copy costs nothing.
- `state/` is cumulative (send counts, ignored flags). An older copy must never
  replace a newer one.
- A BMS reading arrives once. If it cannot be pushed, the run has to go red,
  because there is no "next run" that will fetch it again.
"""

import json
import os
import platform
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

_WORKFLOWS_REL = Path(".github/workflows")
_SCRIPTS_REL = Path("src/main/java/org/ktronics/scripts")

# workflow file, name of its commit step, the data file and the state file a
# run of that workflow writes.
STEPS = {
    "shinemonitor": {
        "file": "trigger-shinemonitor.yml",
        "step": "Commit updated data + alert state",
        "data": "data/test-plant-2026-10.csv",
        "state": "state/device_alarms_state.json",
    },
    "dessmonitor": {
        "file": "trigger-dessmonitor.yml",
        "step": "Commit DessMonitor data and state",
        "data": "data/dessmonitor-test-plant-2026-10.csv",
        "state": "state/dessmonitor_device_alarms_state.json",
    },
    "bms": {
        "file": "trigger-bms-ingest.yml",
        "step": "Commit reading + state",
        "data": "bms-module/data/test-device.csv",
        "state": "bms-module/state/bms_alerts_state.json",
    },
}
ALL = sorted(STEPS)
SCHEDULED = ["dessmonitor", "shinemonitor"]

GIT = shutil.which("git")
BASH = shutil.which("bash")


def _usable_bash():
    """True if `bash` can run the step against paths this process can see."""
    if not GIT or not BASH:
        return False
    if platform.system() != "Windows":
        return True
    # On Windows `bash` may be the WSL launcher, which sees a different
    # filesystem. Git Bash reports MINGW/MSYS and is fine.
    try:
        kernel = subprocess.run([BASH, "-c", "uname -s"], capture_output=True,
                                text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return kernel.startswith(("MINGW", "MSYS"))


pytestmark = pytest.mark.skipif(
    not _usable_bash(),
    reason="needs git and a bash that shares this filesystem (Linux, or Git Bash on Windows)",
)


def _workflows_dir():
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _WORKFLOWS_REL
        if candidate.is_dir():
            return candidate
    raise AssertionError(f"could not find {_WORKFLOWS_REL} above {__file__}")


def _step_script(key):
    """The `run:` script of the workflow's commit step, exactly as written."""
    spec = STEPS[key]
    workflow = yaml.safe_load(
        (_workflows_dir() / spec["file"]).read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == spec["step"]:
                return step["run"]
    raise AssertionError(
        f"{spec['file']} has no step named {spec['step']!r}. If the step was "
        "renamed or moved into a shared script, update STEPS in this file.")


def _state_text(send_count, ignored):
    return json.dumps(
        {"99001:SN-TEST:W-1": {"send_count": send_count, "ignored": ignored}},
        indent=2) + "\n"


class Sandbox:
    """A bare `origin`, plus the clones that stand in for workflow runs."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.origin = tmp_path / "origin.git"
        self.empty_config = tmp_path / "empty.gitconfig"
        self.empty_config.write_text("", encoding="utf-8")
        self.shims = tmp_path / "shims"
        self.shims.mkdir()
        self.sleep_log = tmp_path / "sleep.log"
        self._write_shims()

        self.git(tmp_path, "init", "--bare", "-b", "main", str(self.origin))
        seed = self.clone("seed", depth=None)
        (seed / "README.md").write_text("fleet monitor\n", encoding="utf-8")
        # A step may call a shared script by its path in the repository.
        real_scripts = _workflows_dir().parents[1] / _SCRIPTS_REL
        for script in sorted(real_scripts.glob("*.sh")):
            (seed / _SCRIPTS_REL).mkdir(parents=True, exist_ok=True)
            shutil.copy(script, seed / _SCRIPTS_REL / script.name)
        for spec in STEPS.values():
            self.write(seed, spec["data"], "date,value\n2026-10-01,1.0\n")
            self.write(seed, spec["state"], _state_text(1, False))
        self.commit(seed, "seed")
        self.git(seed, "push", "-q", "origin", "HEAD:main")

    # -- plumbing ---------------------------------------------------------

    def env(self, extra=None):
        env = dict(os.environ)
        env.update({
            # Nothing from this machine's git setup may leak into the test.
            "GIT_CONFIG_GLOBAL": str(self.empty_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
        })
        env.update(extra or {})
        return env

    def git(self, cwd, *args, check=True):
        result = subprocess.run([GIT, *args], cwd=str(cwd), env=self.env(),
                                capture_output=True, text=True, timeout=120)
        if check and result.returncode != 0:
            raise AssertionError(
                f"test setup: git {' '.join(args)} failed\n{result.stdout}{result.stderr}")
        return result.stdout.strip()

    def clone(self, name, branch="main", depth=1):
        """A checkout the way actions/checkout makes one: one branch, shallow."""
        target = self.tmp / name
        args = ["clone", "-q"]
        if depth:
            args += ["--depth", str(depth), "--branch", branch]
        self.git(self.tmp, *args, self.origin.as_uri(), str(target))
        return target

    @staticmethod
    def write(repo, rel, text):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    @staticmethod
    def append(repo, rel, text):
        with open(repo / rel, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def commit(self, repo, message):
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", message)

    def _write_shims(self):
        # `sleep` returns at once and leaves a record, so a retry loop that
        # waits can be tested without the test waiting too.
        self._shim("sleep", '#!/usr/bin/env bash\necho "$*" >> "$KT_SLEEP_LOG"\n')
        # `git` passes everything through, except that it can fail the first
        # fetch (a network blip) or let a rival land just before the first
        # push (a lost race).
        self._shim("git", """#!/usr/bin/env bash
if [ "${1:-}" = "fetch" ] && [ -n "${KT_FAIL_FIRST_FETCH:-}" ] && [ ! -e "$KT_FAIL_FIRST_FETCH" ]; then
  : > "$KT_FAIL_FIRST_FETCH"
  echo "fatal: unable to access origin: simulated network failure" >&2
  exit 1
fi
if [ "${1:-}" = "push" ] && [ -n "${KT_RACE_MARKER:-}" ] && [ ! -e "$KT_RACE_MARKER" ]; then
  : > "$KT_RACE_MARKER"
  "$KT_REAL_GIT" -C "$KT_RIVAL_CLONE" push -q origin HEAD:main
fi
exec "$KT_REAL_GIT" "$@"
""")

    def _shim(self, name, text):
        path = self.shims / name
        path.write_text(text, encoding="utf-8", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # -- the world outside the run ---------------------------------------

    def land_on_main(self, files, message, name="rival"):
        """Another run pushes to main while ours is still working."""
        rival = self.clone(name)
        for rel, text in files.items():
            self.write(rival, rel, text)
        self.commit(rival, message)
        self.git(rival, "push", "-q", "origin", "HEAD:main")
        return rival

    def reject_every_push(self):
        hook = self.origin / "hooks" / "pre-receive"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\necho 'push declined by policy' >&2\nexit 1\n",
                        encoding="utf-8", newline="\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # -- reading origin ---------------------------------------------------

    def sha(self, branch="main"):
        return self.git(self.tmp, "--git-dir", str(self.origin), "rev-parse", branch)

    def on_origin(self, rel, branch="main"):
        return self.git(self.tmp, "--git-dir", str(self.origin), "show", f"{branch}:{rel}")

    def slept(self):
        return self.sleep_log.exists() and self.sleep_log.read_text().strip() != ""

    # -- running the step -------------------------------------------------

    def run_step(self, key, work, ref_name="main", extra_env=None):
        script = self.tmp / f"step-{key}.sh"
        script.write_text(_step_script(key), encoding="utf-8", newline="\n")
        env = self.env({
            "GITHUB_REF_NAME": ref_name,
            "KT_REAL_GIT": GIT,
            "KT_SLEEP_LOG": str(self.sleep_log),
            "PATH": str(self.shims) + os.pathsep + os.environ.get("PATH", ""),
        })
        # The step sets its own committer; ours must not mask a missing one.
        for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                     "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            env.pop(name, None)
        env.update(extra_env or {})
        # Every one of these steps declares `shell: bash`, which GitHub runs as:
        return subprocess.run(
            [BASH, "--noprofile", "--norc", "-eo", "pipefail", str(script)],
            cwd=str(work), env=env, capture_output=True, text=True, timeout=180)


@pytest.fixture
def box(tmp_path):
    return Sandbox(tmp_path)


def _log(result):
    return f"\n--- step exit {result.returncode} ---\n{result.stdout}{result.stderr}"


# --- what already works, and must keep working ---------------------------


@pytest.mark.parametrize("key", ALL)
def test_a_run_with_no_competition_publishes_its_commit(box, key):
    work = box.clone("work")
    box.append(work, STEPS[key]["data"], "2026-10-02,2.0\n")

    result = box.run_step(key, work)

    assert result.returncode == 0, _log(result)
    assert "2026-10-02,2.0" in box.on_origin(STEPS[key]["data"]), _log(result)


@pytest.mark.parametrize("key", ALL)
def test_a_lost_race_is_retried_and_the_data_lands(box, key):
    """
    The retry path itself: another run lands on main between our rebase and
    our push. The first push is refused; the step must fetch, rebase and push
    again, and end with both commits on main.

    This is the case issue #18 is about, run for real rather than described.
    """
    work = box.clone("work")
    box.append(work, STEPS[key]["data"], "2026-10-02,2.0\n")

    rival = box.clone("rival")
    box.write(rival, "README.md", "fleet monitor\nsomething unrelated\n")
    box.commit(rival, "rival run lands first")

    result = box.run_step(key, work, extra_env={
        "KT_RACE_MARKER": str(box.tmp / "race.marker"),
        "KT_RIVAL_CLONE": str(rival),
    })

    assert result.returncode == 0, _log(result)
    assert "2026-10-02,2.0" in box.on_origin(STEPS[key]["data"]), _log(result)
    assert "something unrelated" in box.on_origin("README.md"), (
        "the rival's commit must survive our retry" + _log(result))


@pytest.mark.parametrize("key", ALL)
def test_a_transient_fetch_failure_is_retried(box, key):
    work = box.clone("work")
    box.append(work, STEPS[key]["data"], "2026-10-02,2.0\n")

    result = box.run_step(key, work, extra_env={
        "KT_FAIL_FIRST_FETCH": str(box.tmp / "fetch.marker")})

    assert result.returncode == 0, _log(result)
    assert "2026-10-02,2.0" in box.on_origin(STEPS[key]["data"]), _log(result)


# --- findings from the review of PR #28 ----------------------------------


@pytest.mark.parametrize("key", ALL)
def test_retries_pause_between_attempts(box, key):
    """
    A retry that does not wait is not a retry. Five attempts with no pause are
    over in a second or two, which is shorter than the network blip or the
    rival push they are meant to outlast.
    """
    work = box.clone("work")
    box.append(work, STEPS[key]["data"], "2026-10-02,2.0\n")

    result = box.run_step(key, work, extra_env={
        "KT_FAIL_FIRST_FETCH": str(box.tmp / "fetch.marker")})

    assert box.slept(), (
        "the step retried after a failed fetch without sleeping first" + _log(result))


@pytest.mark.parametrize("key", ALL)
def test_a_manual_run_from_a_branch_never_writes_to_main(box, key):
    """
    "Run workflow" can be started from any branch, and that is the obvious way
    to try out a change to a workflow. Such a run must publish to the branch it
    was started from. If the target is hardcoded to `main`, the run rebases the
    branch onto main and pushes it there: unreviewed commits on the live
    branch, with no pull request.
    """
    feature = box.clone("feature-author")
    box.git(feature, "checkout", "-q", "-b", "feature")
    box.write(feature, "src/unreviewed.py", "print('not reviewed yet')\n")
    box.commit(feature, "WIP: unreviewed feature work")
    box.git(feature, "push", "-q", "origin", "HEAD:feature")
    main_before = box.sha("main")

    work = box.clone("work", branch="feature")
    box.append(work, STEPS[key]["data"], "2026-10-02,2.0\n")

    result = box.run_step(key, work, ref_name="feature")

    assert box.sha("main") == main_before, (
        "a run started from `feature` moved `main`" + _log(result))


@pytest.mark.parametrize("key", SCHEDULED)
def test_a_stale_run_does_not_roll_back_state_on_main(box, key):
    """
    `state/` is not regenerated. It counts sends and remembers what has been
    ignored. A run that started from an older commit (a re-run of yesterday's
    job, or two runs overlapping) holds an older copy; if its copy wins a
    conflict, an alarm already sent three times and ignored drops back to a
    low count and the customer is mailed all over again.

    Dropping this run's copy is fine. Replacing main's is not.
    """
    state = STEPS[key]["state"]
    work = box.clone("work")                       # sees send_count 1
    box.land_on_main({state: _state_text(3, True)},
                     "a newer run records the third send")
    box.write(work, state, _state_text(2, False))  # our older view moves it to 2

    result = box.run_step(key, work)

    on_main = json.loads(box.on_origin(state))["99001:SN-TEST:W-1"]
    assert on_main == {"send_count": 3, "ignored": True}, (
        "main's newer state was replaced by a stale run's copy" + _log(result))


@pytest.mark.parametrize("key", SCHEDULED)
def test_a_push_refused_every_time_fails_the_step(box, key):
    """
    Losing a race is temporary: the next attempt wins. A push that is refused
    every single time is not a race. It is lost permission, a branch rule or an
    outage, and it will still be there on the next run. Ending green hides it,
    and data silently stops reaching main. That is how the August outage ran
    for a month.
    """
    box.reject_every_push()
    work = box.clone("work")
    box.append(work, STEPS[key]["data"], "2026-10-02,2.0\n")

    result = box.run_step(key, work)

    assert result.returncode != 0, (
        "every push was refused and the step still reported success" + _log(result))


def test_a_stale_bms_run_does_not_delete_readings_already_on_main(box):
    """
    The BMS file only ever gains rows, and each row is a reading the device
    sent once. When our run started from an older commit, main already holds a
    row we have not seen. Resolving that conflict by keeping one side deletes
    readings.

    Either both rows end up on main, or the run fails and main keeps its own.
    """
    data = STEPS["bms"]["data"]
    work = box.clone("work")
    box.land_on_main({data: "date,value\n2026-10-01,1.0\n2026-10-02,52.1\n"},
                     "an earlier reading lands first")
    box.append(work, data, "2026-10-02,52.4\n")

    result = box.run_step("bms", work)

    on_main = box.on_origin(data)
    assert "2026-10-02,52.1" in on_main, (
        "a reading that was already on main has been deleted" + _log(result))
    if result.returncode == 0:
        assert "2026-10-02,52.4" in on_main, (
            "the step reported success but this run's reading is not on main" + _log(result))


def test_bms_ingest_goes_red_when_the_reading_cannot_be_pushed(box):
    """
    "The data will land on the next run" is true for a scheduled workflow that
    fetches from a vendor API. It is not true here: this workflow has no
    schedule, and the reading exists only in this run. If it cannot be pushed,
    the run must fail so somebody knows a reading was dropped.
    """
    box.reject_every_push()
    work = box.clone("work")
    box.append(work, STEPS["bms"]["data"], "2026-10-02,52.4\n")

    result = box.run_step("bms", work)

    assert result.returncode != 0, (
        "the reading could not be pushed and the step still reported success" + _log(result))

# permission check update