---
name: paradex-options-recap
description: >
  Options market recap for a user-specified window, invoked via /recap. Parses
  "/recap [asset] [options] [window]" (e.g. "/recap btc options 8h") and produces
  a fixed-format recap: snapshot block, biggest print, block flow table, themes,
  vol surface, and bottom line. Use when the user types /recap or asks for
  a market recap, options flow summary, "what happened in BTC options", or "last Xh of flow".
  The output format is fixed — always the same sections in the same order.
compatibility: Deribit public API (web_fetch), Paradigm block tape (if injected),
  OKX/Bullish/IBIT public APIs (web_fetch). No authentication required.
metadata:
  author: tradeparadex
  version: "2.0"
---

# Options Recap

## Command Syntax

`/recap [asset] [window]` — order-independent, all optional.

| Token | Examples | Default |
|---|---|---|
| `asset` | `btc`, `eth` | `btc` |
| `window` | `1h`, `4h`, `8h`, `24h`, `1d` | `24h` |

`/recap` alone = BTC options, last 24h.

## Data Fetches

Fetch DVOL, spot, and trades in parallel. Vol surface needs the instrument list first, then per-instrument tickers.

| Data | Endpoint |
|---|---|
| DVOL history | `/api/v2/public/get_volatility_index_data?currency=BTC&resolution=3600&start_timestamp=<ms>&end_timestamp=<ms>` |
| Spot (recap window) | `/api/v2/public/get_tradingview_chart_data?instrument_name=BTC-PERPETUAL&resolution=60&start_timestamp=<ms>&end_timestamp=<ms>` |
| Spot (7d for realized) | Same endpoint, `start_timestamp=<7d-ago>` |
| Option trades | `/api/v2/public/get_last_trades_by_currency?currency=BTC&kind=option&count=1000&start_timestamp=<ms>&end_timestamp=<ms>&sorting=desc` |
| Instrument list | `/api/v2/public/get_instruments?currency=BTC&kind=option&expired=false` |
| Per-instrument ticker | `/api/v2/public/ticker?instrument_name=<inst>` (or `deribit__get_ticker` MCP) |
| OKX trades | `/api/v5/market/trades?instType=OPTION&instFamily=BTC-USD` |
| Bullish trades | `/trading-api/v1/trades?type=option` |

**Response shapes:**
- DVOL: `result.data` → `[[ts, open, high, low, close], …]`. First open = start, last close = end.
- Spot: `result` → `{open, high, low, close, ticks}`. Use `low[]`/`high[]`/`close[]`.
- Instruments: `result` → `[{instrument_name, strike, expiration_timestamp, option_type}]`. Use the exact `instrument_name` string (Deribit format: `5JUN26`, not `05JUN26`).
- Ticker: carries `mark_iv`, `greeks` (`delta`, `vega`, `gamma`). Use `mark_iv` only — thin books push `ask_iv` to extremes.

`block_trade_id` present = block; absent = screen.

## Computing the numbers

Realized vol, flow greeks (Black-76), and surface skew are math that LLMs get wrong by estimating. **Always use the bundled script; never hand-compute these.**

```bash
uv run scripts/paradex_options_recap.py --data snapshot.json
```

Snapshot shape (omit any field to skip that section):
```json
{
  "dvol_close": 48.16,
  "spot": 61973.5,
  "spot_closes_7d": [63670, 63812, …],
  "trades": [{"instrument_name": "BTC-26JUN26-55000-P", "iv": 72.0,
              "timestamp": 1780000000000, "direction": "buy",
              "amount": 100, "block_trade_id": "BLOCK-1"}],
  "tickers": {"BTC-5JUN26-62000-C": {"mark_iv": 82.87, "delta": 0.4956}}
}
```

Returns `realized_vol`, `flow_greeks`, `top_blocks`, `vol_surface`. If a `derived` block is already injected into context, read it and skip the script.

## Analysis

**Snapshot** — pull spot, DVOL open→close, RV(7d) vs implied. VRP = implied − realized. Label:
- spot↑ vol↓ → "vol sold through rally"
- spot↓ vol↑ → "vol bid into weakness"
- spot↑ vol↑ → "vol bought through rally"

RV must be 7-day trailing window. Read from `derived.realized_vol` or the script — never estimate.

**Block Flow** — read `top_blocks` from script. Biggest single print first, then structure table sorted by notional. Each row: structure name, total notional, detail line (strikes × size × side × IV). Mark `two-way` or one-sided from the field — do not infer.

Dealer positioning from `flow_greeks.positioning_label`:
- short gamma → chase spot, amplify moves
- long gamma → fade moves
- balanced → no decisive positioning

**Themes** — screen (non-block) trades grouped by expiry/strike/direction. 2–4 bullets. Named, factual, no intent inference.

**Vol Surface** — discover-then-fetch:
1. `get_instruments` once. Front expiry (nearest ≥ now) + second if blocks span expiries.
2. ATM ± 4 strikes per expiry. Fetch tickers in parallel. Pass `mark_iv` + `delta` + `spot` to script.
3. Read `atm_iv`, `rr_25d`, `butterfly_25d`, `term_structure`. Note `wings_extrapolated` if set.

## Output Format — FIXED

Six sections, this exact order, every recap. Never reorder, add, or drop sections.

Work silently — no narration. If live tools are unavailable, prepend one line: `⚠ Data estimated — no live feed available.`

---

**Shape to mirror:**

**[ASSET] Options · [WINDOW] Recap · [HH:MM]–[HH:MM] UTC**

**Snapshot**

```yaml
Spot      $[X]        [up/down X%] (from $[Y])
DVOL      [X]v        [flat/rising/falling] ([open] -> [close])
RV 7d     [X]v        implied [CHEAP/RICH/IN LINE] vs realized
VRP       [±X]v       vol [underpriced/overpriced] vs delivered
Volume    $[X]M       [primary venue] (incl. Paradigm)
P/C       [X.Xx]x     [calls/puts] dominant
```

**Biggest Print**

```yaml
[DDMMMYY] [structure]   [Nx]   $[X]M   [HH:MM] UTC   via [Venue]
```

**Block Flow — $[X]M / [N] blocks**

```yaml
#  Structure            Notl     Detail
-  -------------------  -------  ------------------------------------------
1  [structure]          $[X]M    [strikes] x[size] - [SIDE] [IV]v [two-way/one-sided]
2  …
```

Dealer positioning: [short/long] vega + [short/long] gamma → [mechanical read]; [implication].

**Themes**
1. [Theme name] — [structure, strikes, size, IV, side]. [One factual line.]
2. …

[2–4 themes. Facts only — no intent inference.]

**Vol Surface**
Skew: front 25Δ RR [±X]v → [puts bid / calls bid] · Term: [front]v → [back]v → [contango / flat / backwardation]

```yaml
Expiry     ATM                  25d RR               Fly
---------  -------------------  -------------------  -------------------
[DDMMMYY]  [X.X] → [Y.Y]v      [±X.X] → [±Y.Y]v    [X.X] → [Y.Y]v
…
```

`* wings extrapolated` (if applicable)

**Bottom Line**
[1–2 sentences. Calls/puts lead, volume, dominant block structure. Dealer positioning vs vol surface vs RV/IV gap. No forecasts.]

---

## Thin Window

(< 2h, no blocks, < 20 screen trades) — output all six sections; mark empty ones `No data`.
