"""CLI: strategy JSON (+ optional backtest) → tear-sheet card PNG."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from strategy_viz.renderers.strategy_card import render


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: render_strategy_card.py <strategy.json> [<backtest.json>] <out.png>",
              file=sys.stderr)
        sys.exit(2)
    strategy_path = Path(sys.argv[1])
    if len(sys.argv) == 3:
        bt_path = None
        out_path = Path(sys.argv[2])
    else:
        bt_path = Path(sys.argv[2])
        out_path = Path(sys.argv[3])
    strat = json.loads(strategy_path.read_text())
    if "evaluators" in strat:
        print("listener-form strategy has no payoff/backtest; skipping", file=sys.stderr)
        sys.exit(0)
    bt = json.loads(bt_path.read_text()) if bt_path else None
    render(strat, bt, out_path)


if __name__ == "__main__":
    main()
