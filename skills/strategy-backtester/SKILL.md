---
name: paradex-strategy-backtester
description: >
  Translates options strategy ideas into importable JSON for the Paradex Strategy Backtester
  HTML tool or runs the backtester directly via a Python CLI script. Integrates with
  paradex-options-pricer and paradex-pm-analyzer to seed realistic entry conditions and
  validate margin. Use whenever the user wants to backtest an options strategy, says "run a
  backtest", "test this strategy historically", "how would X perform over the last N months",
  "simulate iron condor / strangle / straddle / condor / butterfly / collar / calendar",
  "generate a backtest config", "import into backtester", "what entry conditions should I use",
  "interpret my backtest results", "run the Python backtester", "uv run backtest", or shares
  metrics like Sharpe, max drawdown, win rate from a prior run. Also trigger when the user is
  in the middle of a paradex-strategy-builder conversation and asks to validate the idea with
  historical data — hand off the strategy spec to the backtester JSON format.
compatibility: Requires Paradex MCP server (mcp-paradex-py) for seeding live market data. Python script requires uv (no browser needed).
metadata:
  author: tradeparadex
  version: "1.3"
---

# Paradex Strategy Backtester

Bridges natural-language strategy ideas and the Paradex Strategy Backtester — a browser HTML
tool (`strategy_backtester.html`) **and** a Python CLI script that can run the same simulation
headlessly on low-resource machines.

## Python Script (recommended for automation)

`scripts/paradex_backtest_engine.py` is a complete port of the JS engine from the HTML tool.
It requires only `uv` — no browser, no heavy dependencies.

```bash
# Basic run — reads date range from strategy JSON
uv run scripts/paradex_backtest_engine.py strategy.json

# Override dates
uv run scripts/paradex_backtest_engine.py strategy.json --start 2025-01-01 --end 2026-04-27

# Deribit CSV as data source (longer history)
uv run scripts/paradex_backtest_engine.py strategy.json --deribit data.csv

# Save full results (equity curve, trades, metrics)
uv run scripts/paradex_backtest_engine.py strategy.json --output results.json

# Testnet API
uv run scripts/paradex_backtest_engine.py strategy.json --testnet

# Skip margin/liquidation computation (faster, no DTL output)
uv run scripts/paradex_backtest_engine.py strategy.json --no-margin

# Cache fetched data locally for reuse across runs
uv run scripts/paradex_backtest_engine.py strategy.json --cache-dir ~/.paradex_cache

# Hard timeout (exit with error after N seconds)
uv run scripts/paradex_backtest_engine.py strategy.json --timeout 120
```

No auth required — all data endpoints are public. The script prints a metrics summary and
a trade log. Use `--output` to save the full equity curve and trade tape as JSON.

## HTML Tool

Open `strategy_backtester.html` in a browser. Use **↑ Import Strategy** to load a strategy
JSON, set the date range and data source, then click **▶ Run Backtest**. Supports a visual
equity chart, drawdown chart, IV overlay, and trade log table.

---

## Your Role

1. **Generate backtester JSON** from a natural-language strategy description
2. **Seed entry conditions** using live IV and market data from MCP tools
3. **Interpret results** when the user shares metrics from a completed backtest
4. **Integrate** with `paradex-options-pricer` (for IV/strike data) and `paradex-pm-analyzer` (for margin mode/IMR)

For template strategies and regime guidance, read [references/templates.md](references/templates.md).
For the complete field-level grammar (all operators, valid values, defaults), read [references/grammar.md](references/grammar.md).

---

## Workflow A: Strategy → Backtester JSON

If the user brings a text spec from `paradex-strategy-builder` (e.g., "sell 25Δ strangle when IVP > 60"), translate their conditions directly into JSON without asking them to re-explain the strategy.

### Step 1: Clarify the strategy

Extract:
- **Underlying**: BTC / ETH / SOL
- **Structure**: from the template catalogue or custom legs
- **Capital**: starting equity in USD (default $100K)
- **DTE target**: days to expiry for each leg (7, 14, 30, 60, 90)
- **Strike mode**: by delta (e.g. 25Δ), ATM, or % OTM
- **Margin mode**: XM (cross margin) or PM (portfolio margin)
- **Delta hedge**: enabled? band (default 0.1 = 10% of option notional)

If the user hasn't specified, propose sensible defaults and ask for confirmation.

### Step 2: Seed entry conditions from live market data

Before generating the JSON, pull current market state to set realistic entry thresholds:

```
Call paradex_market_summaries for the underlying perp (e.g. BTC-USD-PERP):
  → underlying_price: current spot
  → funding_rate: current 8h funding rate

For IV conditions — use paradex-options-pricer (or paradex_market_summaries on option markets):
  → ATM mark_iv for the target DTE term → determines whether IVP entry makes sense
```

If the strategy is "sell when IV is high" → enable `entryIVPctile` with a meaningful threshold
(e.g. IVP > 60% over 30d). If no strong IV view, leave disabled.

### Step 3: Check margin mode

If the user hasn't specified XM vs PM, call `paradex-pm-analyzer` logic (or ask the user).
PM mode reduces margin significantly for hedged multi-leg structures.

### Step 4: Generate the JSON

Produce a complete strategy JSON object. Tell the user to either:
- **Python**: `uv run scripts/paradex_backtest_engine.py strategy.json --start ... --end ...`
- **HTML**: Open `strategy_backtester.html` → click **↑ Import Strategy** → select the file → run

---

## Strategy JSON Schema

```json
{
  "name": "short_strangle_14d_25d",
  "underlying": "BTC",
  "capital": 100000,
  "atmIvTermDays": 7,
  "riskFreeRate": 0.05,
  "marginMode": "PM",
  "maxImrPctEntry": 70,
  "deltaHedge": { "enabled": false, "band": 0.1 },
  "legs": [ /* see Leg Schema */ ],
  "entry": { /* see Entry Schema */ },
  "exit": { /* see Exit Schema */ },
  "backtest": {
    "startDate": "2026-01-01",
    "endDate": "2026-04-27"
  }
}
```

### Leg Schema

```json
{
  "type": "option",          // "option" | "perp"
  "side": "SELL",            // "BUY" | "SELL"
  "optionType": "PUT",       // "CALL" | "PUT"  (ignored for perp)
  "strikeMode": "delta",     // "delta" | "atm" | "otm_pct"
  "strikeParam": 0.25,       // delta (0–1), or % OTM fraction
  "dteTarget": 14,           // days to expiry at entry
  "size": 1.0,               // contracts (or % of capital if sizeMode=pct_capital)
  "sizeMode": "contracts"    // "contracts" | "pct_capital"
}
```

Perp leg: same schema, `type: "perp"`, `size` = number of base units (e.g. 0.1 BTC).

### Entry Schema

```json
{
  "frequency": 168,           // hours between rebalance attempts
  "gateMode": "all",          // "all" | "any" | "min"
  "gateMin": 2,
  "rvPctile":    { "enabled": false, "op": ">", "value": 50, "window": 168 },
  "ivPctile":    { "enabled": false, "op": ">", "value": 50, "window": 720 },
  "rsi":         { "enabled": false, "op": "<", "value": 70 },
  "sma":         { "enabled": false, "op": "above", "period": 168 },
  "fundingRate": { "enabled": false, "op": ">", "value": 0.01 }
}
```

`window` values in hours: 24=1d, 168=7d, 336=14d, 720=30d, 1440=60d, 2160=90d.

### Exit Schema

```json
{
  "gateMode": "any",
  "gateMin": 2,
  "profitTarget": { "enabled": true,  "value": 25  },
  "stopLoss":     { "enabled": true,  "value": 100 },
  "ivPctile":     { "enabled": false, "op": ">", "value": 80, "window": 720 },
  "dteFloor":     { "enabled": true,  "value": 1  },
  "maxHold":      { "enabled": false, "value": 336 },
  "distToLiq":    { "enabled": false, "value": 10  }
}
```

EXPIRY (DTE=0) always closes positions regardless of gateMode.

---

## Workflow B: Interpret Backtest Results

When the user shares results, provide:

### 1. Headline assessment
- **Sharpe**: < 0 = bad; 0–1 = acceptable; > 1 = good; > 2 = excellent for options
- **Max Drawdown**: short premium targets < 15%; > 25% needs attention
- **Win Rate**: short premium 60–75%; long vol 30–45%
- **Holding %**: < 10% → entry conditions too restrictive; > 90% → no idle capital buffer

### 2. Regime correlation
Pull data to explain *why* results look the way they do. Use `paradex_klines` to check
whether the spot was trending or range-bound during the backtest window.

### 3. Parameter suggestions
- **Low win rate for short premium**: tighten profit target (25% → 15%), add IVP > 60% entry filter
- **Large single losses**: enable stop loss; shorter DTE reduces gamma exposure
- **High holding %**: loosen entry conditions or increase rebalance frequency
- **Negative Sharpe**: wrong regime — consider the opposite structure

### 4. Cross-skill handoff
- Check current margin impact: run `paradex-pm-analyzer`
- Find better strikes: run `paradex-options-pricer`
- Build execution plan: hand to `paradex-strategy-builder`

---

## Integration Patterns

### Before backtesting (seed live data)

```
1. paradex-options-pricer → ATM IV for target term
   → if IV > 60th percentile over 30d: set entryIVPctile enabled=true, op=">", value=60
   → if IV < 40th percentile: set entryIVPctile enabled=true, op="<", value=40

2. paradex_funding_data → current 8h funding rate
   → if rate > 0.03%/8h: enable fundingRate filter for perp-hedged strategies

3. paradex-pm-analyzer → confirm margin mode (XM vs PM)
   → set "marginMode" in JSON to match account's actual methodology
```

### After backtesting (interpret and act)

```
Sharpe > 1, consistent win rate → strategy has edge
Sharpe < 0 → wrong regime or parameters; check with paradex-strategy-builder
High drawdown (>30%) → reduce size or add distToLiq exit
Low holding % (<10%) → entry conditions too restrictive; loosen filters
```

---

## Data Source Guidance

| Source | When to use | Notes |
|---|---|---|
| **Paradex API** | Testing on Paradex-native data (recent weeks) | Options data limited to when markets launched |
| **Deribit CSV** | Longer BTC/ETH history (months–years) | Export from Deribit historical data page; use `--deribit` flag |
| **Both (HTML only)** | Cross-venue IV validation | HTML tool supports side-by-side comparison |

---

## Output Format

When generating a strategy JSON, always:

1. State the structure in plain language
2. Explain each entry condition enabled and why
3. Explain each exit condition and the rationale
4. Provide the complete JSON block (copy-paste ready)
5. Give run instructions for both Python and HTML

When interpreting results, lead with the headline verdict before the detail.

---

## Caveats

- **Approximation, not simulation**: hourly bars, BS pricing, no bid-ask spread, no queue priority
- **Short Paradex history**: Paradex options launched recently; use Deribit CSV for meaningful long-horizon tests
- **PM margin is approximate**: backtester uses cached scenario configs; actual exchange margin may differ
- **IV data gaps**: engine forward-fills using last known IV when snapshots are missing
- **No financial advice**: historical results do not guarantee future returns
