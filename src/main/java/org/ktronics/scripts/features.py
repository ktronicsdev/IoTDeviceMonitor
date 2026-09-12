"""
Feature flags — single source of truth for which emails and alerts are live.

Reads src/main/java/org/ktronics/config/features.json. Every flag can be
overridden for one run with an environment variable, so a manual
workflow_dispatch can re-enable a channel without a commit:

    KT_FEATURE_EMAILS_WEEKLY_CUSTOMER_REPORTS=true

The env var name is KT_FEATURE_ + the dotted path, upper-cased, dots replaced
by underscores.

Usage:
    from features import is_enabled
    if is_enabled("emails.weekly_customer_reports"):
        ...

A missing flag returns the caller's default (True unless stated otherwise), so
adding a new channel never silently mutes an existing one.
"""

import json
import os
from pathlib import Path

# Anchored to this module, not the CWD: the workflows run from the repo root but
# pytest does not, and a CWD-relative path silently "fails open" (every flag reads
# as ON) when it can't find the file — which would make a disabled channel come
# back to life depending on where the script was invoked from.
FEATURES_PATH = Path(__file__).resolve().parent.parent / "config" / "features.json"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

_cache = None


def _load():
    """Read and cache features.json. A missing or malformed file means 'all on'."""
    global _cache
    if _cache is not None:
        return _cache

    try:
        with open(FEATURES_PATH, "r", encoding="utf-8") as f:
            _cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Fail open: a broken flags file must not silence alerting.
        _cache = {}

    return _cache


def env_name(path):
    """Dotted flag path -> its environment-variable override name."""
    return "KT_FEATURE_" + path.replace(".", "_").upper()


def is_enabled(path, default=True):
    """
    True if the flag at `path` (e.g. "emails.device_alarms_admin") is on.

    Order of precedence: environment override, then features.json, then default.
    """
    override = os.environ.get(env_name(path))
    if override is not None:
        lowered = override.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        # An unparseable override is ignored rather than guessed at.

    node = _load()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]

    return bool(node) if isinstance(node, bool) else default


def all_flags():
    """Flat {dotted_path: bool} of every flag, with env overrides applied."""
    flat = {}

    def walk(node, prefix):
        for key, value in node.items():
            if key.startswith("_") or key == "version":
                continue
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                walk(value, path)
            elif isinstance(value, bool):
                flat[path] = is_enabled(path)

    walk(_load(), "")
    return flat


def reset_cache():
    """Drop the cached file — for tests that write a temporary features.json."""
    global _cache
    _cache = None


def _emit_github_output(stream):
    """Write every flag as a step output so workflow `if:` can gate on it.

    Dots are not legal in output names, so "emails.device_alarms_admin" becomes
    "emails_device_alarms_admin" and is read as
    steps.flags.outputs.emails_device_alarms_admin == 'true'.
    """
    for name, state in sorted(all_flags().items()):
        stream.write(f"{name.replace('.', '_')}={'true' if state else 'false'}\n")


if __name__ == "__main__":
    import sys

    # `python3 features.py` prints the live flag state — handy in workflow logs.
    for name, state in sorted(all_flags().items()):
        print(f"{'ON ' if state else 'off'}  {name}")

    # `python3 features.py --github-output` also feeds $GITHUB_OUTPUT.
    if "--github-output" in sys.argv:
        out = os.environ.get("GITHUB_OUTPUT")
        if not out:
            print("GITHUB_OUTPUT not set — nothing written", file=sys.stderr)
            sys.exit(1)
        with open(out, "a", encoding="utf-8") as f:
            _emit_github_output(f)
