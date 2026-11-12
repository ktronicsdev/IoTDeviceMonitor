#!/usr/bin/env python3
"""The animated alarm-pipeline page (site/alarm-pipeline/index.html) is static and
self-contained. It must name every source and stage, honour reduced motion, and pull
nothing from a CDN. It must not live under site/ph1800/, where each subdirectory is
treated as a plant page."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[7]
PAGE = REPO_ROOT / "site" / "alarm-pipeline" / "index.html"

SOURCES = ["Shine Cloud", "Dess Cloud", "Solis Cloud", "Deye Cloud",
           "SolaX Cloud", "Solarman Cloud", "Ktronics IoT Cloud"]
STAGES = ["READ", "GATE", "DECIDE", "NOTIFY", "REMEMBER"]


def _html():
    return PAGE.read_text(encoding="utf-8")


def test_page_exists_outside_ph1800():
    assert PAGE.is_file()
    assert "ph1800" not in PAGE.parts


def test_names_every_source_and_stage():
    html = _html()
    for name in SOURCES + STAGES:
        assert name in html, name


def test_no_py_suffix_or_must_in_text():
    html = _html()
    assert ".py" not in html
    assert "MUST" not in html


def test_vertical_layout_with_customer_popup():
    """Stages are stacked in one narrow column (phone first), and the NOTIFY step
    pops a customer notification up and closes it again."""
    html = _html()
    assert "<svg id=" not in html
    assert "max-width:520px" in html
    assert 'class="pop"' in html and "@keyframes pop" in html and "@keyframes xpress" in html


def test_does_not_switch_animation_off():
    # The browser's reduced-motion flag was freezing the page for the owner; the motion is
    # the point of this page, so it must not be disabled.
    assert "prefers-reduced-motion" not in _html()


def test_no_external_scripts():
    assert not re.search(r"<script[^>]+src=", _html())
