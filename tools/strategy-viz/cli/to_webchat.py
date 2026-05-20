"""CLI: strategy (+ optional backtest) JSON → webchat-renderer spec.

Examples:
    # preview layout
    python3 cli/to_webchat.py samples/iron_condor_btc.json out/x.webchat.json

    # full layout (auto-promoted when --backtest is supplied)
    python3 cli/to_webchat.py --backtest out/iron_condor_btc.bt.json \\
        samples/iron_condor_btc.json out/x.webchat.json

    # ad-hoc composition from explicit block ids
    python3 cli/to_webchat.py --blocks header,legs,greeks \\
        samples/iron_condor_btc.json out/x.webchat.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from strategy_viz.blocks import LAYOUTS, render
from strategy_viz.common import ensure_parent


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compose a strategy summary as a webchat-renderer spec.")
    ap.add_argument("strategy", help="Strategy JSON")
    ap.add_argument("out", help="Output JSON")
    ap.add_argument("--backtest", help="Optional backtest results JSON")
    ap.add_argument("--layout", default="preview",
                    help=f"Named layout. One of: {', '.join(sorted(LAYOUTS))}")
    ap.add_argument("--blocks", help="Comma-separated block ids; overrides --layout")
    args = ap.parse_args()

    strat = json.loads(Path(args.strategy).read_text())
    if "evaluators" in strat:
        print("listener-form not supported by this composer (no positions/payoff)",
              file=sys.stderr)
        return
    bt = json.loads(Path(args.backtest).read_text()) if args.backtest else None
    layout: str | list[str]
    if args.blocks:
        layout = args.blocks.split(",")
    elif bt and args.layout == "preview":
        layout = "full"
    else:
        layout = args.layout
    spec = render(strat, bt, layout=layout)
    ensure_parent(Path(args.out)).write_text(json.dumps(spec, indent=2))


if __name__ == "__main__":
    main()
