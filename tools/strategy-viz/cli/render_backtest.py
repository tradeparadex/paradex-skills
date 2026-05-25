"""CLI: backtester results JSON → equity / drawdown / cycle log PNG."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from strategy_viz.renderers.backtest import render


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: render_backtest.py <results.json> <out.png>", file=sys.stderr)
        sys.exit(2)
    bt = json.loads(Path(sys.argv[1]).read_text())
    render(bt, Path(sys.argv[2]), name=bt.get("_strategy_name", ""))


if __name__ == "__main__":
    main()
