# Strategy summary blocks

Composable building blocks for emitting a strategy summary into the
`webchat-ui-renderer`. Defined in `strategy_viz/blocks.py`; one block per visual concept.

The shape is intentionally tiny:

```python
def some_block(strat, bt) -> list[dict]:
    """Return zero or more webchat primitives. Never raises."""
```

That's it. A block is a pure function from `(strat, bt)` to a flat list of
`webchat-ui-renderer` primitive specs. Missing data → empty list. No
state, no DOM, no rendering — just the typed spec.

## Why blocks

Before this catalog existed, the strategy card was three different
hard-coded layouts in three files (matplotlib axes, HTML template,
webchat compose). Re-ordering sections meant editing three places.
"Just show me the legs" meant copy-pasting the legs construction.

After: one catalog, one ordered list of block IDs per layout. To send
"just the legs and the Greeks" in chat, you do:

```python
from strategy_viz.blocks import render
spec = render(strat, bt=None, layout=["legs", "greeks"])
```

## Catalog

| Block id | Emits | Notes |
|---|---|---|
| `header` | markdown (name + thesis) + 4 labeled_outputs | always present |
| `legs` | data_table | one row per leg |
| `entry` | data_table | gate mode in header, "✓ enabled" status per row |
| `exit` | data_table | adds EXPIRY override row when any option leg present |
| `risk_banner` | alert_banner | fires on delta-hedge, IMR ceiling > 70%, backtest min-DTL < 5%, or max-DD > 25%; silent otherwise |
| `greeks` | data_table | per-leg Δ/Γ/Vega/Θ at entry + portfolio Σ row |
| `payoff` | markdown + performance_chart | net curve at expiry, assumed IV 60% |
| `bt_heading` | markdown | `### Backtest results` — only if backtest present |
| `bt_kpis` | 4 metric_cards | total return / Sharpe / max DD / win % |
| `bt_equity` | performance_chart | downsampled equity curve |
| `bt_trades` | data_table | first 30 cycles |

All `bt_*` blocks self-skip when `bt is None`. All blocks self-skip on
missing fields. Blocks must not mutate `strat` or `bt` (covered by
`tests/test_blocks.py::test_blocks_are_pure_functions`).

## Layouts

Named layouts in `LAYOUTS`:

| Name | Block sequence | Use |
|---|---|---|
| `preview` | header → legs → entry → exit → risk_banner → greeks → payoff | strategy summary before placing |
| `full` | preview + bt_heading + bt_kpis + bt_equity + bt_trades | full tear sheet with backtest |
| `legs_only` | header + legs | "show me the structure" |
| `payoff_only` | payoff + greeks | "what's the economic shape" |
| `rules_only` | entry + exit + risk_banner | "what are the gates" |
| `backtest_only` | bt_heading + bt_kpis + bt_equity + bt_trades | "how did it perform" |

Pass a layout name *or* a literal block-id list to `render()`.

## Adding a new block

1. Write the function in `strategy_viz/blocks.py` taking `(strat, bt)` and returning a
   `list[dict]` of primitive specs.
2. Add it to `CATALOG`.
3. Decide which layouts it belongs in (most blocks are in `preview` and
   `full` only).
4. Add at minimum two tests in `tests/test_blocks.py`: that the block
   self-skips on minimal input, and that its emissions only use the seven
   documented primitives (covered automatically by the parametrized
   smoke tests in `test_blocks.py`).

A block that doesn't fit the "list of primitives" shape probably isn't a
block — it's a layout decision (containers, grids) and belongs elsewhere.
The webchat renderer's container schema is one stack/grid per message;
nested containers aren't supported, so blocks emit flat lists.

## Non-goals

- Matplotlib backend for blocks. The strategy card PNG
  (`render_strategy_card.py`) stays as a one-off polished artifact —
  matplotlib layouts are 2D and don't compose linearly the way the
  webchat stack does. If we ever need this, blocks would need to declare
  `(width, height_rows)` and a renderer would do grid packing.
- Browser-side block catalog. The browser's `index.html` Overview tab
  could consume the same JSON catalog, but the perf cost of round-tripping
  through Python isn't worth it for the interactive editor. The JS path
  duplicates the layout intent, not the block logic.
