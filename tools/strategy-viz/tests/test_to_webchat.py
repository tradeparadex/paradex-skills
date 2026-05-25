"""Webchat composition tests — exercise the public API directly.

Calls `blocks.render(strat, bt, layout)` rather than a CLI shim. The
auto-promote-to-full-when-backtest-present behavior that used to live in
`compose()` is now the caller's choice; tests pass `layout="full"`
explicitly when they want the backtest panels."""
import json
from pathlib import Path

import pytest

from strategy_viz.blocks import render

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
OUT = Path(__file__).resolve().parent.parent / "out"
ALLOWED_COMPONENTS = {
    "alert_banner", "metric_card", "labeled_output",
    "performance_chart", "positions_table", "data_table", "markdown",
}


@pytest.fixture
def iron_condor():
    return json.loads((SAMPLES / "iron_condor_btc.json").read_text())


@pytest.fixture
def short_strangle():
    return json.loads((SAMPLES / "short_strangle_eth.json").read_text())


def test_render_returns_single_stack_spec(iron_condor):
    """The webchat renderer parses one JSON object per message — render must
    never return a list, and the layout must be a single 'stack'."""
    spec = render(iron_condor, None)
    assert isinstance(spec, dict), "render returned a list; renderer rejects arrays"
    assert spec["layout"] == "stack"
    assert "_subgrid" not in json.dumps(spec)


def test_render_only_uses_documented_components(iron_condor):
    spec = render(iron_condor, None)
    emitted = {c["component"] for c in spec["children"]}
    unknown = emitted - ALLOWED_COMPONENTS
    assert not unknown, f"render emitted undocumented components: {unknown}"


def test_render_alert_banner_fires_on_delta_hedge(short_strangle):
    spec = render(short_strangle, None)
    banners = [c for c in spec["children"] if c["component"] == "alert_banner"]
    assert banners, "short_strangle has delta hedge enabled but no banner was emitted"
    assert "delta hedge" in banners[0]["props"]["message"].lower()


def test_render_no_banner_when_nothing_to_warn(iron_condor):
    spec = render(iron_condor, None)
    banners = [c for c in spec["children"] if c["component"] == "alert_banner"]
    assert banners == []


def test_render_full_layout_adds_kpi_and_equity(iron_condor):
    bt_path = OUT / "iron_condor_btc.bt.json"
    if not bt_path.exists():
        pytest.skip("backtest fixture not generated")
    bt = json.loads(bt_path.read_text())
    spec = render(iron_condor, bt, layout="full")
    comps = [c["component"] for c in spec["children"]]
    assert "metric_card" in comps
    assert "performance_chart" in comps
    assert comps.count("performance_chart") >= 2


def test_render_includes_greeks_table(iron_condor):
    spec = render(iron_condor, None)
    greeks = [c for c in spec["children"] if c["component"] == "data_table"
              and "Greeks" in c["props"]["columns"][0]["header"]]
    assert len(greeks) == 1
    rows = greeks[0]["props"]["rows"]
    assert len(rows) == len(iron_condor["legs"]) + 1
    assert rows[-1]["leg"] == "Σ portfolio"


def test_render_lists_all_legs(iron_condor):
    spec = render(iron_condor, None)
    legs_table = next(c for c in spec["children"] if c["component"] == "data_table"
                      and c["props"]["columns"][0]["key"] == "side")
    assert len(legs_table["props"]["rows"]) == len(iron_condor["legs"])
