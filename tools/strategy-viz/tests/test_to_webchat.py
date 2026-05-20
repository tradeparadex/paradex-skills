import json
from pathlib import Path

import pytest

from to_webchat import compose

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


def test_compose_returns_single_stack_spec(iron_condor):
    """The webchat renderer parses one JSON object per message — compose must
    never return a list, and the layout must be a single 'stack'."""
    spec = compose(iron_condor, None)
    assert isinstance(spec, dict), "compose returned a list; renderer rejects arrays"
    assert spec["layout"] == "stack"
    assert "_subgrid" not in json.dumps(spec)  # internal marker must not leak


def test_compose_only_uses_documented_components(iron_condor):
    spec = compose(iron_condor, None)
    emitted = {c["component"] for c in spec["children"]}
    unknown = emitted - ALLOWED_COMPONENTS
    assert not unknown, f"compose emitted undocumented components: {unknown}"


def test_compose_alert_banner_fires_on_delta_hedge(short_strangle):
    spec = compose(short_strangle, None)
    banners = [c for c in spec["children"] if c["component"] == "alert_banner"]
    assert banners, "short_strangle has delta hedge enabled but no banner was emitted"
    assert "delta hedge" in banners[0]["props"]["message"].lower()


def test_compose_no_banner_when_nothing_to_warn(iron_condor):
    spec = compose(iron_condor, None)
    banners = [c for c in spec["children"] if c["component"] == "alert_banner"]
    assert banners == []


def test_compose_with_backtest_adds_kpi_and_equity(iron_condor):
    bt_path = OUT / "iron_condor_btc.bt.json"
    if not bt_path.exists():
        pytest.skip("backtest fixture not generated")
    bt = json.loads(bt_path.read_text())
    spec = compose(iron_condor, bt)
    comps = [c["component"] for c in spec["children"]]
    assert "metric_card" in comps           # KPI tiles only appear with backtest
    assert "performance_chart" in comps     # equity + payoff
    assert comps.count("performance_chart") >= 2


def test_compose_includes_greeks_table(iron_condor):
    spec = compose(iron_condor, None)
    greeks = [c for c in spec["children"] if c["component"] == "data_table"
              and "Greeks" in c["props"]["columns"][0]["header"]]
    assert len(greeks) == 1
    rows = greeks[0]["props"]["rows"]
    # one row per leg plus the portfolio total
    assert len(rows) == len(iron_condor["legs"]) + 1
    assert rows[-1]["leg"] == "Σ portfolio"


def test_compose_lists_all_legs(iron_condor):
    spec = compose(iron_condor, None)
    legs_table = next(c for c in spec["children"] if c["component"] == "data_table"
                      and c["props"]["columns"][0]["key"] == "side")
    assert len(legs_table["props"]["rows"]) == len(iron_condor["legs"])
