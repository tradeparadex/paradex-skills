import json
from pathlib import Path

import pytest

from strategy_viz.blocks import CATALOG, LAYOUTS, render

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
OUT = Path(__file__).resolve().parent.parent / "out"
ALLOWED = {"alert_banner", "metric_card", "labeled_output",
           "performance_chart", "positions_table", "data_table", "markdown"}


@pytest.fixture
def iron_condor():
    return json.loads((SAMPLES / "iron_condor_btc.json").read_text())


@pytest.fixture
def short_strangle():
    return json.loads((SAMPLES / "short_strangle_eth.json").read_text())


# Every block reachable as a callable with the documented signature.
@pytest.mark.parametrize("block_id", sorted(CATALOG.keys()))
def test_block_callable_returns_list(block_id, iron_condor):
    out = CATALOG[block_id](iron_condor, None)
    assert isinstance(out, list)
    for child in out:
        assert "component" in child
        assert child["component"] in ALLOWED


# Blocks must not throw on missing data — they should self-skip.
@pytest.mark.parametrize("block_id", sorted(CATALOG.keys()))
def test_block_self_skips_when_data_missing(block_id):
    minimal = {"name": "x"}
    out = CATALOG[block_id](minimal, None)
    assert isinstance(out, list)


# bt_* blocks emit nothing without a backtest fixture.
@pytest.mark.parametrize("block_id", ["bt_heading", "bt_kpis", "bt_equity", "bt_trades"])
def test_bt_blocks_skip_without_backtest(block_id, iron_condor):
    assert CATALOG[block_id](iron_condor, None) == []


def test_risk_banner_fires_on_delta_hedge(short_strangle):
    out = CATALOG["risk_banner"](short_strangle, None)
    assert len(out) == 1
    assert out[0]["component"] == "alert_banner"
    assert "delta hedge" in out[0]["props"]["message"].lower()


def test_risk_banner_silent_otherwise(iron_condor):
    assert CATALOG["risk_banner"](iron_condor, None) == []


# Render dispatch.
def test_render_named_layout(iron_condor):
    spec = render(iron_condor, None, layout="preview")
    assert spec["layout"] == "stack"
    components = {c["component"] for c in spec["children"]}
    assert components.issubset(ALLOWED)
    # header markdown is always first
    assert spec["children"][0]["component"] == "markdown"


def test_render_unknown_layout_raises(iron_condor):
    with pytest.raises(KeyError):
        render(iron_condor, None, layout="does-not-exist")


def test_render_unknown_block_raises(iron_condor):
    with pytest.raises(KeyError) as exc:
        render(iron_condor, None, layout=["header", "frobnicator"])
    assert "frobnicator" in str(exc.value)


def test_render_custom_block_list(iron_condor):
    spec = render(iron_condor, None, layout=["payoff", "greeks"])
    comps = [c["component"] for c in spec["children"]]
    # payoff emits markdown + performance_chart; greeks emits data_table
    assert "performance_chart" in comps
    assert "data_table" in comps
    assert "labeled_output" not in comps  # header NOT included


def test_render_full_layout_with_backtest(iron_condor):
    bt_path = OUT / "iron_condor_btc.bt.json"
    if not bt_path.exists():
        pytest.skip("backtest fixture not present")
    bt = json.loads(bt_path.read_text())
    spec = render(iron_condor, bt, layout="full")
    comps = [c["component"] for c in spec["children"]]
    assert "metric_card" in comps          # bt_kpis present
    assert comps.count("performance_chart") >= 2  # equity + payoff


def test_layouts_only_reference_known_blocks():
    for name, ids in LAYOUTS.items():
        unknown = [b for b in ids if b not in CATALOG]
        assert not unknown, f"layout {name} references unknown blocks: {unknown}"


def test_legs_only_layout_is_compact(iron_condor):
    spec = render(iron_condor, None, layout="legs_only")
    comps = [c["component"] for c in spec["children"]]
    assert comps == ["markdown", "labeled_output", "labeled_output",
                     "labeled_output", "labeled_output", "data_table"]


def test_blocks_are_pure_functions(iron_condor):
    """Same input → same output. Blocks should never mutate `strat` or `bt`."""
    snapshot = json.dumps(iron_condor, sort_keys=True)
    for bid in CATALOG:
        CATALOG[bid](iron_condor, None)
    assert json.dumps(iron_condor, sort_keys=True) == snapshot
