#!/usr/bin/env python3
"""Tests for the two clocks in the dashboard header (ph1000 + ph1800).

The header carries "Actual time (SL)", computed in the BROWSER, next to "Last update",
a plant-LOCAL stamp with no zone suffix that the publisher copies out of the device.
Both have been wrong in production:

  * 2026-09-07 - the clock read 11:27:53 beside a 16:53 device stamp. The browser had
    formatted `timeZone:'Asia/Colombo'` as UTC (a managed profile or an anti-fingerprint
    extension drops the zone), so the clock ran exactly 5h30m slow;
  * ph1000 parsed the device stamp with `new Date(s)` - i.e. in the VIEWER's zone - so
    from UTC+2 a fresh stamp landed in the future, `age` went negative and hours-old
    data kept a green "Online" dot. ph1800 had already fixed this; ph1000 had not.

Two layers, mirroring test_ph1800_battery_tab.py:
  * static checks (always run) - the conversion is arithmetic and no clock path depends
    on the browser agreeing to resolve an IANA zone;
  * a real render test (needs node) - runs each page's JS in a vm under a frozen clock
    and asserts the header text, once with a working Intl and once with a browser that
    ignores zones, repeated from several viewer timezones.
    See fixtures/dashboard_clock.test.js.

Sri Lanka is UTC+05:30 year-round (no DST), which is what makes the arithmetic exact.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[7]
PH1000 = REPO_ROOT / "site" / "ph1000" / "index.html"
PH1800 = REPO_ROOT / "site" / "ph1800" / "_app.html"
HARNESS = Path(__file__).parent / "fixtures" / "dashboard_clock.test.js"

PAGES = {"ph1000": PH1000, "ph1800": PH1800}


@pytest.fixture(scope="module")
def pages():
    return {name: p.read_text(encoding="utf-8") for name, p in PAGES.items()}


class TestClockWiring:
    """The offset is a constant, not something we ask the browser for."""

    @pytest.mark.parametrize("name", sorted(PAGES))
    def test_header_has_the_clock_element(self, pages, name):
        assert 'id="sltime"' in pages[name]
        assert "Actual time (SL)" in pages[name]

    @pytest.mark.parametrize("name", sorted(PAGES))
    def test_offset_is_the_fixed_330_minutes(self, pages, name):
        """+05:30 with no DST, so a hardcoded offset is exact - and cannot be dropped
        by a browser that refuses to resolve Asia/Colombo."""
        assert "SL_MS=330*60000" in pages[name] or "SL_MS = 330*60000" in pages[name]

    def test_ph1000_clock_does_not_ask_the_browser_for_the_zone(self, pages):
        """This is the 5h30m-slow bug: tickSL() must not go through Intl at all."""
        src = pages["ph1000"]
        body = src[src.index("function tickSL()"):]
        body = body[:body.index("\n}")]
        assert "Intl" not in body
        assert "slParts(" in body

    def test_ph1800_probes_intl_and_falls_back(self, pages):
        """ph1800 keeps Intl (zoneParts is generic) but must verify it once at load and
        fall back to the fixed offset when the browser hands back UTC."""
        src = pages["ph1800"]
        assert "const TZ_OK=" in src
        assert "(tz===PLANT_TZ && !TZ_OK) ? _slParts(d)" in src

    def test_ph1000_parses_device_stamps_as_plant_local(self, pages):
        """`new Date("2026-09-07 16:53:01")` reads the stamp in the VIEWER's zone. That
        is the stuck-"Online" bug - the stamp must be pinned to Colombo instead."""
        src = pages["ph1000"]
        assert "const ts = s => new Date(String(s).replace(' ','T'));" not in src, \
            "ph1000 ts() is back to viewer-zone parsing"
        assert "Date.parse(String(s).replace(' ','T')+'Z') - SL_MS" in src

    @pytest.mark.parametrize("name", sorted(PAGES))
    def test_no_page_hardcodes_a_summer_time_shift(self, pages, name):
        """Sri Lanka has had no DST since 2006; anything conditional on a month or a
        getTimezoneOffset() reading would be a viewer-zone leak creeping back in."""
        assert "getTimezoneOffset" not in pages[name]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
class TestClockRender:
    """Run the real page script under a frozen clock and read the header back."""

    # The viewer's own timezone must not matter. UTC+2 is where the bug was reported
    # from, Asia/Colombo is the plant itself, and the other two bracket the offset.
    @pytest.mark.parametrize("tz", ["Europe/Stockholm", "Asia/Colombo", "UTC",
                                    "America/New_York", "Pacific/Auckland"])
    def test_clock_and_staleness_are_right_from_any_viewer_timezone(self, tz):
        r = subprocess.run(["node", str(HARNESS), str(REPO_ROOT)],
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ, "TZ": tz})
        assert r.returncode == 0, f"clock checks failed under TZ={tz}:\n{r.stdout}\n{r.stderr}"
        assert "ALL CHECKS PASSED" in r.stdout

    def test_harness_actually_catches_the_bug(self, tmp_path):
        """A guard on the guard: rebuild the pre-fix pages and confirm the harness fails
        on them. Without this the suite could quietly stop testing anything."""
        broken_1000 = PH1000.read_text(encoding="utf-8").replace(
            "const ts = s => new Date(Date.parse(String(s).replace(' ','T')+'Z') - SL_MS);",
            "const ts = s => new Date(String(s).replace(' ','T'));")
        assert broken_1000 != PH1000.read_text(encoding="utf-8"), "ts() anchor moved"
        (tmp_path / "site" / "ph1000").mkdir(parents=True)
        (tmp_path / "site" / "ph1800").mkdir(parents=True)
        (tmp_path / "site" / "ph1000" / "index.html").write_text(broken_1000, encoding="utf-8")
        (tmp_path / "site" / "ph1800" / "_app.html").write_text(
            PH1800.read_text(encoding="utf-8"), encoding="utf-8")

        r = subprocess.run(["node", str(HARNESS), str(tmp_path)],
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ, "TZ": "Europe/Stockholm"})
        assert r.returncode != 0, "harness passed on the pre-fix ph1000 page"
        assert "CHECK(S) FAILED" in r.stdout


class TestPlantPagesCarryTheFix:
    """The ph1800 plant pages are verbatim copies of _app.html - a copy that missed the
    fix would serve a 5h30m-slow clock to that one customer."""

    def test_every_ph1800_plant_page_has_the_fallback(self):
        pages = sorted(p / "index.html" for p in PH1800.parent.iterdir() if p.is_dir())
        assert pages, "no plant pages found under site/ph1800/"
        stale = [str(p.relative_to(REPO_ROOT)) for p in pages
                 if "const TZ_OK=" not in p.read_text(encoding="utf-8")]
        assert not stale, f"plant pages missing the clock fix: {stale}"
