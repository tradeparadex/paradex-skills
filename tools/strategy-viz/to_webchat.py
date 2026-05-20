"""CLI wrapper around `blocks.render` — composes a strategy summary as
a single webchat-renderer stack spec from a strategy JSON (+ optional
backtest results JSON).

Use `blocks.py` directly if you're calling from Python; this module
exists so you can stitch the spec from the shell or a build script.

Examples:
    # Default "preview" layout
    python3 to_webchat.py samples/iron_condor_btc.json out/x.webchat.json

    # Add backtest results — splices in the bt_* blocks
    python3 to_webchat.py --layout full --backtest out/iron_condor_btc.bt.json \\
        samples/iron_condor_btc.json out/x.webchat.json

    # Pick a slim layout
    python3 to_webchat.py --layout payoff_only samples/iron_condor_btc.json out/x.webchat.json

    # Compose ad-hoc from explicit block ids
    python3 to_webchat.py --blocks header,legs,greeks samples/iron_condor_btc.json out/x.webchat.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import ensure_parent
from blocks import LAYOUTS, render


def compose(strat, backtest, spec_id=None, layout=None):
    """Back-compat shim: prior callers imported `compose` directly.
    When `layout` is omitted, picks "full" if a backtest is present and
    "preview" otherwise — matches the old auto-promotion behavior."""
    if layout is None:
        layout = "full" if backtest else "preview"
    return render(strat, backtest, layout=layout, spec_id=spec_id)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Compose a strategy summary as a webchat-renderer spec.")
    ap.add_argument("strategy", help="Strategy JSON")
    ap.add_argument("out", help="Output JSON")
    ap.add_argument("--backtest", help="Optional backtest results JSON")
    ap.add_argument("--layout", default="preview",
                    help=f"Named layout or 'auto'. One of: {', '.join(sorted(LAYOUTS))}")
    ap.add_argument("--blocks", help="Comma-separated block ids; overrides --layout")
    args = ap.parse_args()

    strat = json.loads(Path(args.strategy).read_text())
    if "evaluators" in strat:
        print("listener-form not supported by this composer (no positions/payoff)",
              flush=True)
        return
    bt = json.loads(Path(args.backtest).read_text()) if args.backtest else None
    layout = args.blocks.split(",") if args.blocks else (
        "full" if (bt and args.layout == "preview") else args.layout)
    spec = render(strat, bt, layout=layout)
    ensure_parent(Path(args.out)).write_text(json.dumps(spec, indent=2))


if __name__ == "__main__":
    main()
