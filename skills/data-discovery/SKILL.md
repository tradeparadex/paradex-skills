---
name: paradigm-data-discovery
description: >
  Catalog of historical S3 + DuckDB datasets for Paradigm RFQ block-trade flow
  and Tardis exchange market data (Deribit and OKX options). This is a
  catalog skill only — it answers "which historical datasets are connected,
  where do they live in S3, what columns do they have, what date ranges do
  they cover, and how do they join". It does NOT cover live Paradex perp DEX
  data, live exchange tickers, account positions, vaults, or order placement.
  Paradigm (the RFQ block-trade platform) and Paradex (the perp DEX) are
  different products — this skill is the Paradigm side. Fire when the user
  asks what historical / S3 / DuckDB / Tardis / Paradigm-tape data is
  available, what columns a dataset has, where a file lives in S3, what date
  range a dataset spans, or as a routing step before composing a DuckDB
  query against the s3://terminal-paradigm-prod bucket. Do not fire for
  questions about Paradex markets, positions, funding, or live tickers —
  route those to the Paradex-specific skills.
compatibility: Read-only data catalog. No authentication required to view the
  catalog itself. Running the suggested DuckDB/S3 queries requires IRSA
  credentials (AWS_WEB_IDENTITY_TOKEN_FILE, AWS_ROLE_ARN) — see
  references/s3-access.md for the credential bootstrap.
metadata:
  author: tradeparadex
  version: "1.1"
---

# Paradigm Data Discovery

Reference catalog of **historical S3-backed datasets** the agent can query
through DuckDB. Scope: Paradigm RFQ tapes and Tardis exchange data on
`s3://terminal-paradigm-prod`. Lets the agent answer "which historical
dataset do I need?" without globbing the bucket every session.

## Scope — Paradigm, not Paradex

This skill covers **Paradigm** (the RFQ block-trade platform) historical data
plus **Tardis-sourced** Deribit and OKX option data. It does **not** cover
**Paradex** (the perp DEX) — for live Paradex markets, positions, funding,
vaults, or order placement, route to the Paradex-specific skills
(`market-analyst`, `portfolio-copilot`, `vault-intelligence`, etc.).

If the user's query contains "Paradex" and not "Paradigm", and is asking
about a live or account-bound concept (positions, P&L, current funding,
chain data, orderbook), this is the wrong skill — stand down.

## Trigger

Fire only when both:
(a) the user is asking about **data availability / schema / path / coverage**
    (not analysis or live values), **and**
(b) the question is anchored to one of the in-scope sources: Paradigm tape,
    Tardis, DuckDB, S3, `terminal-paradigm-prod`, block trades, RFQs, combo
    quotes, option trades history.

Strong-fire phrases:

- "What Paradigm / Tardis / S3 / DuckDB data do we have?"
- "Where does the <Paradigm tape | Tardis trades | combo quotes> live in S3?"
- "What columns does the <Paradigm trade tape | RFQ tape | Tardis trades> have?"
- "What's the date range for <Deribit combo quotes | OKX option trades>?"
- "Do we have <OKX combos | Bybit options | AVAX options> in S3?"
- "What's the schema for `paradigm_trade_tape_slim` / `paradigm_rfq_tape_slim`?"
- "I need to write a DuckDB query — which dataset?"

Do **not** fire for:

- Live exchange tickers / mark prices / greeks (use `paradigm-block-analyst`
  or venue-specific skills).
- Paradex perp DEX questions — markets, funding, positions, orders, vaults,
  margin, fills (Paradex skills, not Paradigm).
- Generic "what can you do" / "what skills do I have" — that's a meta
  question, not a data catalog question.
- A trade JSON paste asking for analysis (use `paradigm-block-analyst`).
- Questions that name a dataset path and want a query written — hand off
  directly to query authoring.

## Step 1 — Identify Intent

Classify the user's question into one of:

| Intent | Action |
|---|---|
| Inventory ("what do we have?") | List the dataset families in `references/datasets.md` |
| Lookup ("where is X?") | Match to a specific dataset row, return path + schema |
| Coverage ("date range for X?") | Return last verified range + glob check snippet |
| Schema ("columns of X?") | Return the column table for that dataset |
| Gap ("do we have Y?") | Check catalog; if absent, point to "What Is NOT Here" |
| Routing (pre-query) | Surface 1–2 candidate datasets and prompt for confirmation |

## Step 2 — Surface the Catalog

Pull from `references/datasets.md`. It is grouped into:

1. **Paradigm Block Trade Tape** (`paradigm_data/`)
   - `paradigm_trade_tape_slim` — executed RFQ block trades
   - `paradigm_rfq_tape_slim` — RFQ activity including unfilled
2. **Tardis Market Data** (`external/tardis/v1/`)
   - Deribit option trades
   - Deribit option quotes (sparse)
   - Deribit combo quotes (densest dataset)
   - OKX option trades

For each dataset listed, report:

- **S3 path** (with the `YYYY/MM/DD` partition pattern where applicable)
- **Last verified coverage** (date range, with the caveat that coverage may
  extend forward)
- **Schema** (column name + type + notes)
- **Notable filters / partitions** (e.g. `WHERE PRODUCT LIKE '%OPTION%'`,
  combo strategy codes like `CS`, `CCAL`, `CDIAG`, `FS`, `CSRxy`)

## Step 3 — Always Include Verification Hint

Coverage dates in the catalog are point-in-time snapshots. When the user asks
about a specific date or recent data, include the glob date-range probe:

```sql
SELECT
  MIN(regexp_extract(file, '/(\d{4}/\d{2}/\d{2})/', 1)) AS earliest,
  MAX(regexp_extract(file, '/(\d{4}/\d{2}/\d{2})/', 1)) AS latest,
  COUNT(*) AS file_count
FROM glob('<path-with-**>');
```

…so they can confirm latest availability before concluding data is missing.

## Step 4 — Output Format

Structure responses as:

1. **Direct answer** — one or two sentences naming the dataset(s) that fit.
2. **Path + coverage** — S3 URI, last verified date range, partitioning.
3. **Schema** — column table only if the user asked for columns or is about
   to query (omit for pure inventory questions to keep responses tight).
4. **Caveats** — coverage gaps, unit quirks (Tardis µs timestamps, Deribit
   prices in index currency, contracts not USD), and join keys.
5. **Next step** — either the verification glob query or a prompt to confirm
   which dataset to query.

Keep responses short for inventory questions; expand only when the user is
clearly about to query.

## Notes

- **Bucket:** `s3://terminal-paradigm-prod`, region `eu-west-2`.
- **Auth:** IRSA (web identity → STS AssumeRoleWithWebIdentity) — see
  `references/s3-access.md`. Tokens expire ~1 hour; refresh on
  HTTP 400 `InvalidToken`.
- **DuckDB:** `INSTALL httpfs; LOAD httpfs;` every new session.
- **Unit gotchas to flag when relevant:**
  - Tardis timestamps are microseconds → `to_timestamp(ts / 1e6)`.
  - Deribit option prices are in BTC/ETH (index currency), not USD.
  - Deribit amounts are contracts (1 BTC contract = 1 BTC notional).
- **Join keys across Paradigm tapes:** `RFQ_ID`, `BLOCK_TRADE_ID`.
- **Paradigm exchange suffixes:** `DBT` = Deribit, `PRDX` = Paradex,
  `BYB` = Bybit.
- **What is NOT here** (call out when asked): Deribit option quotes beyond
  2026-01-01 are sparse, OKX combo quotes are absent, Greeks/IV are not in
  raw Tardis data (compute or source separately), Paradex options are
  excluded (everlasting/perpetual style, no expiry).
- This skill is a catalog only — it does not execute queries. For analysis
  on a parsed Paradigm trade JSON, hand off to `paradigm-block-analyst`.
