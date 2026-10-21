"""
One bad import must not hide the whole suite.

On 2026-10-21 `test_tuya_mcp.py` did `import tuya_mcp`, which imports
`requests`. CI installs `pytest pyyaml` and nothing else, because tuya_mcp is a
workstation tool that talks to the author's own home devices. pytest answered
with:

    collected 661 items / 1 error
    !!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!

Not one failing test - *no* tests. Every scheduled ShineMonitor run went red
with nothing actually run, so the red said only "something is broken
somewhere", which is the least useful thing a test suite can say.

Two defences, one per test here:

  * the runners pass --continue-on-collection-errors, so a bad file costs that
    file and the rest still report;
  * a test module may not import a package CI does not install unless it
    guards the import, so the bad file does not happen in the first place.

The second is the real fix and the first is the safety net; a suite that can be
silenced by one typo is worth both.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

_WORKFLOWS = Path(".github") / "workflows"
_SCRIPTS = Path("src") / "main" / "java" / "org" / "ktronics" / "scripts"
_FLAG = "--continue-on-collection-errors"

# Exactly what the CI `pip install` line provides, plus the import name where
# it differs from the package name (pyyaml -> yaml).
CI_PACKAGES = {"pytest", "yaml"}


def _repo_root():
    """Walk up to the repo root. Counting .parent lands outside the tree."""
    for parent in Path(__file__).resolve().parents:
        if (parent / _WORKFLOWS).is_dir():
            return parent
    raise AssertionError(f"could not find {_WORKFLOWS} above {__file__}")


ROOT = _repo_root()
HERE = Path(__file__).resolve().parent


def _first_party():
    """Module names importable from the repo itself, via sys.path juggling."""
    names = set()
    for d in (ROOT / _SCRIPTS, ROOT / "bms-module" / "scripts", HERE):
        if d.is_dir():
            names |= {p.stem for p in d.glob("*.py")}
    for d in (ROOT / _SCRIPTS).glob("*/"):
        if (d / "__init__.py").is_file():
            names.add(d.name)
    return names


def _runners():
    """
    Names of the workflows that actually run the suite.

    Names, not contents: a parametrize value becomes the test id, and a whole
    workflow file as an id overflows the environment variable pytest puts the
    current test name into.
    """
    found = []
    for path in sorted((ROOT / _WORKFLOWS).glob("*.yml")):
        if re.search(r"^\s*pytest\s+src/test",
                     path.read_text(encoding="utf-8"), re.M):
            found.append(path.name)
    return found


def test_there_is_a_runner_to_check():
    """Guard against this file passing because it matched nothing."""
    assert _runners(), (
        "no workflow runs `pytest src/test` — if the suite moved, point this "
        "file at its new runner rather than leaving it passing vacuously")


@pytest.mark.parametrize("name", _runners())
def test_a_collection_error_does_not_hide_the_rest(name):
    text = (ROOT / _WORKFLOWS / name).read_text(encoding="utf-8")

    assert _FLAG in text, (
        f"{name} runs the suite without {_FLAG}. Without it a single "
        f"module-level ImportError aborts collection and NO tests run, so a "
        f"red build tells you nothing about the production code. Add it to "
        f"the pytest invocation."
    )


def _module_level_imports(tree):
    """Top-level imports only: inside a try or a function they are guarded."""
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.module.split(".")[0], node.lineno


@pytest.mark.parametrize(
    "path", sorted(HERE.glob("test_*.py")), ids=lambda p: p.name)
def test_a_test_module_does_not_need_a_package_ci_lacks(path):
    """
    CI installs `pytest pyyaml`. Anything else a test imports at module level
    has to be either first-party or guarded with pytest.importorskip, or the
    file takes the whole suite down on a machine without it.
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    guarded = set(re.findall(r"importorskip\(\s*[\"']([\w.]+)", text))
    allowed = CI_PACKAGES | set(sys.stdlib_module_names) | _first_party()
    allowed |= {g.split(".")[0] for g in guarded}

    offenders = [
        f"line {line}: import {mod}"
        for mod, line in _module_level_imports(tree)
        if mod not in allowed
    ]

    assert not offenders, (
        f"{path.name} imports a package CI does not install:\n  "
        + "\n  ".join(offenders)
        + "\n\nEither add it to the CI `pip install` line, or guard it:\n"
        "    pytest.importorskip(\"name\", exc_type=ImportError)\n"
        "before the import. Unguarded, it aborts collection for every test "
        "in the suite, not just this file."
    )
