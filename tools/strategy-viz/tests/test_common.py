from pathlib import Path

import pytest

from strategy_viz.common import REASON_COLORS, cycles_from_trades, ensure_parent, gate_label, hrs


def test_hrs_round_days():
    assert hrs(24) == "1d"
    assert hrs(168) == "7d"
    assert hrs(720) == "30d"


def test_hrs_sub_day_keeps_hours():
    assert hrs(6) == "6h"
    assert hrs(12) == "12h"
    assert hrs(0) == "0h"


def test_gate_label_modes():
    assert gate_label("all", 1, 3) == "ALL of 3"
    assert gate_label("any", 1, 3) == "ANY of 3"
    assert gate_label("min", 2, 3) == "≥2 of 3"


def test_gate_label_defaults_to_all_when_missing():
    assert gate_label("", 1, 2) == "ALL of 2"
    assert gate_label(None, 1, 2) == "ALL of 2"


def test_reason_colors_complete_set():
    # exit-reason vocabulary must cover what the backtester engine emits
    expected = {"TP", "SL", "DTE", "EXPIRY", "MAX", "DTL", "IVP", "REHEDGE"}
    assert expected.issubset(REASON_COLORS.keys())


def test_cycles_group_legs_by_entry_time():
    trades = [
        {"entry_time": 100, "exit_time": 200, "exit_spot": 50.0, "pnl": 1.0,
         "reason": "TP", "bars_held": 2, "is_hedge": False},
        {"entry_time": 100, "exit_time": 210, "exit_spot": 50.0, "pnl": 2.0,
         "reason": "TP", "bars_held": 2, "is_hedge": False},
        {"entry_time": 300, "exit_time": 400, "exit_spot": 51.0, "pnl": -1.0,
         "reason": "SL", "bars_held": 5, "is_hedge": False},
    ]
    cycles = cycles_from_trades(trades)
    assert len(cycles) == 2
    assert cycles[0]["entry_time"] == 100
    assert cycles[0]["pnl"] == 3.0
    assert cycles[0]["n_legs"] == 2
    assert cycles[0]["exit_time"] == 210  # the max across grouped legs
    assert cycles[1]["pnl"] == -1.0


def test_cycles_skip_hedge_legs():
    trades = [
        {"entry_time": 1, "exit_time": 2, "exit_spot": 10, "pnl": 5,
         "reason": "TP", "is_hedge": False},
        {"entry_time": 1, "exit_time": 3, "exit_spot": 10, "pnl": 99,
         "reason": "REHEDGE", "is_hedge": True},
    ]
    cycles = cycles_from_trades(trades)
    assert len(cycles) == 1
    assert cycles[0]["pnl"] == 5


def test_ensure_parent_creates_dirs(tmp_path: Path):
    target = tmp_path / "nested" / "dir" / "file.txt"
    out = ensure_parent(target)
    assert out is target
    assert target.parent.exists()
    target.write_text("ok")
    assert target.read_text() == "ok"


def test_ensure_parent_existing_path_ok(tmp_path: Path):
    target = tmp_path / "file.txt"
    ensure_parent(target)
    ensure_parent(target)  # idempotent
