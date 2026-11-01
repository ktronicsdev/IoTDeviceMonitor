"""
A fetch must never replace committed data with nothing.

Every fetch script used to do this:

    out="data/<plant>-<YYYY-MM>.csv"
    : > "$out"                      # truncate FIRST
    echo "date,kwh" >> "$out"
    ... parse the API response ...
    if no rows: echo "(No daily rows returned)"    # file stays empty

The file is emptied before anyone knows whether the API gave us anything, and
the empty file is then committed. Read off main, 20-22 October:

    crdesilva-home-2026-10.csv   31 rows -> 0 -> 31 -> 0 -> 0 -> 31

Three symptoms came from that one line, which is why it took three attempts to
stop the mail:

  * connectivity.py reads a header-only file as "no data rows at all - the
    portal has nothing for this plant", so a plant that reported that same
    morning is mailed as a dead link;
  * those plants flip between dead and reporting every run, so clear_recovered
    wipes their state each time and the 3-send rule restarts at #1 for ever;
  * plants entering and leaving the dead-link set move the UC9 content hash,
    which is what kept defeating the weekly all-clear gate.

The rule these tests pin: **a run that parses no rows leaves the committed
file alone.** Writing is staged and swapped in only on success.

Filed as #33. On 2026-11-01 the rule was widened to **a failed call or an
all-zero answer never replaces a real figure**; see the fake-vendor tests below.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS_REL = Path("src/main/java/org/ktronics/scripts")

# Every fetch script, and the shape of the file each one owns.
FETCHERS = [
    "check_shinemonitor_monthly.sh",
    "check_shinemonitor_yearly.sh",
    "check_dessmonitor_monthly.sh",
    "check_dessmonitor_yearly.sh",
]


def _repo_root():
    """Walk up to the repo root. Counting .parent lands outside the tree."""
    for parent in Path(__file__).resolve().parents:
        if (parent / SCRIPTS_REL).is_dir():
            return parent
    raise AssertionError(f"could not find {SCRIPTS_REL} above {__file__}")


ROOT = _repo_root()
SCRIPTS = ROOT / SCRIPTS_REL


# --- the shape of the code, checkable without running anything -----------


@pytest.mark.parametrize("name", FETCHERS)
def test_no_fetch_truncates_the_committed_file(name):
    """
    `: > "$out"` is only safe when `$out` is a staging file. The bug was that
    it pointed at `data/...csv` - the committed one - so truncation happened
    before the response was known to be good.
    """
    text = (SCRIPTS / name).read_text(encoding="utf-8")

    # Find what `out` is assigned, anywhere in the script.
    assignments = re.findall(r'^\s*out=(.+)$', text, re.M)
    assert assignments, f"{name}: no `out=` assignment found; has it been renamed?"

    committed = [a.strip() for a in assignments if "data/" in a]
    assert not committed, (
        f"{name} points $out straight at a committed file and then truncates "
        f"it:\n  out={committed[0]}\n\nStage it (`out=$(mktemp)`) and `mv` it "
        f"onto the real path only once rows have been parsed. See #33."
    )


@pytest.mark.parametrize("name", FETCHERS)
def test_every_fetch_keeps_the_old_file_when_nothing_came_back(name):
    """
    The empty case must say so and keep what is there. Silence here is how the
    bug survived: the old message was '(No daily rows returned)', which the
    workflow's grep filtered out before anyone could read it.
    """
    text = (SCRIPTS / name).read_text(encoding="utf-8")

    assert "keeping existing" in text, (
        f"{name} has no branch that preserves the committed file when the API "
        f"returns nothing")
    assert re.search(r"WARNING[^\n]*keeping existing", text), (
        f"{name} preserves the file but does not say WARNING, so the "
        f"workflow's grep will swallow the only evidence it happened (#32)")


@pytest.mark.parametrize("name", FETCHERS)
def test_the_swap_happens_only_on_the_success_path(name):
    """`mv` must be reachable only where rows were parsed."""
    text = (SCRIPTS / name).read_text(encoding="utf-8")
    assert re.search(r'mv "\$out" "\$final_out"', text), (
        f"{name} never swaps the staged file into place")


def test_the_workflow_lets_the_warning_through():
    """
    The guard is worthless if nobody sees it. The fetch steps pipe through a
    grep allow-list, and `AUTH FAIL` has been invisible that way for months.
    """
    wf = (ROOT / ".github" / "workflows" / "trigger-shinemonitor.yml").read_text(
        encoding="utf-8")
    filters = re.findall(r'grep -E "\(([^"]+)\)"', wf)
    fetch_filters = [f for f in filters if "CSV" in f]
    assert fetch_filters, "no fetch-step grep filter found; has the step changed?"

    for f in fetch_filters:
        assert "WARNING" in f, (
            f"a fetch step filters its output through `grep -E \"({f})\"`, "
            f"which drops the WARNING line that says data was preserved")


# --- running the real thing ----------------------------------------------


BASH = shutil.which("bash")


@pytest.mark.skipif(BASH is None, reason="needs bash; present in CI and Git Bash")
def test_an_empty_response_leaves_the_file_untouched(tmp_path):
    """
    The bug reproduced end to end, in bash, with the real idiom.

    `old` is the fix; `buggy` is what the scripts did before. Running both
    against an empty response shows the difference the guard makes, so this
    fails loudly if anyone reinstates the simpler form.
    """
    data = tmp_path / "data"
    data.mkdir()
    committed = data / "plant-2026-10.csv"
    committed.write_text("date,kwh\n2026-10-01,5.5\n2026-10-02,6.5\n",
                         encoding="utf-8")

    fixed = (
        'set -euo pipefail\n'
        'final_out="data/plant-2026-10.csv"\n'
        'out="$(mktemp)"\n'
        ': > "$out"\n'
        'echo "date,kwh" >> "$out"\n'
        'ROWS=()\n'                       # the vendor returned nothing
        'if [[ "${#ROWS[@]}" -eq 0 ]]; then\n'
        '  echo "WARNING: no daily rows returned - keeping existing $final_out"\n'
        '  rm -f "$out"\n'
        'else\n'
        '  printf "%s\\n" "${ROWS[@]}" >> "$out"\n'
        '  mv "$out" "$final_out"\n'
        'fi\n'
    )

    result = subprocess.run([BASH, "--noprofile", "--norc", "-c", fixed],
                            cwd=str(tmp_path), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60)

    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stdout, "the preserved case said nothing"

    rows = [l for l in committed.read_text(encoding="utf-8").splitlines()[1:] if l]
    assert len(rows) == 2, (
        f"the committed file lost its data on an empty response: {rows!r}. "
        f"This is #33 - a header-only CSV gets committed and connectivity.py "
        f"then reports the plant as a dead link.")


@pytest.mark.skipif(BASH is None, reason="needs bash; present in CI and Git Bash")
def test_the_old_idiom_really_did_destroy_it(tmp_path):
    """
    The counterpart, so the test above is known to be testing something. If
    this ever stops destroying the file, the premise behind #33 has changed
    and the guard may no longer be needed.
    """
    data = tmp_path / "data"
    data.mkdir()
    committed = data / "plant-2026-10.csv"
    committed.write_text("date,kwh\n2026-10-01,5.5\n2026-10-02,6.5\n",
                         encoding="utf-8")

    buggy = (
        'set -euo pipefail\n'
        'out="data/plant-2026-10.csv"\n'
        ': > "$out"\n'
        'echo "date,kwh" >> "$out"\n'
        'ROWS=()\n'
        'if [[ "${#ROWS[@]}" -eq 0 ]]; then\n'
        '  echo "(No daily rows returned)"\n'
        'else\n'
        '  printf "%s\\n" "${ROWS[@]}" >> "$out"\n'
        'fi\n'
    )

    subprocess.run([BASH, "--noprofile", "--norc", "-c", buggy],
                   cwd=str(tmp_path), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60, check=True)

    rows = [l for l in committed.read_text(encoding="utf-8").splitlines()[1:] if l]
    assert rows == [], (
        "the old idiom no longer empties the file - if that is genuinely so, "
        "re-read #33 before relaxing the guard")


# --- the real scripts against a fake vendor (2026-11-01) ------------------
#
# Everything above checks the shape of the code or a copy of its idiom, and all
# of it stayed green while real data was being destroyed two other ways:
#
#   * Troy's account holds two plants whose names sanitise to one filename. The
#     dead one is listed first and answers with a month of 0.0000. When the live
#     one's call then failed, "keeping existing" kept the dead plant's zeros, so
#     a healthy plant was mailed DATA LINK DOWN 9 times in 12 days.
#   * The yearly fetch wrote kwh=0 for any month whose call failed, and its guard
#     only caught all twelve being zero. On 2026-11-01 that left 54 months across
#     17 plants reading 0, in the totals customers are sent every Sunday.
#
# So these run the real scripts. `curl` is replaced by a stub that answers from
# files on disk, and a call with no file gets silence, which is exactly what
# ShineMonitor gives a GitHub runner when it hangs.

DEAD_PID = "1240025"
LIVE_PID = "1282286"

# Pure bash on purpose: every extra process here is multiplied by 12 months and
# 2 tries per test, and forking is slow under Git Bash on Windows.
FAKE_CURL = r"""#!/usr/bin/env bash
url=""
for arg in "$@"; do case "$arg" in http*) url="$arg" ;; esac; done
query="&${url#*\?}"
param() {
  REPLY=""
  case "$query" in *"&$1="*) REPLY="${query#*&$1=}"; REPLY="${REPLY%%&*}" ;; esac
}
param action; action="$REPLY"
param plantid; plantid="$REPLY"
param date; day="$REPLY"
for name in "$action-$plantid-$day" "$action-$plantid" "$action"; do
  if [ -f "$FAKE_API_DIR/$name" ]; then
    IFS= read -r -d '' body < "$FAKE_API_DIR/$name"
    printf '%s' "$body"
    exit 0
  fi
done
exit 0
"""

CREDENTIALS = """{
  "company_key": "fake-company",
  "accounts": [
    {
      "label": "Troy",
      "username": "fake-user",
      "password": "fake-password"
    }
  ]
}
"""


class FakeVendor:
    """A working directory, a stub `curl` and the answers it gives."""

    def __init__(self, tmp_path):
        self.root = tmp_path
        self.data = tmp_path / "data"
        self.data.mkdir()
        self.api = tmp_path / "api"
        self.api.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        curl = self.bin / "curl"
        curl.write_bytes(FAKE_CURL.encode("utf-8"))
        curl.chmod(0o755)
        self.credentials = tmp_path / "credentials.json"
        self.credentials.write_text(CREDENTIALS, encoding="utf-8", newline="\n")
        self.answer("authEmail", '{"err":0,"dat":{"token":"t","secret":"s"}}')

    def answer(self, name, body):
        (self.api / name).write_text(body, encoding="utf-8", newline="\n")

    def plants(self, *pids):
        """The account's plants, in the order the API lists them."""
        names = {DEAD_PID: "Troy Hybrid 3kw", LIVE_PID: "TROY HYBRID 3kw"}
        listed = ",".join(f'{{"pid":{p},"name":"{names[p]}"}}' for p in pids)
        self.answer("queryPlants",
                    f'{{"err":0,"dat":{{"total":{len(pids)},"plant":[{listed}]}}}}')

    def days(self, pid, month, values):
        """queryPlantEnergyMonthPerDay answers with these daily readings."""
        rows = ",".join(f'{{"val":"{v}","ts":"{month}-{i:02d} 00:00:00"}}'
                        for i, v in enumerate(values, start=1))
        self.answer(f"queryPlantEnergyMonthPerDay-{pid}-{month}",
                    f'{{"err":0,"dat":{{"perday":[{rows}]}}}}')

    def month_total(self, pid, month, kwh):
        self.answer(f"queryPlantEnergyMonth-{pid}-{month}",
                    f'{{"err":0,"dat":{{"energy":"{kwh}"}}}}')

    def month_silent(self, pid, month):
        """This one call comes back empty, as a hang does."""
        self.answer(f"queryPlantEnergyMonth-{pid}-{month}", "")

    def months_default_to_no_data(self):
        """Any month not answered otherwise gets err=12, as future months do."""
        self.answer("queryPlantEnergyMonth", '{"err":12,"desc":"ERR_NO_RECORD"}')

    def write_csv(self, name, header, rows):
        (self.data / name).write_text(
            "\n".join([header] + [f"{k},{v}" for k, v in rows]) + "\n",
            encoding="utf-8", newline="\n")

    def read_csv(self, name):
        lines = (self.data / name).read_text(encoding="utf-8").splitlines()[1:]
        return dict(line.split(",", 1) for line in lines if line)

    def run(self, script, period):
        env = dict(os.environ,
                   FAKE_API_DIR=str(self.api),
                   SM_RETRY_PAUSE_SECS="0",
                   PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}")
        result = subprocess.run(
            [BASH, "--noprofile", "--norc", (SCRIPTS / script).as_posix(),
             self.credentials.as_posix(), period],
            cwd=str(self.root), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout


needs_bash = pytest.mark.skipif(BASH is None,
                                reason="needs bash; present in CI and Git Bash")

MONTH = "2026-10"
MONTH_CSV = f"troy-troy-hybrid-3kw-{MONTH}.csv"
REAL_DAYS = [("2026-10-01", "14.2000"), ("2026-10-02", "13.7000"),
             ("2026-10-03", "16.2000")]
ZERO_DAYS = ["0.0000", "0.0000", "0.0000"]

YEAR = "2026"
YEAR_CSV = f"troy-troy-hybrid-3kw-{YEAR}.csv"
ALL_MONTHS = [f"{YEAR}-{m:02d}" for m in range(1, 13)]
REAL_YEAR = {m: f"{300 + i}.5000" for i, m in enumerate(ALL_MONTHS[:10])}
REAL_YEAR.update({"2026-11": "0", "2026-12": "0"})


def _year_on_disk(vendor):
    vendor.write_csv(YEAR_CSV, "month,kwh", list(REAL_YEAR.items()))


# ---- monthly: a month of zeros must not replace real readings ------------


@needs_bash
def test_dead_twin_cannot_wipe_a_month_when_the_live_plant_is_silent(tmp_path):
    """The Troy run itself, 2026-10-31 17:56: dead plant answers, live one hangs."""
    vendor = FakeVendor(tmp_path)
    vendor.plants(DEAD_PID, LIVE_PID)
    vendor.write_csv(MONTH_CSV, "date,kwh", REAL_DAYS)
    vendor.days(DEAD_PID, MONTH, ZERO_DAYS)

    output = vendor.run("check_shinemonitor_monthly.sh", MONTH)

    assert vendor.read_csv(MONTH_CSV) == dict(REAL_DAYS), (
        "a month of real readings was replaced by a dead plant's zeros, because "
        "two plants share this filename. connectivity.py then reports the "
        "healthy plant as DATA LINK DOWN.")
    assert re.search(r"WARNING[^\n]*keeping existing", output), (
        "the kept month said nothing, so the workflow's grep shows no trace of it")


@needs_bash
def test_live_plant_still_replaces_the_month_when_it_answers(tmp_path):
    """The guard must not freeze the file: new readings have to land."""
    vendor = FakeVendor(tmp_path)
    vendor.plants(DEAD_PID, LIVE_PID)
    vendor.write_csv(MONTH_CSV, "date,kwh", REAL_DAYS)
    vendor.days(DEAD_PID, MONTH, ZERO_DAYS)
    vendor.days(LIVE_PID, MONTH, ["14.2000", "13.7000", "16.2000", "12.0000"])

    vendor.run("check_shinemonitor_monthly.sh", MONTH)

    rows = vendor.read_csv(MONTH_CSV)
    assert rows.get("2026-10-04") == "12.0000", (
        f"the live plant answered with a new day and it was not written: {rows}")


@needs_bash
def test_order_of_the_twins_does_not_matter(tmp_path):
    """Nothing promises the API lists the dead plant first."""
    vendor = FakeVendor(tmp_path)
    vendor.plants(LIVE_PID, DEAD_PID)
    vendor.days(LIVE_PID, MONTH, ["14.2000", "13.7000"])
    vendor.days(DEAD_PID, MONTH, ZERO_DAYS)

    vendor.run("check_shinemonitor_monthly.sh", MONTH)

    assert vendor.read_csv(MONTH_CSV).get("2026-10-01") == "14.2000", (
        "the dead plant listed second wiped what the live plant had just written")


@needs_bash
def test_a_real_outage_inside_a_month_is_still_recorded(tmp_path):
    """Only a wholly zero month is suspect. Zero days among real ones are data."""
    vendor = FakeVendor(tmp_path)
    vendor.plants(LIVE_PID)
    vendor.write_csv(MONTH_CSV, "date,kwh", REAL_DAYS)
    vendor.days(LIVE_PID, MONTH, ["14.2000", "0.0000", "0.0000"])

    vendor.run("check_shinemonitor_monthly.sh", MONTH)

    assert vendor.read_csv(MONTH_CSV)["2026-10-02"] == "0.0000", (
        "a genuine zero day was discarded; the guard has become too eager")


@needs_bash
def test_a_plant_with_no_history_still_gets_its_zero_month(tmp_path):
    """
    Nothing to protect, so write it. connectivity.py needs these rows to tell
    "rows arrive but read zero" from "no rows at all".
    """
    vendor = FakeVendor(tmp_path)
    vendor.plants(DEAD_PID)
    vendor.days(DEAD_PID, MONTH, ZERO_DAYS)

    vendor.run("check_shinemonitor_monthly.sh", MONTH)

    assert vendor.read_csv(MONTH_CSV) == {
        "2026-10-01": "0.0000", "2026-10-02": "0.0000", "2026-10-03": "0.0000"}


@needs_bash
def test_a_silent_vendor_leaves_the_month_alone(tmp_path):
    """#33, against the real script rather than a copy of its idiom."""
    vendor = FakeVendor(tmp_path)
    vendor.plants(LIVE_PID)
    vendor.write_csv(MONTH_CSV, "date,kwh", REAL_DAYS)

    output = vendor.run("check_shinemonitor_monthly.sh", MONTH)

    assert vendor.read_csv(MONTH_CSV) == dict(REAL_DAYS)
    assert "WARNING" in output


# ---- yearly: a failed call must not zero the month it failed on ----------


@needs_bash
def test_one_failed_call_keeps_that_months_figure(tmp_path):
    """
    The fleet-wide bug. Troy's 2026-09 read 349.5, then 0, then 349.5, depending
    only on whether that one call came back.
    """
    vendor = FakeVendor(tmp_path)
    vendor.plants(LIVE_PID)
    _year_on_disk(vendor)
    for month in ALL_MONTHS:
        vendor.month_total(LIVE_PID, month, REAL_YEAR[month])
    vendor.month_silent(LIVE_PID, "2026-09")

    output = vendor.run("check_shinemonitor_yearly.sh", YEAR)

    assert vendor.read_csv(YEAR_CSV)["2026-09"] == REAL_YEAR["2026-09"], (
        "one failed call zeroed a month of real production. The weekly report "
        "sums these rows, so a customer's yearly total drops by that month.")
    assert re.search(r"WARNING[^\n]*2026-09", output), (
        "the kept month is not named in a WARNING, so the log hides that the "
        "vendor failed to answer for it")


@needs_bash
def test_new_figures_still_replace_old_ones(tmp_path):
    """The current month grows all month; keeping a figure must not freeze it."""
    vendor = FakeVendor(tmp_path)
    vendor.plants(LIVE_PID)
    _year_on_disk(vendor)
    vendor.months_default_to_no_data()
    vendor.month_total(LIVE_PID, "2026-10", "341.1000")

    vendor.run("check_shinemonitor_yearly.sh", YEAR)

    assert vendor.read_csv(YEAR_CSV)["2026-10"] == "341.1000"


@needs_bash
def test_a_zeroed_month_heals_as_soon_as_its_call_succeeds(tmp_path):
    """What repairs the rows the old code already damaged."""
    vendor = FakeVendor(tmp_path)
    vendor.plants(LIVE_PID)
    damaged = dict(REAL_YEAR, **{"2026-09": "0"})
    vendor.write_csv(YEAR_CSV, "month,kwh", list(damaged.items()))
    vendor.months_default_to_no_data()
    vendor.month_total(LIVE_PID, "2026-09", "349.5000")

    vendor.run("check_shinemonitor_yearly.sh", YEAR)

    assert vendor.read_csv(YEAR_CSV)["2026-09"] == "349.5000"


@needs_bash
def test_no_data_answer_cannot_erase_a_figure(tmp_path):
    """
    err=12 is what the dead twin says for every month. It is also the path every
    silent call takes, so this covers a vendor that answers nothing at all.
    """
    vendor = FakeVendor(tmp_path)
    vendor.plants(DEAD_PID)
    _year_on_disk(vendor)
    vendor.months_default_to_no_data()

    vendor.run("check_shinemonitor_yearly.sh", YEAR)

    assert vendor.read_csv(YEAR_CSV) == REAL_YEAR, (
        "the dead twin's 'no data' answers changed the live plant's year")


@needs_bash
def test_new_year_file_keeps_its_twelve_rows(tmp_path):
    """Months with no figure anywhere stay 0, so the file keeps its shape."""
    vendor = FakeVendor(tmp_path)
    vendor.plants(LIVE_PID)
    vendor.months_default_to_no_data()
    vendor.month_total(LIVE_PID, "2026-01", "333.2000")

    output = vendor.run("check_shinemonitor_yearly.sh", YEAR)

    rows = vendor.read_csv(YEAR_CSV)
    assert list(rows) == ALL_MONTHS
    assert rows["2026-01"] == "333.2000" and rows["2026-12"] == "0"
    assert "WARNING" not in output, (
        "months that have no figure yet are normal. Warning about them drowns "
        "the warning that matters")
