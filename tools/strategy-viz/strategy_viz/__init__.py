"""Paradex strategy visualization library.

Public API — embed in other Python apps to compose webchat-renderer specs,
generate mermaid diagrams, or compute pricing / Greeks from strategy JSON.

Stable surface:

    from strategy_viz import blocks, mermaid, pricing, specs, common

    blocks.render(strat, bt, layout)               # → one webchat-renderer stack spec
    mermaid.convert(strat)                         # → (mmd source, name) dispatch
    mermaid.backtester_to_mermaid(strat)           # backtester-form only
    mermaid.listener_to_mermaid(strat)             # listener-form only
    pricing.bs_greeks(S, K, T, sigma, opt)         # delta / gamma / vega / theta
    pricing.leg_greeks_at_entry(leg)               # signed, size-weighted
    pricing.portfolio_greeks(legs)                 # Σ across legs
    pricing.payoff_curve(legs, n_points)           # (spots, pnl) sampled
    specs.thesis(strat)                            # one-line description
    specs.entry_lines(entry) / specs.exit_lines(exit_)

Optional matplotlib renderers under `strategy_viz.renderers`:

    from strategy_viz.renderers import payoff, backtest, strategy_card
    payoff.render(strat, out_path, backtest=...)
    strategy_card.render(strat, bt, out_path)
"""
from . import blocks, common, mermaid, pricing, specs

__all__ = ["blocks", "common", "mermaid", "pricing", "specs"]
