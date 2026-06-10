---
name: paradigm-data-discovery
description: >
  Catalog and query-launcher for ALL historical market data in S3
  (s3://terminal-dime-prod). ALWAYS load this skill before concluding
  any dataset is out of scope — do not dismiss based on asset class or
  venue assumptions. Covers: Paradigm RFQ block-trade tape,
  Paradigm RFQ activity tape, Deribit/OKX option trades + combo
  quotes + future top-of-book quotes, Bullish option chain snapshots
  (native greeks/IV) + orderbook history, IBIT ETF options trades
  (equity-side vol cross-reference; DO NOT dismiss
  as out of scope — it lives in this S3 bucket), and the on-chain Paradex
  perp historical trade tape. Fires for any retrospective or "what data do
  we have" question — returns S3 path + ready-to-run DuckDB query. Does
  NOT cover live Paradex markets, positions, vaults, or order placement.
compatibility: Read-only data catalog. No authentication required to view the
  catalog itself. Running the suggested DuckDB/S3 queries requires IRSA
  credentials (AWS_WEB_IDENTITY_TOKEN_FILE, AWS_ROLE_ARN) — see
  references/s3-access.md for the credential bootstrap.
metadata:
  author: tradeparadex
  version: "1.3"
---

## Hard Rules

1. **Never dismiss a data query without reading this skill first.**
   Domain assumptions ("IBIT is TradFi", "that's not a Paradigm product",
   "that venue isn't supported") are NOT a valid substitute for checking
   the catalog. All five dataset families live under
   `s3://terminal-dime-prod` regardless of the instrument's native
   venue or asset class.
2. **IBIT is in scope.** `paradigm_data/ibit_options_trades/` contains
   IBIT ETF option trades. Used for equity-side BTC vol
   cross-referencing. Always surface it when the user asks about IBIT,
   ETF options, or equity vol vs crypto vol comparisons.
3. **Default to this skill for any "what data / latest data / do we have X"
   question.** Even if the answer turns out to be "not in catalog," the
   correct response is to load this skill, check, and report — not to
   assume absence based on prior knowledge.

# Paradigm Data Discovery

Reference catalog **and entry-point** for historical S3-backed datasets the
agent can query through DuckDB. Scope: everything under
`s3://terminal-dime-prod` — Paradigm RFQ tapes, exchange market data
(options + futures), Bullish option chain + orderbook, IBIT ETF options
trades, and the on-chain Paradex perp trade tape.

Two jobs:

1. **Catalog** — answer "which historical dataset do I need, where does it
   live, what's in it?" without globbing the bucket.
2. **Query launcher** — when the user asks a *retrospective* question that
   the catalog can answer (biggest trades in a window, volume by venue,
   structure mix over time, IBIT vs crypto vol, Bullish chain on a date),
   surface the path **and** a ready-to-run DuckDB query so the user (or
   downstream query runner) can execute it. Crucially: never reply
   "I don't have access to historical block trade data" — the tapes on S3
   *are* the historical data.

## Scope — historical S3 data, not live feeds

In scope: anything stored under `s3://terminal-dime-prod` —
Paradigm block-trade tapes, Deribit/OKX option and combo
data, Deribit/Bybit/OKX future quotes, Bullish option chain
snapshots + orderbook history, IBIT ETF option trades, and the historical
Paradex perp trade tape (`paradex_data/paradex_trade_tape.csv.gz`).

Out of scope: anything **live** — live Paradex markets, positions,
funding, vaults, orderbook, order placement, account state. For those,
route to the Paradex-specific skills (`market-analyst`,
`portfolio-copilot`, `vault-intelligence`, etc.).

Rule of thumb: if the user's question is anchored to a specific past date
or date range, or asks about a tape / snapshot / historical aggregate,
this skill is in scope. If the user wants "right now" / current / live
state of a Paradex account or market, stand down.

## Trigger

Fire when the user is asking about, or implicitly needs, any of the
historical S3-backed datasets — Paradigm tapes, option/future market data,
Bullish, IBIT, or the Paradex DEX historical trade tape. Four trigger
families:

**(A) Catalog questions** — explicit "what data / where / what columns /
what coverage":

- "What Paradigm / S3 / DuckDB data do we have?"
- "Where does the <Paradigm tape | option trades | combo quotes | Paradex
  trade tape> live in S3?"
- "What columns does the <Paradigm trade tape | RFQ tape | option trades
  | Paradex trade tape | IBIT trades | Bullish chain> have?"
- "What's the date range for <Deribit combo quotes | OKX option trades |
  Paradex trade tape | IBIT options>?"
- "Do we have <OKX combos | Bybit options | AVAX options | Paradex perp
  trades> in S3?"
- "What's the schema for `paradigm_trade_tape_slim` / `paradex_trade_tape`?"

**(B) Historical Paradigm-flow analysis** — retrospective questions over
a date range / period that the Paradigm tape can answer:

- "What were the biggest RFQs in <month/quarter/date range>?"
- "Rank Paradigm block trades by notional last <period>"
- "Show me <BTC|ETH|SOL|XRP|AVAX> option block trades on <date>"
- "How much Paradigm block volume on Deribit/Paradex/Bybit in <period>?"
- "Most-traded structures (straddles, risk reversals, etc.) in <period>"
- "Top counterparties / largest single trades / unfilled RFQ ratio in <period>"
- "Compare Paradigm flow across DBT/PRDX/BYB for <period>"

**(C) Exchange market-data analysis** — retrospective questions over option
trades, combo quotes, or future quotes:

- "Most-traded Deribit options on <date>"
- "OKX option trade volume in <period>"
- "Top combo quote activity on <date>"
- "Bybit/Deribit/OKX future top-of-book on <date>"

**(D) Paradex DEX historical trade tape** — retrospective questions about
*on-chain Paradex perp* trades that the historical tape can answer:

- "Biggest Paradex perp trades in <period>"
- "Paradex BTC-USD-PERP volume on <date>"
- "Show me Paradex trade tape rows from <date range>"
- "How many trades on Paradex `<MARKET>` last month?"
- "Paradex taker buy vs sell breakdown for <period>"

For (B), (C), and (D), the response **must** include both the S3 path
and a concrete DuckDB query (see Step 5) — don't just describe the
dataset. Filter `WHERE NOT IS_TRADEBUST` on the Paradex tape.

Do **not** fire for:

- Live exchange tickers / mark prices / greeks (use `paradigm-block-analyst`
  or venue-specific skills).
- **Live** Paradex questions — current positions, current funding rate,
  live orderbook, order placement, vault state, margin (those are
  Paradex-live skills, not this catalog). The *historical* Paradex trade
  tape *is* in scope — see family (D) above.
- Generic "what can you do" / "what skills do I have" — that's a meta
  question, not a data catalog question.
- A trade JSON paste asking for analysis of *that single trade* (use
  `paradigm-block-analyst`).

## Step 1 — Identify Intent

| Intent | Action |
|---|---|
| Inventory ("what do we have?") | List dataset families from `references/datasets.md` |
| Lookup ("where is X?") | Return path + schema for that dataset |
| Coverage ("date range for X?") | Return last verified range + glob probe |
| Schema ("columns of X?") | Return column table |
| Gap ("do we have Y?") | Check catalog; if absent, point to "What Is NOT Here" |
| **Historical analysis** ("biggest RFQs in March 2026") | **Pick the dataset, return path + ready-to-run DuckDB query (Step 5)** |
| Routing (pre-query) | Surface 1–2 candidate datasets and prompt for confirmation |

## Step 2 — Surface the Catalog

Pull from `references/datasets.md`. Grouped into:

1. **Paradigm Block Trade Tape** (`paradigm_data/`)
   - `paradigm_trade_tape_slim` — executed RFQ block trades
   - `paradigm_rfq_tape_slim` — RFQ activity including unfilled
2. **Exchange Market Data** (`external/tardis/v1/`)
   - Deribit option trades
   - Deribit option quotes (sparse)
   - Deribit combo quotes (densest dataset)
   - OKX option trades
   - Future quotes (Deribit, Bybit, OKX) — top-of-book for dated +
     perpetual futures
3. **Bullish (Options)** (`paradigm_data/bullish_*`)
   - `bullish_option_chain_snapshots` — chain snapshots with **native
     greeks and IV** (only dataset in the catalog with these)
   - `bullish_options_orderbook_historical` — top-2-level orderbook
     history
4. **IBIT ETF Options Trades** (`paradigm_data/ibit_options_trades/`)
   - IBIT (Bitcoin ETF) option trades — equity-side vol
     cross-reference for crypto BTC options
5. **Paradex DEX Trade Tape** (`paradex_data/`)
   - `paradex_trade_tape.csv.gz` — on-chain Paradex perp trades
     (historical only; live Paradex state is out of scope)

For each, report: S3 path (with the correct partition pattern —
`YYYY/MM/DD/` for option/future market data, `date=YYYY-MM-DD/` Hive-style for Bullish/IBIT,
flat file for Paradigm and Paradex tapes), last verified coverage, schema,
notable filters (e.g. `WHERE PRODUCT LIKE '%OPTION%'` for Paradigm,
`WHERE NOT IS_TRADEBUST` for Paradex tape).

## Step 3 — Always Include Verification Hint

When the user asks about a specific date or recent data, include the glob
date-range probe. Use the regex that matches the dataset's partition
layout:

**Daily-partitioned (`YYYY/MM/DD/`) — option/future market data:**

```sql
SELECT
  MIN(regexp_extract(file, '/(\d{4}/\d{2}/\d{2})/', 1)) AS earliest,
  MAX(regexp_extract(file, '/(\d{4}/\d{2}/\d{2})/', 1)) AS latest,
  COUNT(*) AS file_count
FROM glob('<path-with-**>');
```

**Hive-style (`date=YYYY-MM-DD/`) — Bullish, IBIT:**

```sql
SELECT
  MIN(regexp_extract(file, 'date=(\d{4}-\d{2}-\d{2})', 1)) AS earliest,
  MAX(regexp_extract(file, 'date=(\d{4}-\d{2}-\d{2})', 1)) AS latest,
  COUNT(*) AS file_count
FROM glob('<hive-path-with-**>');
```

…so they can confirm latest availability before concluding data is missing.

## Step 4 — Output Format

1. **Direct answer** — name the dataset(s) that fit, in one or two sentences.
2. **Path + coverage** — S3 URI, last verified date range, partitioning.
3. **Schema** — column table only if user asked for columns or is about to
   query (omit for pure inventory questions).
4. **Caveats** — coverage gaps, unit quirks, join keys.
5. **Next step** — verification glob query, or for historical-analysis
   intent, a ready-to-run DuckDB query (Step 5).

## Step 5 — Ready-to-Run DuckDB Query (for historical-analysis intent)

When the user's question is analytical and answerable from the catalog,
always include a runnable DuckDB query. Pattern:

**Paradigm tape — biggest RFQ block trades in a window:**

```sql
-- Credential bootstrap assumed (see references/s3-access.md)
INSTALL httpfs; LOAD httpfs;

SELECT
  DATE, TIME, PRODUCT, DESCRIPTION, QTY, PRICE,
  NOTIONAL_VOLUME_USD, SIDE, RFQ_ID
FROM read_csv_auto('s3://terminal-dime-prod/paradigm_data/paradigm_trade_tape_slim.csv.gz')
WHERE DATE BETWEEN DATE '2026-03-01' AND DATE '2026-03-31'
  AND PRODUCT LIKE '%OPTION%'   -- or drop this filter for all products
ORDER BY NOTIONAL_VOLUME_USD DESC
LIMIT 25;
```

**Paradex DEX historical trade tape — biggest trades by notional in a window:**

```sql
INSTALL httpfs; LOAD httpfs;

SELECT
  TRADE_AT, MARKET, PRICE, SIZE, TAKER_SIDE,
  PRICE * SIZE AS NOTIONAL_USD
FROM read_csv_auto('s3://terminal-dime-prod/paradex_data/paradex_trade_tape.csv.gz')
WHERE NOT IS_TRADEBUST
  AND TRADE_AT >= TIMESTAMP '2026-04-01'
  AND TRADE_AT <  TIMESTAMP '2026-05-01'
  -- AND MARKET = 'BTC-USD-PERP'   -- optional: filter to a single market
ORDER BY NOTIONAL_USD DESC
LIMIT 25;
```

Query-template guidelines:

- Always include `INSTALL httpfs; LOAD httpfs;` at top.
- For **Paradigm** tape questions, use `paradigm_trade_tape_slim.csv.gz` for
  *executed* block trades; use `paradigm_rfq_tape_slim.csv.gz` if they want
  RFQ-level stats (fill rate, unfilled, lifespan).
- For **Paradex DEX** tape questions, use
  `paradex_data/paradex_trade_tape.csv.gz`. **Always filter
  `WHERE NOT IS_TRADEBUST`**. Compute notional as `PRICE * SIZE` — there
  is no precomputed USD notional column.
- For **option/future market data** questions, use the daily-partitioned path with a glob over
  the date range; remember timestamps are µs (`to_timestamp(ts / 1e6)`).
- For **Bullish chain** questions, prefer this dataset for greeks/IV.
- For **IBIT** questions, expect the equity calendar (no weekends).
- Filter by `PRODUCT LIKE '%OPTION%'` only if the user specifically asked
  about options; otherwise leave it open so perps/futures are included.
- Use `NOTIONAL_VOLUME_USD` (Paradigm) or `PRICE * SIZE` (Paradex DEX) for
  "biggest" / "largest" / ranking queries.
- Use the exchange suffix in Paradigm `PRODUCT` (`- DBT`, `- PRDX`,
  `- BYB`) to filter by venue. For the Paradex DEX tape, filter by `MARKET`.

If the agent has a DuckDB-execution tool available, hand the query off to
it and present the results. If not, return the query with a note that
the user can run it themselves once IRSA credentials are loaded.

Keep responses short for inventory questions; for analysis-intent
questions, give the path + query + a one-line interpretation.

## Notes

- **Bucket:** `s3://terminal-dime-prod`, region `ap-northeast-1`.
- **Auth:** IRSA (web identity → STS AssumeRoleWithWebIdentity) — see
  `references/s3-access.md`. Tokens expire ~1 hour; refresh on
  HTTP 400 `InvalidToken`.
- **DuckDB:** `INSTALL httpfs; LOAD httpfs;` every new session.
- **Unit gotchas to flag when relevant:**
  - Option/future feed timestamps are microseconds → `to_timestamp(ts / 1e6)`.
  - Deribit option prices are in BTC/ETH (index currency), not USD.
  - Deribit amounts are contracts (1 BTC contract = 1 BTC notional).
- **Join keys across Paradigm tapes:** `RFQ_ID`, `BLOCK_TRADE_ID`.
- **Paradigm exchange suffixes:** `DBT` = Deribit, `PRDX` = Paradex,
  `BYB` = Bybit.
- **What is NOT here** (call out when asked): Deribit option quotes beyond
  2026-01-01 are sparse, OKX combo quotes are absent, Greeks/IV are not in
  the raw option/future feed data, Paradex options excluded (everlasting/perpetual style).
- This skill is a catalog and query-launcher. For analysis of a single
  pasted trade JSON, hand off to `paradigm-block-analyst`. For execution of
  the SQL queries this skill emits, use whatever DuckDB tool the agent has
  available.
