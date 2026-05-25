from strategy_viz.specs import entry_lines, exit_lines, expectancy, thesis


def test_entry_lines_only_enabled():
    entry = {
        "rvPctile": {"enabled": False, "op": ">", "value": 50, "window": 168},
        "ivPctile": {"enabled": True, "op": ">", "value": 55, "window": 720},
        "rsi": {"enabled": True, "op": "<", "value": 30},
    }
    lines = entry_lines(entry)
    assert len(lines) == 2
    assert "IV pctile > 55" in lines[0]
    assert "RSI(14) < 30" == lines[1]


def test_entry_lines_handles_missing_op_safely():
    # schema-accepted-but-incomplete input must not raise; renderers can still display
    entry = {"ivPctile": {"enabled": True}}  # no op/value/window
    lines = entry_lines(entry)
    assert len(lines) == 1
    assert "IV pctile" in lines[0]
    assert "None" in lines[0]


def test_exit_lines_all_types():
    exit_ = {
        "profitTarget": {"enabled": True, "value": 50},
        "stopLoss": {"enabled": True, "value": 100},
        "dteFloor": {"enabled": True, "value": 1},
        "maxHold": {"enabled": False, "value": 168},
    }
    lines = exit_lines(exit_)
    joined = " | ".join(lines)
    assert "profit ≥ 50% of premium" in joined
    assert "loss ≥ 100% of premium" in joined
    assert "DTE ≤ 1d" in joined
    assert "held" not in joined  # maxHold disabled


def test_thesis_iron_condor_shape():
    strat = {
        "legs": [
            {"side": "SELL", "type": "option", "optionType": "CALL"},
            {"side": "BUY", "type": "option", "optionType": "CALL"},
            {"side": "SELL", "type": "option", "optionType": "PUT"},
            {"side": "BUY", "type": "option", "optionType": "PUT"},
        ],
        "entry": {"ivPctile": {"enabled": True, "op": ">", "value": 55}},
    }
    t = thesis(strat)
    assert "sell 2 options" in t
    assert "buy 2 options" in t
    assert "elevated IV" in t


def test_thesis_no_trigger():
    strat = {"legs": [{"side": "SELL", "type": "option", "optionType": "PUT"}], "entry": {}}
    assert "no signal filter" in thesis(strat)


def test_expectancy_empty():
    assert expectancy([]) == 0.0


def test_expectancy_average():
    assert expectancy([{"pnl": 100}, {"pnl": -50}, {"pnl": 150}]) == 200 / 3
