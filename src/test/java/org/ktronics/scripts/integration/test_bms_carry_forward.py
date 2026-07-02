#!/usr/bin/env python3
"""Tests for check_ph1000_bms.carry_forward_packs — keeping both BMS packs on the dashboard
across the device's heartbeat-only cycles by reusing the last-good summary per pack.

No network: carry_forward_packs is a pure function over (out, prev_bms). Regression guard for
the "B2 vanishes when only a heartbeat frame is cached" bug."""
import sys
from pathlib import Path

import pytest  # noqa: F401

SRC = Path(__file__).resolve().parents[6]
SCRIPTS = SRC / "main" / "java" / "org" / "ktronics" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_ph1000_bms as bms  # noqa: E402

ID1, ID2 = bms.PACKS[0]["iotId"], bms.PACKS[1]["iotId"]
N1, N2 = bms.PACKS[0]["name"], bms.PACKS[1]["name"]


def _pack(iotId, name, soc, rep):
    return {"iotId": iotId, "name": name, "soc": soc, "voltage": 54.0, "reported_ms": rep}


def _prev(*packs):
    return {"ok": True, "packs": list(packs)}


def test_carries_both_missing_packs_from_prev():
    out = {"ok": False, "packs": [], "auth_failed": False, "offline_packs": [N1, N2]}
    bms.carry_forward_packs(out, _prev(_pack(ID1, N1, 97, 1000), _pack(ID2, N2, 96, 1000)))
    assert out["ok"] is True
    assert {p["name"] for p in out["packs"]} == {N1, N2}
    assert all(p.get("carried") for p in out["packs"])     # both tagged carried
    assert out["offline_packs"] == []                       # shown from cache, not "offline"


def test_does_not_overwrite_a_fresh_pack():
    fresh = {"iotId": ID1, "name": N1, "soc": 50, "voltage": 53.0, "reported_ms": 9999}
    out = {"ok": True, "packs": [fresh], "auth_failed": False}
    bms.carry_forward_packs(out, _prev(_pack(ID1, N1, 97, 1000), _pack(ID2, N2, 96, 1000)))
    b1 = next(p for p in out["packs"] if p["iotId"] == ID1)
    assert b1["soc"] == 50 and not b1.get("carried")       # fresh B1 untouched
    b2 = next(p for p in out["packs"] if p["iotId"] == ID2)
    assert b2["soc"] == 96 and b2["carried"]               # only B2 carried


def test_skips_carry_when_session_dead():
    out = {"ok": False, "packs": [], "auth_failed": True}
    bms.carry_forward_packs(out, _prev(_pack(ID1, N1, 97, 1000)))
    assert out["packs"] == []                               # auth_failed -> no carry, pane hides


def test_no_prev_leaves_out_unchanged():
    out = {"ok": False, "packs": [], "auth_failed": False}
    bms.carry_forward_packs(out, {})
    assert out["packs"] == [] and out["ok"] is False


def test_ignores_prev_pack_without_summary():
    # a prev entry that was itself only a heartbeat (no soc) must not be carried
    out = {"ok": False, "packs": [], "auth_failed": False}
    bms.carry_forward_packs(out, _prev({"iotId": ID1, "name": N1, "reported_ms": 1000}))
    assert out["packs"] == []
