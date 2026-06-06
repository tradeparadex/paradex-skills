---
name: paradex-options-recap
description: >
  Options market recap for a user-specified window, invoked via /recap. Parses
  "/recap [asset] [options] [window]" (e.g. "/recap btc options 8h") and produces:
  DVOL/spot summary, largest block prints with structure and IV, screen flow themes,
  vol surface reads, and a net bias call. Use when the user types /recap or asks for
  a market recap, options flow summary, "what happened in BTC options", or "last Xh of flow".
compatibility: Deribit public API (web_fetch), Paradigm block tape (if injected),
  deribit__get_ticker MCP (if available). No authentication required.
metadata:
  author: tradeparadex
  version: "1.1"
---

# Options Recap

Converts options flow data into a concise market recap for a user-specified window.

## Command Syntax

```
/recap [asset] [market_type] [window]
```

| Token | Examples | Default |
|---|---|---|
| `asset` | `btc`, `eth` | `btc` |
| `market_type` | `options`, `opts` | `options` |
| `window` | `1h`, `4h`, `8h`, `24h`, `1d` | `24h` |

Tokens are order-independent. Missing tokens use defaults. `/recap` alone = BTC options, last 24h.
If `market_type` is `perps`, route to `paradex-market-analyst` instead.

## Data Fetches

DVOL, spot, and trades are independent — fetch them in parallel. The vol surface needs the instrument list first, then per-instrument tickers (see Analysis step 4).

| Data | Endpoint |
|---|---|
| DVOL history | `GET /api/v2/public/get_volatility_index_data?currency=BTC&resolution=3600&start_timestamp=<ms>&end_timestamp=<ms>` |
| Spot range | `GET /api/v2/public/get_tradingview_chart_data?instrument_name=BTC-PERPETUAL&resolution=60&start_timestamp=<ms>&end_timestamp=<ms>` |
| All option trades | `GET /api/v2/public/get_last_trades_by_currency?currency=BTC&kind=option&count=500&start_timestamp=<ms>&end_timestamp=<ms>&sorting=desc` |
| Instrument list (for vol surface) | `GET /api/v2/public/get_instruments?currency=BTC&kind=option&expired=false` |
| Per-instrument ticker (for vol surface) | `GET /api/v2/public/ticker?instrument_name=<inst>` (or `deribit__get_ticker` MCP if available) |

Response shapes:
- DVOL: `result.data` is `[[timestamp_ms, open, high, low, close], ...]` — open of first row = DVOL open, close of last row = DVOL close.
- Spot: `result` is `{open: [], high: [], low: [], close: [], ticks: [...]}` — min of `low[]` = spot low, max of `high[]` = spot high, last of `close[]` = spot current.
- Instruments: `result` is `[{instrument_name, strike, expiration_timestamp, option_type}, ...]` — the authoritative source of valid instrument names. Never construct names by hand; Deribit uses its own date format (`5JUN26`, not `05JUN26`).
- Ticker: `result` carries `mark_iv`, `bid_iv`, `ask_iv`, and `greeks.delta` per instrument.

Filter the trade list: `block_trade_id` present = block; absent = screen flow.

## Analysis Steps

**1. DVOL / Spot** — open → close, range, spot low/high. Label the relationship:
- Spot up + vol down → "vol sold through a rally"
- Spot down + vol up → "vol bid into weakness"
- Spot up + vol up → "vol bought through a rally"

**2. Block Flow** — cluster trades by `block_trade_id` to reconstruct multi-leg structures.
Report top 8 clusters (> 10 BTC notional) in a table: Time · Structure · Size · Side · Level · IV.
Note two-way vs one-sided flow per structure.

Structure types from leg instruments: same expiry + same strike + C+P = Straddle; same expiry + diff strikes + same type = Spread; diff expiries + same strike = Calendar; C+P diff strikes = Strangle/RR.

**3. Screen Flow Themes** — group non-block trades by expiry, strike, and direction. Surface 3–5 factual bullets:
```
- 0DTE put bid: 63k P at 56v vs ATM 47v · 8 clips
- OTM call selling: 65k C sold 48v · 5 clips
- [Largest screen print]: 23x 70k C sell @ 0.0018 / 45.7v
```

**4. Vol Surface** — build it with a discover-then-fetch pipeline. Do NOT guess instrument names.

1. *Discover.* Call `get_instruments` once. From the returned list, pick the front expiry (nearest `expiration_timestamp` ≥ now) and, if block flow spans expiries, the second expiry too.
2. *Select strikes.* Within each chosen expiry, find the ATM strike (closest to current spot) and take ATM ± 2 strikes, both calls and puts (≈10 instruments per expiry). Use the exact `instrument_name` strings from step 1 — never reconstruct them.
3. *Fetch.* Get the ticker for each selected instrument (parallel). Read `mark_iv` per strike and `greeks.delta` to locate the 10d/25d points.
4. *Assemble.* Group by expiry, sort by strike, then read skew, term structure, and ATM levels off the `mark_iv` values:
```
Put skew: 25d P Xv vs 25d C Yv = +Zv put premium
Term structure: near Xv / back Yv — [contango / flat / backwardation]
ATM by expiry: DDMMMYY Xv · DDMMMYY Yv
```

Quote `mark_iv`, not `ask_iv` — thin books push ask_iv to extreme values (e.g. 190v on a barely-quoted wing) that misrepresent the surface.

## Output Format

Work silently — no narration, no "fetching…". Output is the recap only.

If live data tools are unavailable and you are generating estimated/simulated values, add a one-line note at the top of the recap: `⚠ Data estimated — no live feed available.`

```
## [ASSET] Options — Last [WINDOW] Recap ([HH:MM]–[HH:MM] UTC)

### DVOL / Spot
[ASSET]DVOL: Xv → Yv (±Zv) · range A–B · [spike/fade note if any]
Spot: low X / high Y / now Z · [front futures] $XM vol
[Spot vs vol label]

### Largest Blocks (Paradigm/DBT)
| Time UTC | Structure | Size | Side | Level | IV |
|---|---|---|---|---|---|
| HH:MM | [description] | Nx | Buy/Sell | price | Xv/Yv |

[One line per flow cluster: two-way or one-sided]

### Screen Flow — Notable Themes
- [Theme 1]
- [Theme 2]
- [Theme 3]

### Vol Surface
[3–4 lines: skew, term structure, ATM levels, any anomaly]

`[Bias]` [2–3 sentences: who controls vol, dominant block flow expressed, screen positioning theme, surface confirmation. Facts only — no trade recommendations.]
```

**Thin window** (< 2h, no blocks, < 20 screen trades):
```
## [ASSET] Options — Last [WINDOW] Recap
Light window: no block prints, N screen trades. DVOL Xv → Yv. Spot low–high.
```
