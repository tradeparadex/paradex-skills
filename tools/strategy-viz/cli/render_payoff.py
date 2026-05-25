"""CLI: strategy JSON (+ optional backtest) → payoff card PNG."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from strategy_viz.renderers.payoff import render


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a strategy payoff card.")
    ap.add_argument("strategy", help="Path to strategy JSON")
    ap.add_argument("out", help="Output PNG path")
    ap.add_argument("--backtest", help="Optional backtest results JSON to overlay")
    args = ap.parse_args()

    strat = json.loads(Path(args.strategy).read_text())
    if "evaluators" in strat:
        print("skipping listener-form strategy (no payoff)", file=sys.stderr)
        sys.exit(0)
    bt = json.loads(Path(args.backtest).read_text()) if args.backtest else None
    render(strat, Path(args.out), backtest=bt)


if __name__ == "__main__":
    main()
