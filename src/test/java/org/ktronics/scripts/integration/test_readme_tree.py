"""
The Project Structure tree in Readme.md must match the repository.

On 2026-10-23 the tree listed 6 of the 12 workflows and 22 of the 38 scripts
(#35). It had drifted further than anything else in the README, and for a
plain reason: nothing checked it. The test tables are reconciled before every
push, but no test read the README at all, so a new workflow or script could
land without its line and every push stayed green.

The tree is drawn by hand, so these tests read it the way a person does - by
indentation - rebuild each entry's path, and compare that with what git
tracks:

  * every workflow and every top-level script on main appears in the tree;
  * every workflow or script the tree names still exists;
  * each list closes on its last entry - adding a line below the old last
    one is the easiest way to draw the tree wrong.

Tracked files, not files on disk: a shared working tree holds other people's
unfinished scripts and gitignored workstation tools, and neither belongs in
the README until it is on main.
"""

import re
import subprocess
from pathlib import Path

_WORKFLOWS = ".github/workflows/"
_SCRIPTS = "src/main/java/org/ktronics/scripts/"
_CONNECTOR = re.compile(r"(├── |└── )(\S+)")


def _repo_root():
    """Walk up to the repo root. Counting .parent lands outside the tree."""
    for parent in Path(__file__).resolve().parents:
        if (parent / _WORKFLOWS).is_dir():
            return parent
    raise AssertionError(f"could not find {_WORKFLOWS} above {__file__}")


ROOT = _repo_root()


def _tree_lines():
    """The lines of the ```text block under '## Project Structure'."""
    text = (ROOT / "Readme.md").read_text(encoding="utf-8")
    section = text.split("## Project Structure", 1)[1]
    block = section.split("```text", 1)[1].split("```", 1)[0]
    return block.splitlines()


def _entries():
    """(depth, connector, full path) for every entry, in drawing order.

    A connector's column gives its depth (four characters a level), and the
    names above it at shallower depths give its directory.
    """
    entries, stack = [], []
    for line in _tree_lines():
        m = _CONNECTOR.search(line)
        if not m:
            continue                        # the root, a spacer or a comment
        depth = m.start() // 4
        stack = stack[:depth] + [m.group(2)]
        entries.append((depth, m.group(1).strip(), "".join(
            part if part.endswith("/") else part + "/" for part in stack
        ).rstrip("/")))
    return entries


def _tracked(prefix, suffixes):
    """Files git tracks directly under prefix (not in subdirectories)."""
    out = subprocess.run(["git", "ls-files", prefix], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return {p for p in out.splitlines()
            if p.endswith(suffixes) and "/" not in p[len(prefix):]}


TREE = {path for _, _, path in _entries()}
WORKFLOWS = _tracked(_WORKFLOWS, (".yml",))
SCRIPTS = _tracked(_SCRIPTS, (".py", ".sh"))


def test_the_tree_is_found_and_read():
    # Without this, a renamed heading or a changed fence parses to nothing,
    # and every test below passes by comparing empty sets.
    assert len(WORKFLOWS) >= 10 and len(SCRIPTS) >= 30, "git ls-files found too little"
    assert any(p.startswith(_WORKFLOWS) for p in TREE), \
        "parsed no workflow paths from the Project Structure tree"
    assert any(p.startswith(_SCRIPTS) for p in TREE), \
        "parsed no script paths from the Project Structure tree"


def test_every_workflow_is_in_the_tree():
    missing = sorted(WORKFLOWS - TREE)
    assert not missing, (
        "Readme.md Project Structure is missing these workflows - add each "
        "under .github/workflows/ with its use case:\n  "
        + "\n  ".join(p[len(_WORKFLOWS):] for p in missing))


def test_every_script_is_in_the_tree():
    missing = sorted(SCRIPTS - TREE)
    assert not missing, (
        "Readme.md Project Structure is missing these scripts - add each "
        "under scripts/, in the group for tools no workflow runs if that "
        "is what it is:\n  "
        + "\n  ".join(p[len(_SCRIPTS):] for p in missing))


def test_every_workflow_and_script_in_the_tree_still_exists():
    named = {p for p in TREE
             if (p.startswith(_WORKFLOWS) and p.endswith(".yml"))
             or (p.startswith(_SCRIPTS) and p.endswith((".py", ".sh"))
                 and "/" not in p[len(_SCRIPTS):])}
    gone = sorted(named - WORKFLOWS - SCRIPTS)
    assert not gone, (
        "Readme.md Project Structure names files that are not on main:\n  "
        + "\n  ".join(gone))


def test_each_list_closes_on_its_last_entry():
    entries = _entries()
    wrong = []
    for i, (depth, connector, path) in enumerate(entries):
        # The next entry at this depth or shallower decides: a sibling means
        # this entry is not last, anything shallower means it is.
        after = next((d for d, _, _ in entries[i + 1:] if d <= depth), -1)
        is_last = after < depth
        if is_last and connector != "└──":
            wrong.append(f"{path} is the last entry of its list but is drawn with ├──")
        if not is_last and connector != "├──":
            wrong.append(f"{path} is drawn with └── but entries follow it in the same list")
    assert not wrong, "Project Structure tree is drawn wrongly:\n  " + "\n  ".join(wrong)
