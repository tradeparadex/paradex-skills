# Strategy visualization

Tooling that turns a Paradex strategy JSON (the format consumed by
`skills/strategy-backtester/` and `skills/strategy-listener/`) into a set of
visual artifacts: flowcharts, payoff diagrams, equity-curve tear sheets, and
webchat-renderer payloads.

The strategy-card layout, KPI selection, and tear-sheet sections follow widely
used quant conventions — specifically:

- **pyfolio / quantstats tear sheets**: equity curve + drawdown band stacked on
  a shared x-axis, monthly returns heatmap, rolling Sharpe, exit-reason breakdown
  ([pyfolio][pyfolio], [quantstats][quantstats]).
- **Quantopian / private-fund tear sheet design**: a single-page summary
  skimmable in under a minute — strategy name + thesis, period, hero KPIs,
  performance plots, then risk and trade-quality drilldowns
  ([Carta][carta], [Waveup][waveup], [Visible.vc][visible]).
- **Backtest KPIs**: total return, Sharpe, max drawdown, win rate, expectancy,
  profit factor ([FX Replay][fxreplay], [QuantifiedStrategies][qstrat]).
- **Options-platform "command center" conventions**: payoff diagram at expiry
  with break-even markers, Greeks summary, capital-at-risk, per-leg
  contribution ([TradesViz options command center][tradesviz],
  [OptionAlpha payoff diagrams][optionalpha], [TradingView strategy
  builder][tradingview]).

Mermaid round-trip is **not** supported on purpose — mermaid is a layout DSL,
not an interchange format. JSON stays canonical; validation uses
`strategy.schema.json` (derived from
`skills/strategy-backtester/references/grammar.md`).

## Files

| File | Purpose |
|---|---|
| `to_mermaid.py` | strategy JSON → mermaid `.mmd` flowchart (backtester or listener form) |
| `render_payoff.py` | matplotlib payoff card; `--backtest` overlays realized trade outcomes |
| `render_backtest.py` | equity curve + drawdown band + cycle log + metrics card |
| `render_strategy_card.py` | unified tear-sheet card: header · KPI strip · equity & drawdown · payoff · rules · monthly heatmap · exit-reason breakdown. Works with or without a backtest fixture. |
| `to_webchat.py` | strategy → composed `webchat-ui-renderer` JSON spec (7 primitives only, no custom components) |
| `gen_synthetic_backtest.py` | produce a plausible synthetic backtest results fixture (no live API needed) |
| `index.html` | in-browser editor with tabs (Overview / Flowchart / Payoff / Backtest / Webchat / Mermaid) and JSON-schema validation |
| `strategy.schema.json` | JSON Schema derived from `grammar.md` |
| `samples/*.json` | strategy fixtures (backtester + listener form) |
| `out/*` | generated artifacts (`.mmd`, `.svg`, `.png`, `.webchat.json`, `.bt.json`) |

## Strategy card design

Layout (14×10 in PNG, designed to be skimmed in ~30 s):

```
┌─ HEADER (name · thesis · asset · capital · period · margin) ─────────┐
├─ KPI STRIP (6 tiles: total return · Sharpe · max DD · win % · cycles · capital deployed)
├─ EQUITY CURVE + DRAWDOWN BAND ──┬─ PAYOFF AT EXPIRY (with BE markers)─┤
│                                 ├─ LEGS · ENTRY · EXIT (rules card) ─┤
├─ MONTHLY RETURNS HEATMAP ───────┴─ EXIT-REASON BREAKDOWN ────────────┤
└──────────────────────────────────────────────────────────────────────┘
```

Without a backtest the equity / drawdown / monthly heatmap / exit-reason
panels are omitted; header, KPI strip placeholders, payoff, and rules card
still render — useful as a pre-trade preview.

KPI tiles use a left-edge color stripe (green / amber / red) per threshold
rather than colored backgrounds, so the card stays readable in monochrome
prints.

## Webchat composition

`to_webchat.py` is a thin CLI over `blocks.render()`. The strategy
summary is assembled from a small **block catalog** — one function per
visual concept, each returning a list of `webchat-ui-renderer` primitives.
A "card" is just an ordered list of block IDs.

```python
from blocks import render

# preset layout
spec = render(strat, bt, layout="full")        # tear-sheet with backtest
spec = render(strat, None, layout="preview")   # pre-trade preview

# or compose ad-hoc
spec = render(strat, None, layout=["legs", "greeks"])    # just structure
spec = render(strat, None, layout=["entry", "exit"])     # just gates
```

CLI:

```bash
python3 to_webchat.py --layout payoff_only samples/iron_condor_btc.json out/x.json
python3 to_webchat.py --blocks header,legs,greeks samples/iron_condor_btc.json out/x.json
```

Full catalog and preset layouts: [`docs/blocks-catalog.md`](docs/blocks-catalog.md).
Only the 7 primitives from `skills/webchat-ui-renderer/` are used — no
custom components — and the composer always returns a single stack spec
to match the renderer's "one JSON object per message" contract.

## Interactive views

- **`index.html`** — tabbed editor (Overview · Flowchart · Payoff · **Plotly** · **Greeks** · Backtest · Webchat spec · Mermaid src). The Plotly tab renders per-leg traces + a draggable spot cursor; the Greeks tab shows per-leg Δ/Γ/Vega/Θ at entry. Run via `python3 -m http.server` from this directory.
- **`plotly_payoff.html`** — standalone Plotly view of one strategy with a draggable spot line and a live per-leg/portfolio Greeks table that updates as you drag.
- **`diff.html`** — side-by-side strategy diff. Header, legs, entry, and exit panels with add/remove/change highlighting + a payoff overlay (both nets on one Plotly chart).

## Plotly + React integration

Yes, Plotly works in React. The official wrapper is [`react-plotly.js`](https://github.com/plotly/react-plotly.js) (MIT, by Plotly).

```jsx
import Plot from "react-plotly.js";
<Plot data={traces} layout={layout} config={{responsive: true, displaylogo: false}} />
```

Notes:
- **Bundle size** is the gotcha — full Plotly is ~3.5 MB. Use [partial bundles](https://github.com/plotly/plotly.js/tree/master/dist) (`plotly.js-basic-dist` ≈ 700 KB or `plotly.js-finance-dist` ≈ 1 MB) for chart subsets, or CDN-load if the host page allows it.
- **SSR**: import dynamically (`next/dynamic` with `ssr: false`) — Plotly needs `window` to construct charts.
- **Safety**: Plotly does not `eval` any user input. As long as you don't pass user-controlled HTML into `text`/`title`/`hovertemplate` (which Plotly will render through), there's no XSS surface. Treat strings the same way you'd treat any `dangerouslySetInnerHTML` source.
- **Editable shapes** (the draggable spot cursor in our payoff view) are supported via `layout.shapes[].editable: true`; subscribe to `plotly_relayout` to react to drags.

## Code reuse / structure

| Layer | Python | Browser |
|---|---|---|
| Pricing math (BS, strikes, Greeks, payoffs) | `_pricing.py` | `_shared.js` (SViz namespace) |
| Text labels (entry/exit rows, leg labels, thesis) | `_specs.py` | `_shared.js` |
| Constants / lookups (reasons, gate modes, hours) | `_common.py` | `_shared.js` |
| Mermaid generation | `to_mermaid.py` | inline in `index.html` |
| Webchat composition | `to_webchat.py` | inline in `index.html` |
| Matplotlib renderers | `render_*.py` | — |
| Plotly + diff views | — | `plotly_payoff.html`, `diff.html`, `index.html` |

JS↔Python parity is intentional — the browser can't reach a Python kernel — but kept to two definitions per concept (one in `_*.py`, one in `_shared.js`), not the four it started at. The pricing math agrees to ≥3 decimals; the BS implementations use different `erf` strategies (Python uses `math.erf`; JS uses the Abramowitz-Stegun polynomial).

## Adding a new webchat-ui-renderer primitive?

If the host webchat is willing to take one new component, **`flowchart` (mermaid-based) is the higher-leverage pick** over `interactive_chart` (Plotly):

- Mermaid fills a real gap — no existing primitive can render a process / state / sequence / ER diagram. The strategy flowchart is one use case; order lifecycles, vault flows, settlement steps, fee waterfalls are others.
- Bundle is smaller (~1 MB vs ~3.5 MB full Plotly / ~700 KB basic-dist).
- Static SVG output — no interactivity surface to manage, easy to cache.
- Mermaid's `flowchart`, `sequenceDiagram`, `stateDiagram`, `erDiagram`, `gantt` cover most diagram needs from one library.

Plotly's value is "upgrade `performance_chart`" — useful but partially redundant. If that's the direction, prefer `plotly.js-basic-dist` or `plotly.js-finance-dist` over the full bundle.

## Dependencies

- **Python ≥ 3.10** with `numpy` and `matplotlib` — required for the matplotlib renderers (`render_payoff.py`, `render_backtest.py`, `render_strategy_card.py`) and for `gen_synthetic_backtest.py`.
- `to_mermaid.py` and `to_webchat.py` are dependency-light — they import only from the local `_common` / `_pricing` / `_specs` helpers (pure Python). You can run them without numpy or matplotlib installed.
- **Node** with `npx -p @mermaid-js/mermaid-cli` available — required only when rendering `.mmd` → `.svg`/`.png` via `mmdc`. The browser-side `index.html` loads mermaid.js + ajv from a CDN.

Install Python deps with `pip install numpy matplotlib`. For tests add `pip install pytest jsonschema`.

## Tests

```bash
cd tools/strategy-viz
python3 -m pytest tests/ -v
```

The suite covers `_common`, `_pricing`, `_specs`, the mermaid converter, the webchat composer (including contract guarantees: single stack spec, only documented components, alert banner triggers correctly), and `strategy.schema.json` (every sample validates; missing-op / wrong-op / value-only / min-mode-without-gateMin cases are all rejected).

## Quick start

```bash
# Mermaid flowchart
python3 to_mermaid.py samples/iron_condor_btc.json out/iron_condor_btc.mmd
npx -p @mermaid-js/mermaid-cli mmdc -i out/iron_condor_btc.mmd \
    -o out/iron_condor_btc.svg -p puppeteer.json --quiet

# Strategy card (no backtest)
python3 render_strategy_card.py samples/iron_condor_btc.json \
    out/iron_condor_btc_strategy_card_nobt.png

# Strategy card with backtest results
python3 gen_synthetic_backtest.py samples/iron_condor_btc.json \
    out/iron_condor_btc.bt.json
python3 render_strategy_card.py samples/iron_condor_btc.json \
    out/iron_condor_btc.bt.json out/iron_condor_btc_strategy_card.png

# Webchat composition
python3 to_webchat.py --backtest out/iron_condor_btc.bt.json \
    samples/iron_condor_btc.json out/iron_condor_btc.webchat.json

# Interactive page
cd tools/strategy-viz && python3 -m http.server 8000
# → http://localhost:8000/index.html
```

The synthetic-backtest path is the development fixture; replace
`gen_synthetic_backtest.py` with the real engine
(`skills/strategy-backtester/scripts/paradex_backtest_engine.py --output`)
when running against live data.

[pyfolio]: https://www.quantrocket.com/codeload/quant-finance-lectures/quant_finance_lectures/Lecture33-Portfolio-Analysis-with-pyfolio.ipynb.html
[quantstats]: https://github.com/ranaroussi/quantstats
[carta]: https://carta.com/learn/private-funds/management/portfolio-management/tear-sheets/
[waveup]: https://waveup.com/blog/tear-sheet-examples/
[visible]: https://visible.vc/blog/tear-sheets/
[fxreplay]: https://fxreplay.com/learn/the-5-kpis-that-matter-most-in-backtesting-a-strategy
[qstrat]: https://www.quantifiedstrategies.com/trading-performance/
[tradesviz]: https://www.tradesviz.com/blog/options-command-center/
[optionalpha]: https://optionalpha.com/blog/option-payoff-diagram
[tradingview]: https://www.tradingview.com/support/solutions/43000707214-options-strategy-builder-overview/
