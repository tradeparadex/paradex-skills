import json
from pathlib import Path

import pytest

from strategy_viz.mermaid import (
    backtester_to_mermaid, convert, listener_to_mermaid,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture
def iron_condor():
    return json.loads((SAMPLES / "iron_condor_btc.json").read_text())


@pytest.fixture
def expression_dsl():
    return json.loads((SAMPLES / "btc-expression-dsl.json").read_text())


@pytest.fixture
def rsi_alert():
    return json.loads((SAMPLES / "btc-rsi-alert.json").read_text())


def test_backtester_mermaid_has_required_nodes(iron_condor):
    mmd, name = backtester_to_mermaid(iron_condor)
    assert name == iron_condor["name"]
    assert mmd.startswith("flowchart TD")
    assert "Entry gate" in mmd
    assert "Exit gate" in mmd
    assert "EXPIRY override" in mmd
    # one node per leg
    for i in range(len(iron_condor["legs"])):
        assert f"L{i}[" in mmd


def test_backtester_renders_gate_mode_correctly(iron_condor):
    mmd, _ = backtester_to_mermaid(iron_condor)
    # iron_condor uses gateMode="all" with one enabled entry → "ALL of 1"
    assert "ALL of 1" in mmd


def test_listener_expression_keeps_label_text(expression_dsl):
    # Regression: prior bug used line.replace('N', ...) which mangled 'ANY' / 'NOT'.
    mmd, _ = listener_to_mermaid(expression_dsl)
    assert '"ANY"' in mmd
    assert '"NOT"' in mmd
    # node ids should be prefixed per evaluator (E0N0, E1N0, etc) but the labels
    # MUST remain "ANY" / "NOT" — never "AE1NY" or "E1NOT"
    assert "AE1NY" not in mmd
    assert "E1NOT" not in mmd
    assert "ANY" in mmd
    assert "NOT" in mmd


def test_listener_gate_form(rsi_alert):
    mmd, _ = listener_to_mermaid(rsi_alert)
    assert mmd.startswith("flowchart TD")
    assert "Evaluator: rsi-oversold" in mmd
    assert "ALL of 2" in mmd  # rsi + fundingRate both enabled
    assert "webhook" in mmd.lower()


def test_convert_dispatches_on_form(iron_condor, rsi_alert):
    bt_mmd, _ = convert(iron_condor)
    ls_mmd, _ = convert(rsi_alert)
    # backtester form has a 'Cycle' starting node, listener has 'Feed'
    assert "Cycle" in bt_mmd and "Feed" not in bt_mmd
    assert "Feed" in ls_mmd and "Cycle" not in ls_mmd


def test_backtester_marks_listener_form_as_not_applicable(rsi_alert):
    # listener-form should not accidentally be routed through backtester branch
    mmd, _ = convert(rsi_alert)
    assert "Hold position" not in mmd
    assert "EXPIRY override" not in mmd


def test_no_delta_hedge_node_when_disabled(iron_condor):
    mmd, _ = backtester_to_mermaid(iron_condor)
    assert "Δ-hedge" not in mmd  # iron_condor has deltaHedge.enabled=False
