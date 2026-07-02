#!/usr/bin/env python3
"""Tests for the PACK-cell (79 B) BMS frame: decode + fetch discrimination + the independent
cells carry-forward that feeds the dashboard's Battery B1/B2 tabs.

The cell frame is a DIFFERENT, mutually-exclusive frame type from the summary frame — it carries
all 16 cell mV + 4 temps + pack voltage but no SOC/current. No network (api_call is monkeypatched)."""
import sys
from pathlib import Path

import pytest  # noqa: F401

SRC = Path(__file__).resolve().parents[6]
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_ph1000_bms as bms  # noqa: E402

# A real 79 B cell frame captured from Mifanza B1 (verified against the PACEEX app):
# 16 cells 3364..3377 mV, temps 31.8/31.8/31.7/31.5 C, pack ~54.67 V.
CELL_HEX_B1 = ("9A00000A0200004402155F10"
               "0D240BE80D880BE80D420BE70DDE0BE5"
               "0D6300000D2800000D7F00000D7D0000"
               "0D5600000D2100000D3C00000D3A0000"
               "0D5A00000D6B00000D5C00000D310000"
               "70C49D")
HEARTBEAT_HEX = "9A0000000700000618070232116DB19D"   # short heartbeat, not a cell frame
EXPECTED_CELLS = [3364, 3464, 3394, 3550, 3427, 3368, 3455, 3453,
                  3414, 3361, 3388, 3386, 3418, 3435, 3420, 3377]


class TestDecodeCellFrame:
    def test_decodes_16_cells_temps_and_pack_v(self):
        c = bms.decode_cell_frame(CELL_HEX_B1)
        assert c["mv"] == EXPECTED_CELLS
        assert c["temps"] == [31.8, 31.8, 31.7, 31.5]
        assert c["pack_v"] == round(sum(EXPECTED_CELLS) / 1000.0, 2)
        assert c["high"] == {"n": 4, "mv": 3550} and c["low"] == {"n": 10, "mv": 3361}
        assert c["spread"] == 3550 - 3361
        assert c["tmax"] == {"n": 1, "c": 31.8} and c["tmin"] == {"n": 4, "c": 31.5}

    def test_rejects_heartbeat_and_summary(self):
        assert bms.decode_cell_frame(HEARTBEAT_HEX) is None      # byte[3]!=0x0A
        assert bms.decode_cell_frame("9A00") is None             # too short


class TestFetchPackDiscrimination:
    def test_cell_mode_returns_marker_with_cell_frame(self, monkeypatch):
        monkeypatch.setattr(bms, "_octet_call",
                            lambda *a, **k: {"code": 200, "data": {"WIFI_Band": {"value": CELL_HEX_B1, "time": 111}}})
        d = bms.fetch_pack({"name": "Mifanza B1", "role": "master", "iotId": "x"}, "tok")
        assert d["offline"] is True                              # no fresh SOC this run
        assert d["cell_frame"]["mv"] == EXPECTED_CELLS           # ...but cells captured
        assert d["reported_ms"] == 111

    def test_heartbeat_returns_plain_offline(self, monkeypatch):
        monkeypatch.setattr(bms, "_octet_call",
                            lambda *a, **k: {"code": 200, "data": {"WIFI_Band": {"value": HEARTBEAT_HEX, "time": 5}}})
        d = bms.fetch_pack({"name": "Mifanza B2", "role": "slave", "iotId": "y"}, "tok")
        assert d["offline"] is True and "cell_frame" not in d


class TestAttachCells:
    def _out_with_pack(self, name="Mifanza B1"):
        return {"ok": True, "packs": [{"iotId": "x", "name": name, "soc": 97}],
                "offline_packs": [name]}

    def test_attaches_fresh_cells_and_clears_offline(self):
        out = self._out_with_pack()
        bms.attach_cells(out, {"x": {"mv": EXPECTED_CELLS, "ts": 999}}, {})
        assert out["packs"][0]["cells"]["mv"] == EXPECTED_CELLS
        assert out["offline_packs"] == []                        # cell mode != offline

    def test_carries_cells_from_prev_when_absent_this_run(self):
        out = self._out_with_pack()
        prev = {"packs": [{"iotId": "x", "cells": {"mv": EXPECTED_CELLS, "ts": 42}}]}
        bms.attach_cells(out, {}, prev)                          # no fresh cells -> carry forward
        assert out["packs"][0]["cells"]["ts"] == 42

    def test_no_cells_leaves_pack_untouched(self):
        out = self._out_with_pack()
        bms.attach_cells(out, {}, {})
        assert "cells" not in out["packs"][0]

    def test_cells_only_pack_added_when_no_summary_anywhere(self):
        # pack in cell mode, no fresh summary AND none in prev -> add a cells-only pack so the
        # Battery tab still shows cells (main SOC pane skips it: no soc).
        out = {"ok": False, "packs": [], "auth_failed": False,
               "offline_packs": [bms.PACKS[0]["name"]]}
        bms.attach_cells(out, {bms.PACKS[0]["iotId"]: {"mv": EXPECTED_CELLS, "ts": 3}}, {})
        assert len(out["packs"]) == 1 and out["ok"] is True
        p = out["packs"][0]
        assert p.get("cells_only") is True and p.get("soc") is None
        assert p["cells"]["mv"] == EXPECTED_CELLS and out["offline_packs"] == []

    def test_cells_independent_of_summary_carry_forward(self):
        # a pack in cell mode: no fresh summary -> summary carried; cells fresh -> attached.
        out = {"ok": False, "packs": [], "auth_failed": False, "offline_packs": ["Mifanza B1"]}
        prev = {"packs": [{"iotId": bms.PACKS[0]["iotId"], "name": "Mifanza B1", "soc": 91}]}
        bms.carry_forward_packs(out, prev)                       # brings back SOC 91 (carried)
        bms.attach_cells(out, {bms.PACKS[0]["iotId"]: {"mv": EXPECTED_CELLS, "ts": 7}}, prev)
        pk = out["packs"][0]
        assert pk["soc"] == 91 and pk["carried"] is True         # summary carried
        assert pk["cells"]["mv"] == EXPECTED_CELLS               # cells fresh
        assert out["offline_packs"] == []
