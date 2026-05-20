import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "strategy.schema.json").read_text())
SAMPLES = ROOT / "samples"
VALIDATOR = jsonschema.Draft7Validator(SCHEMA)

BACKTESTER_SAMPLES = [
    "iron_condor_btc.json", "short_strangle_eth.json",
    "csp_btc_oversold.json", "covered_perp_call_sol.json",
]


@pytest.mark.parametrize("fname", BACKTESTER_SAMPLES)
def test_all_backtester_samples_validate(fname):
    s = json.loads((SAMPLES / fname).read_text())
    errors = list(VALIDATOR.iter_errors(s))
    assert not errors, f"sample failed: {[e.message for e in errors]}"


def _base_strategy():
    return {
        "name": "x", "underlying": "BTC", "capital": 10000,
        "legs": [{"type": "option", "side": "SELL", "optionType": "PUT",
                  "strikeMode": "delta", "strikeParam": 0.25, "dteTarget": 14, "size": 1}],
        "entry": {"gateMode": "all"},
        "exit": {"gateMode": "any"},
    }


def test_enabled_pctile_requires_op_value_window():
    s = _base_strategy()
    s["entry"]["ivPctile"] = {"enabled": True}  # missing op/value/window
    errors = list(VALIDATOR.iter_errors(s))
    assert errors, "schema accepted enabled=true ivPctile with no op/value"
    msgs = " | ".join(e.message for e in errors)
    assert "'op'" in msgs and "'value'" in msgs


def test_enabled_sma_requires_period_not_value():
    s = _base_strategy()
    s["entry"]["sma"] = {"enabled": True, "op": ">", "value": 5}  # wrong: needs op above/below + period
    errors = list(VALIDATOR.iter_errors(s))
    assert errors


def test_value_only_exit_condition():
    s = _base_strategy()
    s["exit"]["profitTarget"] = {"enabled": True}  # missing value
    errors = list(VALIDATOR.iter_errors(s))
    assert errors


def test_min_mode_requires_gate_min():
    s = _base_strategy()
    s["entry"]["gateMode"] = "min"
    errors = list(VALIDATOR.iter_errors(s))
    assert errors


def test_option_leg_requires_strike_fields():
    s = _base_strategy()
    s["legs"] = [{"type": "option", "side": "SELL", "size": 1}]  # missing strike/dte/optionType
    errors = list(VALIDATOR.iter_errors(s))
    assert errors


def test_perp_leg_does_not_need_option_fields():
    s = _base_strategy()
    s["legs"] = [{"type": "perp", "side": "BUY", "size": 1}]
    errors = list(VALIDATOR.iter_errors(s))
    assert not errors, f"perp leg should validate: {[e.message for e in errors]}"


def test_disabled_condition_with_only_enabled_field_is_valid():
    s = _base_strategy()
    s["entry"]["ivPctile"] = {"enabled": False}
    errors = list(VALIDATOR.iter_errors(s))
    assert not errors


def test_unknown_underlying_rejected():
    s = _base_strategy()
    s["underlying"] = "XRP"
    errors = list(VALIDATOR.iter_errors(s))
    assert errors
