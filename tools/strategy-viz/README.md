# Strategy visualization

Embeddable library for turning a Paradex strategy JSON (the format consumed
by `skills/strategy-backtester/` and `skills/strategy-listener/`) into:

- A composed **webchat-renderer JSON spec** using only the 7 documented
  primitives — drop straight into a chat message.
- A **mermaid flowchart** source string — render anywhere a mermaid client
  exists.
- **Greeks / payoff math** at any spot, per leg and aggregated.
- Optional static **matplotlib renderers** for tear-sheet PNGs and demo
  HTML pages.

Designed to be **embedded in other apps**: a Python server (skill / agent
runtime) imports the package; a JS/TS client imports the ESM module. The
two implementations are kept in numerical agreement via a parity test.

## Layout

```
tools/strategy-viz/
├── strategy_viz/         ← Python library (importable)
│   ├── __init__.py         re-exports the public API
│   ├── common.py           hrs, gate_label, cycles_from_trades, ensure_parent, REASON_COLORS
│   ├── pricing.py          Black-Scholes, Greeks, strikes, payoff curves
│   ├── specs.py            entry_lines / exit_lines / thesis / expectancy
│   ├── blocks.py           webchat composition: 11 blocks + render(strat, bt, layout)
│   ├── mermaid.py          backtester_to_mermaid / listener_to_mermaid / convert
│   ├── renderers/          matplotlib PNG renderers (require numpy + matplotlib)
│   │   ├── payoff.py         render(strat, out_path, backtest=None)
│   │   ├── backtest.py       render(bt, out_path, name="")
│   │   └── strategy_card.py  render(strat, bt, out_path)
│   └── js/
│       ├── index.mjs        ESM mirror of pricing + specs + common (the JS library)
│       └── package.json
├── cli/                  ← thin argparse wrappers
│   ├── to_mermaid.py
│   ├── to_webchat.py
│   ├── render_payoff.py
│   ├── render_backtest.py
│   ├── render_strategy_card.py
│   └── gen_synthetic_backtest.py
├── demo/                 ← standalone HTML pages (require a static server)
│   ├── index.html
│   ├── diff.html
│   ├── plotly_payoff.html
│   └── puppeteer.json    config for headless mermaid-cli rendering
├── samples/              dev fixtures (9 sample strategies)
├── docs/
│   └── blocks-catalog.md
├── tests/                pytest suite (130 cases including JS↔Python parity)
├── pyproject.toml
├── strategy.schema.json  JSON-Schema for the backtester strategy format
└── README.md
```

The `strategy_viz/` package is the library surface. Everything else
(`cli/`, `demo/`, `samples/`) is dev / demo tooling that can be ignored by
embedders.

## Install (Python)

```bash
pip install -e tools/strategy-viz/                    # library only (stdlib)
pip install -e 'tools/strategy-viz/[renderers]'       # + numpy + matplotlib
pip install -e 'tools/strategy-viz/[dev]'             # + pytest + jsonschema
```

## Install (JS)

The JS library is a plain ESM module — no build step. Vendor it directly:

```bash
cp -r tools/strategy-viz/strategy_viz/js node_modules/@paradex/strategy-viz
```

…or publish to a private npm registry once stable. Then in your webapp:

```js
import { bsGreeks, payoffCurve, legGreeksAtEntry, thesis } from "@paradex/strategy-viz";
```

For React, wrap Plotly calls with `react-plotly.js`; the library itself
has no React dependency.

## Public API

### Python

```python
from strategy_viz import blocks, mermaid, pricing, specs

# Compose a webchat-renderer spec
spec = blocks.render(strat, backtest=None, layout="preview")
spec = blocks.render(strat, backtest, layout="full")
spec = blocks.render(strat, backtest=None, layout=["header", "legs", "greeks"])

# Mermaid flowchart source
mmd, name = mermaid.convert(strat)          # auto-dispatches

# Greeks / payoff math
g = pricing.bs_greeks(S=100, K=100, T=14/365, sigma=0.6, opt="CALL")
pg = pricing.portfolio_greeks(strat["legs"])
spots, net = pricing.payoff_curve(strat["legs"], n_points=80)

# Labels
specs.thesis(strat)                          # one-line description
specs.entry_lines(strat["entry"])            # ["IV pctile > 55 · 30d", …]
```

### JS

```js
import {
  bsGreeks, legGreeksAtEntry, portfolioGreeks, payoffCurve,
  thesis, entryRows, exitRows, hrs, gateLabel
} from "@paradex/strategy-viz";

const greeks = bsGreeks(100, 100, 14 / 365, 0.6, "CALL");
const { spots, net } = payoffCurve(strat.legs, 80);
```

### Choosing the right module

| You want… | Python | JS |
|---|---|---|
| One webchat JSON spec for a strategy | `blocks.render` | (server-side) |
| Mermaid source string | `mermaid.convert` | (server-side) |
| Live per-leg Greeks at any spot | `pricing.leg_greeks_at_entry` | `legGreeksAtEntry` |
| Net payoff curve for a chart | `pricing.payoff_curve` | `payoffCurve` |
| One-line thesis / row labels | `specs.thesis`, `specs.entry_lines` | `thesis`, `entryRows` |
| Tear-sheet PNG | `renderers.strategy_card.render` | — |

## Stability

- Numeric outputs (Greeks, prices, payoffs) agree across Python and JS to
  ≥ 3 decimals. Enforced by `tests/test_parity.py` — divergence fails CI.
- String outputs (thesis, entry/exit lines) match exactly across both
  languages.
- The webchat composer only emits the 7 documented `webchat-ui-renderer`
  primitives. No custom components.
- The composer always returns one stack spec, matching the renderer's
  one-JSON-object-per-message contract.

## Adding a new webchat-ui-renderer primitive?

If the host webchat takes one new component, **mermaid `flowchart` is the
higher-leverage pick** over a Plotly-based `interactive_chart`:

- Mermaid fills a real gap (no current primitive renders process / state /
  sequence / ER diagrams — useful for order lifecycles, vault flows,
  settlement steps, fee waterfalls).
- Bundle is smaller (~1 MB vs ~3.5 MB full Plotly).
- Static SVG output — no interactivity surface to manage.
- A single mermaid library covers `flowchart`, `sequenceDiagram`,
  `stateDiagram`, `erDiagram`, `gantt`.

Plotly is the right pick if the main want is "upgrade `performance_chart`"
(multi-trace, draggable annotations). If you do go that way, prefer
`plotly.js-basic-dist` (~700 KB) over the full bundle.

## Dev / demo tooling

```bash
# Run a sample through every CLI
python3 cli/to_webchat.py --layout full \
    --backtest out/iron_condor_btc.bt.json \
    samples/iron_condor_btc.json out/iron_condor_btc.webchat.json

python3 cli/to_mermaid.py samples/iron_condor_btc.json out/iron_condor_btc.mmd
python3 cli/render_strategy_card.py samples/iron_condor_btc.json \
    out/iron_condor_btc.bt.json out/iron_condor_btc_card.png

# Browse the interactive demos
cd tools/strategy-viz && python3 -m http.server 8000
# → http://localhost:8000/demo/index.html
# → http://localhost:8000/demo/diff.html
# → http://localhost:8000/demo/plotly_payoff.html
```

## Tests

```bash
cd tools/strategy-viz
python3 -m pytest tests/ -v
```

130 cases covering: `common` (hrs, gate_label, cycles), `pricing` (BS
parity, ATM Greeks, strike-from-delta round-trip, perp/option payoff
invariants, signed portfolio Greeks), `specs` (entry/exit lines, thesis,
expectancy), `mermaid` (regression test for the listener-expression label
mangling bug), `blocks` (catalog purity, no-mutation, layout validation),
`schema` (every sample validates, five rejection scenarios), `webchat`
(single-stack-spec contract, allowed components, alert banners), and
**`parity`** (Python vs JS numeric and string agreement across all 7
backtester samples).
