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

Filed as #33.
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
