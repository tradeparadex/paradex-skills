---
name: paradigm-data-discovery
description: >
  Catalog of market data accessible via S3 + DuckDB for the Paradigm/Paradex
  agent stack. Surfaces what datasets are connected, their S3 paths, schemas,
  date coverage, and join keys — so the agent can answer "what data do we have"
  questions without re-discovering the bucket each session. Covers Paradigm
  block trade tape, Paradigm RFQ tape, Tardis option trades for Deribit and
  OKX, Deribit option quotes, and Deribit combo quotes. Use when the user asks
  what data is available, what they can query, which venues/assets are covered,
  what columns a dataset has, where a file lives in S3, or what date range a
  dataset spans. Also use as a routing step before composing DuckDB queries:
  point the user at the right dataset, then hand off to the query author.
compatibility: Read-only data catalog. No authentication required to view the
  catalog itself. Running the suggested DuckDB/S3 queries requires IRSA
  credentials (AWS_WEB_IDENTITY_TOKEN_FILE, AWS_ROLE_ARN) — see
  references/s3-access.md for the credential bootstrap.
metadata:
  author: tradeparadex
  version: "1.0"
---

# Paradigm Data Discovery

Reference catalog of S3-backed market datasets the agent can query through
DuckDB. Lets the agent answer "what data do we have?" without globbing the
bucket every session.

## Trigger

Fire when the user asks any of:

- "What data do we have / what's available / what can I query?"
- "Do we have <venue> <asset> <product> data?" (e.g. "do we have OKX combos?")
- "What columns does <dataset> have?"
- "What's the date range for <dataset>?"
- "Where does the <X> tape live in S3?"
- "How do I get to <data type>?" / "What's the path for <dataset>?"
- "What's missing / what data do we *not* have?"
- Before composing a DuckDB query when the user hasn't yet picked a dataset.

Do **not** fire for:
- Specific analytical questions that already name a known dataset path
  (hand straight to query authoring).
- Live exchange ticker / API calls (those are in `paradigm-block-analyst` and
  venue-specific skills).
- Paradex platform questions about positions, vaults, margin (different skills).

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
