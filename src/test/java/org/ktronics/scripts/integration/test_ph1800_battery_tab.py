#!/usr/bin/env python3
"""Tests for the Battery tab on the PH1800 dashboard (site/ph1800/_app.html).

Two layers:
  * static wiring checks (always run) - the tab is declared, wired into switchTab, and
    refreshed on every data poll, and every plant page is an exact copy of the template;
  * a real render test (needs node) - runs the dashboard JS in a vm with a stub DOM and
    asserts what renderBattery() actually produces for a battery plant, a PV-only plant,
    live per-cell data, and stale per-cell data. See fixtures/ph1800_battery_tab.test.js.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[7]
APP = REPO_ROOT / "site" / "ph1800" / "_app.html"
HARNESS = Path(__file__).parent / "fixtures" / "ph1800_battery_tab.test.js"


@pytest.fixture(scope="module")
def app_html():
    return APP.read_text(encoding="utf-8")


class TestBatteryTabWiring:
    def test_tab_button_and_pane_exist(self, app_html):
        assert 'data-tab="battery"' in app_html
        assert 'id="tabbtn-battery"' in app_html
        assert 'id="tab-battery"' in app_html
        assert 'id="battwrap"' in app_html

    def test_button_starts_hidden(self, app_html):
        """A PV-only plant must never show an empty Battery tab; renderBattery() reveals it."""
        i = app_html.index('id="tabbtn-battery"')
        assert 'style="display:none"' in app_html[i:i + 120]

    def test_switch_tab_handles_battery(self, app_html):
        assert "name==='battery'?'block':'none'" in app_html
        assert "if(name==='battery') renderBattery();" in app_html

    def test_rendered_on_every_data_refresh(self, app_html):
        """Otherwise the pane would only update when the tab is clicked."""
        assert "renderLive(); renderBMS(); renderBattery();" in app_html

    def test_soc_is_labelled_as_an_estimate(self, app_html):
        """It is derived from pack voltage via SOC_TABLE, not read from a BMS - saying
        otherwise would misrepresent the number to the customer."""
        assert "(estimated)" in app_html
        assert "Estimated from pack voltage" in app_html


class TestPlantPagesMatchTemplate:
    """check_ph1800.ensure_page() stamps each plant page as a verbatim copy of _app.html,
    so a drifted page means someone edited a copy instead of the template."""

    def test_every_plant_page_is_identical_to_the_template(self):
        tpl = APP.read_bytes()
        pages = sorted(p / "index.html" for p in APP.parent.iterdir() if p.is_dir())
        assert pages, "no plant pages found under site/ph1800/"
        drifted = [str(p.relative_to(REPO_ROOT)) for p in pages
                   if not p.exists() or p.read_bytes() != tpl]
        assert not drifted, f"pages differ from _app.html: {drifted}"

    def test_template_uses_lf_endings(self):
        """ensure_page() writes with newline='' to preserve LF; a CRLF flip would rewrite
        every plant page on the next run and churn the diff."""
        assert b"\r\n" not in APP.read_bytes()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
class TestBatteryTabRender:
    def test_renders_correctly_for_all_states(self):
        r = subprocess.run(["node", str(HARNESS), str(REPO_ROOT)],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"render checks failed:\n{r.stdout}\n{r.stderr}"
        assert "ALL CHECKS PASSED" in r.stdout
