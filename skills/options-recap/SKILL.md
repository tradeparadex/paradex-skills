---
name: paradex-options-recap
description: >
  Options market recap for a user-specified window, invoked via /recap. Parses
  "/recap [asset] [options] [window]" (e.g. "/recap btc options 8h") and produces
  a fixed-format recap: DVOL/spot, volume by venue, block structure mix, flow themes,
  vol surface movers, and a summary. Use when the user types /recap or asks for
  a market recap, options flow summary, "what happened in BTC options", or "last Xh of flow".
  The output format is fixed — always the same sections in the same order.
compatibility: Deribit public API (web_fetch), Paradigm block tape (if injected),
  OKX/Bullish/IBIT public APIs (web_fetch). No authentication required.
metadata:
  author: tradeparadex
  version: "1.5"
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

Returns `realized_vol`, `flow_greeks`, `top_blocks`, `vol_surface`. If a `derived` block is already injected into context, read it and skip the script. Verify with `python3 scripts/test_vol_math.py`.

## Analysis

**1. DVOL / Spot** — open → close, range, spot range. Label the spot/vol relationship:
- spot↑ vol↓ → "vol sold through rally"
- spot↓ vol↑ → "vol bid into weakness"
- spot↑ vol↑ → "vol bought through rally"

Add the realized-vs-implied line: `RV(7d) Xv vs implied Yv → [rich / cheap / in line]`. Realized must be a 7-day trailing window (not the recap window). Read from `derived.realized_vol` or the script — never estimate stdev/annualization.

**2. Volume** — sum notional across execution venues (Deribit, OKX, Bullish, IBIT) by asset (BTC/ETH/Other). P/C ratio = put notional / call notional. Paradigm is a routing layer; do not list it as a separate venue.

**3. Block Flow** — read `top_blocks` from the script: largest single print, then structure mix sorted by notional. Each entry has `structure`, `size_btc`, `notional_usd`, `side`, `expiry`. Mark side as `Two-way` or one-sided based on the field — do not infer intent.

Then read net dealer positioning from `flow_greeks.positioning_label`. Possible labels:
- short gamma → dealers chase moves
- long gamma → dealers fade moves
- balanced → no decisive positioning

State the label and the mechanical read. Do not extrapolate beyond it.

**4. Screen Themes** — group non-block trades by expiry/strike/direction. Surface 3–5 factual bullets; one strike cluster per bullet.

**5. Vol Surface** — discover-then-fetch, then read from `vol_surface`:
1. Call `get_instruments` once. Pick front expiry (nearest ≥ now) plus second if block flow spans expiries.
2. In each expiry, take ATM ± 4 strikes (calls + puts). ±4 brackets the 25Δ wings; ±2 extrapolates them.
3. Fetch tickers in parallel. Pass `mark_iv` + `delta` + `spot` to the script.
4. Read `atm_iv`, `rr_25d` (25Δ risk reversal = skew), `term_structure`, `skew_label`. Note `wings_extrapolated` if set.

## Output Format — FIXED

All six sections in this exact order, every recap. Section 3 is always blocks; section 4 is always themes. Never reorder, add, or drop sections.

Work silently — no narration. If live tools are unavailable, prepend one line: `⚠ Data estimated — no live feed available.`

---

**Shape to mirror:**

**BTC Options · [WINDOW] Recap · [HH:MM]–[HH:MM] UTC**

---

**DVOL / Spot**

[ASSET] DVOL: Xv → Yv (±Zv) · range A–B · [rising / drifting lower / flat]
Spot: now $X (from $Y) · [spot/vol read]
RV(7d) Rv vs implied Yv → [rich / cheap / in line]

**Volume · $[TOTAL]M · P/C ratio [X.Xx] ([puts/calls] dominant)**

| Venue | BTC | ETH | Other | Total |
|---|---|---|---|---|
| Deribit | $XM | $XM | $XM | $XM |
| OKX | $XM | $XM | $XM | $XM |
| Bullish | $XM | $XM | $XM | $XM |
| IBIT | $XM | $XM | $XM | $XM |
| **Total** | **$XM** | **$XM** | **$XM** | **$XM** |

---

**Largest Blocks · Deribit / OKX / Bullish / IBIT (incl. Paradigm-routed)**

**Largest single:** [DDMMMYY] [Strike] [Structure] · [Nx] · $[X]M · [Venue] · [HH:MM] UTC

| Structure | Notl | Where active |
|---|---|---|
| Outright puts | $XM | Jun 55k P ×200, Sep 50k P ×150 |
| Put spreads | $XM | Jun 60/55k ×250 |
| … | … | … |

Dealer positioning: [long/short] gamma/vega · ≈$X vega/vol-pt

---

**Flow Themes**

[Theme name] — [structure, size, strikes, IV, side]. [One factual line if needed.]

[3–5 themes. Facts only — no intent inference.]

---

**Vol Surface**

Skew: 25Δ RR Zv — [puts bid / calls bid]
Term structure: front Xv / back Yv — [contango / flat / backwardation]
ATM by expiry: DDMMMYY Xv · DDMMMYY Yv

| Strike | IV | Δ IV | OI | Δ OI |
|---|---|---|---|---|
| DDMMMYY Xk C/P | Xv | ±Xv | X BTC | ±X BTC |

[5–8 rows sorted by |Δ IV| descending]

---

**Summary**

[1–2 sentences. State the facts of the session — who was in the market, what they did. No forecasts, no recommendations.]

---

## Thin Window

(< 2h, no blocks, < 20 screen trades) — output all six sections; mark empty ones `No data`.
