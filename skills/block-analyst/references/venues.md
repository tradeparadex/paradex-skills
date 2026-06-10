# Venue Reference — Paradigm Block Analyst

## Deribit (DBT)

**Instrument naming:**
- Options: `BTC-DDMMMYY-STRIKE-C/P` → `BTC-7MAY26-81500-C`
- Perpetuals: `BTC-PERPETUAL`, `ETH-PERPETUAL`
- ETH options: `ETH-DDMMMYY-STRIKE-C/P` → `ETH-10MAY26-2375-C`

**Tool:** `deribit__get_ticker` (native — fastest, most complete)

**Returns:** mark_price, best_bid_price, best_ask_price, mark_iv, bid_iv, ask_iv,
greeks (delta, gamma, theta, vega), open_interest, underlying_price

**Notes:**
- Primary venue for Paradigm flow — most BTC/ETH options route here
- IV returned as percentage (e.g. `34.52` = 34.52%)
- Theta is in USD/day; delta is in BTC/ETH per contract (1 contract = 1 BTC or 1 ETH)

**Identifying Paradigm / block trades on the public tape:**
`get_last_trades_by_instrument` returns one object per trade. Block trades (which is how
Paradigm-routed flow settles on Deribit) carry extra fields:
- `block_trade_id` — present only on block trades. Group prints sharing the same id into one block.
- `block_trade_leg_count` — number of legs in that block (> 1 ⇒ multi-leg structure, e.g. a
  calendar/spread routed as a package). A single Paradigm block shows up as one `block_trade_id`
  with N leg prints, often timestamped within the same millisecond.
- Trades with **no** `block_trade_id` are on-screen / central-limit-order-book flow.
So to reconstruct Paradigm-style blocks from the public tape: pull `get_last_trades_by_instrument`
per leg, keep rows with a `block_trade_id`, and cluster by that id (and timestamp) to see prior
packaged blocks on the same strikes — the best proxy when the native Paradigm tape isn't injected.
Useful windowing params: `start_timestamp` / `end_timestamp` (epoch ms), `count` (max 1000).

---

## OKX

**Instrument naming:**
- Options: `BTC-USD-YYMMDD-STRIKE-C/P` → `BTC-USD-260507-81500-C`
- USDC-margined options: `BTC-USD_UM-DDMMMYY-STRIKE-C/P` (format varies by endpoint)
- Spot ticker: `BTC-USDT`

**Endpoints:**

| Purpose | Endpoint |
|---|---|
| Single instrument ticker | `GET /api/v5/market/ticker?instId=BTC-USD-260507-81500-C` |
| Full expiry vol surface (mark IV + greeks for all strikes) | `GET /api/v5/public/opt-summary?uly=BTC-USD&expTime=YYMMDD` |
| Mark IV + greeks for USDC-margined | `GET /api/v5/public/opt-summary?uly=BTC-USD&expTime=YYMMDD` (returns `_UM` instIds) |

**Base URL:** `https://www.okx.com`

**opt-summary response fields:**
- `instId` — instrument identifier
- `markVol` — mark IV as decimal (e.g. `0.3452` = 34.52%)
- `bidVol` / `askVol` — bid/ask IV as decimal
- `delta`, `gamma`, `theta`, `vega` — greeks (BS convention)
- `volLv` — ATM vol level for the expiry (useful for term structure)
- `fwdPx` — forward price

**Strike grid notes:**
- OKX strike increments differ from Deribit — `$81,500` may not exist
- Nearest strikes are typically in $200–$500 increments near ATM
- When exact strike absent: report the two nearest strikes and interpolate IV
  linearly by moneyness: `IV_81500 ≈ IV_low + (81500 - strike_low) / (strike_high - strike_low) × (IV_high - IV_low)`

**Known limitations:**
- Single ticker endpoint does NOT return IV — use opt-summary for IV
- Short-dated expirations (<2 DTE) may have zero bid/ask vol (mark vol still populated)
- `expTime` format is `YYMMDD` (e.g. `260507` for 7 May 2026) — NOT `YYYYMMDD`

---

## Bybit

**Instrument naming:**
- Options: `BTC-DDMMMYY-STRIKE-C/P` → `BTC-07MAY26-81500-C`
- Note: day is zero-padded (`07` not `7`)

**Endpoints (via Bybit skill market module):**

| Purpose | Endpoint |
|---|---|
| Ticker for specific instrument | `GET /v5/market/tickers?category=option&symbol=BTC-07MAY26-81500-C` |
| All tickers for expiry | `GET /v5/market/tickers?category=option&baseCoin=BTC&expDate=07MAY26` |
| Historical volatility | `GET /v5/market/historical-volatility?category=option&baseCoin=BTC` |

**Base URL:** `https://api.bybit.com`

**Module Router (must follow):**
Before calling Bybit options endpoints, follow the Bybit skill Module Router:
1. Check if `modules/market.md` already loaded this session
2. If not: fetch manifest → verify SHA256 → download and verify market module
3. Then proceed with API calls per module instructions

**Known limitations:**
- Short-dated options (<3 DTE) frequently absent — empty `list` is normal
- Strike grid is sparser than Deribit, especially for BTC
- No IV field in ticker response — price only
- Bybit options volume is significantly lower than Deribit/OKX; treat as reference only

**When Bybit is useful:**
- Confirming whether a structure can be hedged/replicated on Bybit
- Cross-venue premium comparison for longer-dated strikes (7+ DTE)
- BTC perpetual funding rate comparison: `GET /v5/market/funding/history?category=linear&symbol=BTCUSDT`

---

## Paradex

**Tool:** `paradex_trades` MCP (native — no auth required for public trades)

**Instrument naming:**
- Perpetuals: `BTC-USD-PERP`, `ETH-USD-PERP`
- Options (where listed): `BTC-DDMMMYY-STRIKE-C/P` — same format as Deribit

**Returns:** timestamp, price, size, side

**Known limitations:**
- Paradex is primarily a perps/options DEX — not all Deribit strikes are listed
- For structures with option legs, check whether the instrument exists before querying
- Empty result is expected for exotic or short-dated strikes

**When useful:**
- Perp legs in combo structures — query `BTC-USD-PERP` for recent trade context
- Cross-DEX activity check for structures that may be replicated on-chain

---

## Bullish

**Base URL:** `https://api.exchange.bullish.com`

**Endpoint for recent trades:**
`GET /trading-api/v1/trades?symbol=<symbol>&limit=100`

**Symbol format:** `BTCUSDC` (no separator); options format: check listing first via
`GET /trading-api/v1/markets` and match by underlying and expiry.

**Known limitations:**
- Bullish primarily lists spot and a limited set of derivatives
- If the instrument is not listed, record "not listed on Bullish" — expected for most options structures
- API may be rate-limited without auth; one unauthenticated call per instrument is acceptable

---

## IBIT

**Status:** Venue details to be confirmed via `web_fetch` at runtime.

**Approach:**
1. Attempt `web_fetch` on the IBIT public API (endpoint to be resolved from known base URL)
2. If unreachable: record "IBIT unavailable" in data trace — do not fabricate counts
3. If the user's intent is the IBIT ETF options (CBOE-listed equity options):
   note that these are equity options, not crypto, and a direct structure comparison is
   not meaningful — flag this distinction for the user

---

## Cross-Venue Quick Reference

### Live Data (mark price, IV, greeks)

| Feature | Deribit | OKX | Bybit |
|---|---|---|---|
| Short-dated options | ✅ Best | ✅ Good | ❌ Often missing |
| IV in response | ✅ Native | ✅ opt-summary | ❌ No |
| Greeks in response | ✅ Native | ✅ opt-summary | ❌ No |
| Strike granularity | Fine | Medium | Sparse |
| Coin-margined | ✅ Yes | ✅ Yes (_UM) | ✅ Yes |
| Data source method | `deribit__get_ticker` | `web_fetch` | `web_fetch` (+ skill module) |
| Paradigm venue code | `DBT` | `OKX` | — |

### Trade History (90-day tape check)

| Venue | Method | Granularity | Notes |
|---|---|---|---|
| Paradigm | injected tape | Full structured blocks | Best for block-trade recurrence |
| Paradex | `paradex_trades` MCP | Per-instrument trades | Perp legs most relevant |
| Deribit | `web_fetch /api/v2/public/get_last_trades_by_instrument` | Per-leg trades | Deepest options history |
| OKX | `web_fetch /api/v5/market/trades` | Per-leg trades | Good secondary source |
| Bullish | `web_fetch /trading-api/v1/trades` | Per-instrument | Limited listing; expect "not listed" |
| IBIT | `web_fetch` (endpoint TBD) | Per-instrument | Confirm accessibility at runtime |
