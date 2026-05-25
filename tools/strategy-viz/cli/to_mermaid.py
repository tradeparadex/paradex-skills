"""CLI: strategy JSON → mermaid flowchart source.

Usage:
    python3 cli/to_mermaid.py samples/iron_condor_btc.json out/iron_condor_btc.mmd
    python3 cli/to_mermaid.py samples/iron_condor_btc.json          # → stdout
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from strategy_viz.common import ensure_parent
from strategy_viz.mermaid import convert


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: to_mermaid.py <strategy.json> [out.mmd]", file=sys.stderr)
        sys.exit(2)
    strat = json.loads(Path(sys.argv[1]).read_text())
    mmd, _ = convert(strat)
    if len(sys.argv) >= 3:
        ensure_parent(Path(sys.argv[2])).write_text(mmd + "\n")
    else:
        print(mmd)


if __name__ == "__main__":
    main()
