# Available Options Market Data — S3 Catalog

Comprehensive map of options market data accessible via DuckDB + S3.

> **Date ranges below are point-in-time as of last verification: 2026-05-11.**
> Coverage expands over time — for any "recent date" question, run the glob
> probe in `SKILL.md` Step 3 to confirm.

---

## S3 Bucket

- **Bucket:** `s3://terminal-paradigm-prod`
- **Region:** `eu-west-2`
- **Auth:** IRSA (EKS web identity → STS) — see `s3-access.md`.

---

## Dataset 1 — Paradigm Block Trade Tape

Paradigm RFQ block flow. Primary source for options block trades executed via
Paradigm across Deribit, Paradex, and Bybit.

### 1a. `paradigm_trade_tape_slim` — Executed Block Trades

- **Path:** `s3://terminal-paradigm-prod/paradigm_data/paradigm_trade_tape_slim.csv.gz`
- **Last verified coverage:** 2025-11-09 → 2026-05-09
- **Layout:** Single flat CSV — all dates in one file. Coverage likely extends forward.

| Column | Type | Notes |
|---|---|---|
| `DATE` | date | Trade date |
| `TIME` | time | Trade time (UTC) |
| `AUCTION` | varchar | `RFQ` or `OB` (order book) |
| `PRODUCT` | varchar | See product types below |
| `DESCRIPTION` | varchar | Human-readable strategy description |
| `QTY` | double | Quantity (contracts) |
| `PRICE` | double | Execution price |
| `REF_PRICE` | double | Reference/mark price at time of trade |
| `SIDE` | varchar | `BUY` / `SELL` (taker side) |
| `QUOTE_CURRENCY` | varchar | `BTC`, `ETH`, `USD`, etc. |
| `NOTIONAL_VOLUME_USD` | double | USD notional |
| `RFQ_ID` | varchar | Links to RFQ tape |
| `TRADE_ID` | varchar | Unique trade identifier |
| `BLOCK_TRADE_ID` | varchar | Block trade group identifier |

**Product types in trade tape:**

| PRODUCT | Description |
|---|---|
| `BTC OPTION - DBT` | BTC options on Deribit (~21k trades) |
| `ETH OPTION - DBT` | ETH options on Deribit (~11k trades) |
| `SOL OPTION - DBT` | SOL options on Deribit |
| `XRP OPTION - DBT` | XRP options on Deribit |
| `BTC OPTION - PRDX` | BTC options on Paradex |
| `BTC PERPETUAL - DBT` | BTC perps on Deribit |
| `ETH PERPETUAL - DBT` | ETH perps on Deribit |
| `BTC FUTURE - DBT` | BTC futures on Deribit |
| `ETH FUTURE - DBT` | ETH futures on Deribit |

**`DESCRIPTION` examples (options strategies):**

- Outright: `Call 26 Dec 25 104000`, `Put 23 Jan 26 95000`
- Straddle: `Straddle 19 Nov 25 3050`
- Strangle: `Strangle 27 Mar 26 90000/95000`
- Call Spread: `CSpd 27 Mar 26 85000/110000`
- Put Spread: `PSpd 16 Jan 26 95000/93000`
- Risk Reversal: `RRCall 30 Jan 26 70000/108000`
- Iron Fly: `IFly 26 Jun 26 75000/85000/95000`
- Put Fly: `PFly 27 Mar 26 60000/50000/40000`
- Call Calendar: `CCal 27 Feb 26 75000 / 26 Jun 26 75000`
- Put Calendar: `PCal 26 Dec 25 86000 / 27 Mar 26 85000`
- Custom multi-leg: `Cstm +1.00 Call 24 Apr 26 78000 -2.00 Call 24 Apr 26 85000`

**Filter for options only:** `WHERE PRODUCT LIKE '%OPTION%'`

---

### 1b. `paradigm_rfq_tape_slim` — RFQ Activity (including unfilled)

- **Path:** `s3://terminal-paradigm-prod/paradigm_data/paradigm_rfq_tape_slim.csv.gz`
- **Last verified coverage:** 2025-11-09 → 2026-05-09
- **Layout:** Single flat CSV. Includes both completed and expired/uncompleted RFQs.

| Column | Type | Notes |
|---|---|---|
| `DATE` | date | RFQ date |
| `TIME` | time | RFQ creation time (UTC) |
| `AUCTION` | varchar | `RFQ` or `OB` |
| `PRODUCT` | varchar | Same product taxonomy as trade tape |
| `DESCRIPTION` | varchar | Strategy description |
| `QTY` | double | Requested quantity |
| `QUOTE_CURRENCY` | varchar | |
| `NOTIONAL_VOLUME_USD` | double | USD notional |
| `NUMBER_OF_QUOTES` | bigint | Quotes received from MMs |
| `NUMBER_OF_BLOCK_TRADES` | bigint | Trades executed (0 = unfilled) |
| `COMPLETED_STATUS` | varchar | e.g. `COMPLETE`, `EXPIRED` |
| `LIFESPAN_SECONDS` | bigint | How long the RFQ was live |
| `RFQ_ID` | varchar | Links to trade tape |

**Additional product types present in RFQ tape only:**

| PRODUCT | Description |
|---|---|
| `BTC OPTION_FUTURE - DBT` | BTC option+future combo on Deribit (~1,100 RFQs) |
| `ETH OPTION_FUTURE - DBT` | ETH option+future combo on Deribit |
| `AVAX OPTION - DBT` | AVAX options on Deribit |
| `BTC OPTION - BYB` | BTC options on Bybit |

---

## Dataset 2 — Deribit & OKX Options via Tardis

Raw exchange market data sourced from Tardis. Useful for vol surface
construction, execution benchmarking, and Greeks calculation.

### 2a. Deribit — Option Trades

- **Path:** `s3://terminal-paradigm-prod/external/tardis/v1/trades/option/deribit/YYYY/MM/DD/deribit-OPTIONS-YYYY-MM-DD.csv.gz`
- **Last verified coverage:** 2026-02-01 → 2026-04-30 (1 file/day)
- **Layout:** Daily partitioned. Most consistently populated options dataset. Likely extends forward.
- **Assets:** BTC + ETH dated options
- **Instrument format:** `BTC-27FEB26-60000-P`, `ETH-6FEB26-3000-C`

| Column | Type | Notes |
|---|---|---|
| `exchange` | varchar | Always `deribit` |
| `symbol` | varchar | e.g. `BTC-27FEB26-95000-C` |
| `timestamp` | bigint | Microseconds since epoch (UTC) |
| `local_timestamp` | bigint | Tardis receipt timestamp (µs) |
| `id` | varchar | Trade ID |
| `side` | varchar | `buy` / `sell` (taker side) |
| `price` | double | In BTC/ETH (index currency, not USD) |
| `amount` | double | Contracts |

**Example query:**

```sql
SELECT symbol, side, price, amount,
  to_timestamp(timestamp / 1e6) AS ts
FROM read_csv_auto('s3://terminal-paradigm-prod/external/tardis/v1/trades/option/deribit/2026/03/01/deribit-OPTIONS-2026-03-01.csv.gz')
WHERE symbol LIKE 'BTC-%'
ORDER BY timestamp;
```

---

### 2b. Deribit — Option Quotes / Top-of-Book

- **Path:** `s3://terminal-paradigm-prod/external/tardis/v1/quotes/option/deribit/YYYY/MM/DD/deribit-OPTIONS-YYYY-MM-DD.csv.gz`
- **Last verified coverage:** 2026-01-01 only (1 file — currently sparse)
- **Layout:** Daily partitioned. Limited — check for newer files before assuming unavailable.

| Column | Type | Notes |
|---|---|---|
| `exchange` | varchar | |
| `symbol` | varchar | Same format as trades |
| `timestamp` | bigint | µs epoch |
| `local_timestamp` | bigint | |
| `ask_amount` | double | |
| `ask_price` | double | |
| `bid_price` | double | |
| `bid_amount` | double | |

---

### 2c. Deribit — Combo Quotes

- **Path:** `s3://terminal-paradigm-prod/external/tardis/v1/quotes/combo/deribit/YYYY/MM/DD/deribit-<ASSET>-<STRATEGY>-<LEGS>-YYYY-MM-DD.csv.gz`
- **Last verified coverage:** 2026-01-01 → 2026-05-09 (~23,055 files)
- **Layout:** Daily partitioned, one file per combo instrument per day. Densest dataset. Ongoing.
- **Assets:** BTC + ETH
- **Schema:** `exchange, symbol, timestamp, local_timestamp, ask_amount, ask_price, bid_price, bid_amount`

**Combo instrument naming:**

| Code | Strategy | Example |
|---|---|---|
| `CS` | Call Spread | `BTC-CS-16JAN26-95000_100000` |
| `CCAL` | Call Calendar Spread | `BTC-CCAL-9JAN26_2JAN26-90000` |
| `CDIAG` | Call Diagonal | `BTC-CDIAG-26JUN26_2JAN26-60000_90000` |
| `FS` | Futures Calendar Spread | `BTC-FS-25DEC26_26JUN26` |
| `CSR12` / `CSR13` / `CSR23` | Ratio Call Spreads | `BTC-CSR12-16JAN26-88000_92000` |

**Glob pattern for all BTC combos on a given day:**

```sql
SELECT * FROM read_csv_auto(
  's3://terminal-paradigm-prod/external/tardis/v1/quotes/combo/deribit/2026/03/15/deribit-BTC-*.csv.gz'
);
```

---

### 2d. OKX — Option Trades

- **Path:** `s3://terminal-paradigm-prod/external/tardis/v1/trades/option/okex-options/YYYY/MM/DD/okex-options-OPTIONS-YYYY-MM-DD.csv.gz`
- **Last verified coverage:** 2026-02-01 → 2026-04-30 (~87 files)
- **Layout:** Daily partitioned. Parallel to Deribit trades but may diverge — verify independently.
- **Schema:** Same as Deribit option trades (`exchange, symbol, timestamp, local_timestamp, id, side, price, amount`).

---

## What Is NOT Here

- **Deribit option quotes beyond 2026-01-01** — only trades are consistently available.
- **OKX combo quotes** — not present.
- **OKX option quotes** — not present in this catalog.
- **Greeks / IV** — not in raw Tardis data; must be calculated or sourced separately.
- **Paradex options data in Tardis** — Paradex options are everlasting/perpetual
  style with no expiry date, not directly comparable to dated options, and are
  excluded from this catalog. Paradex flow is visible in the Paradigm trade tape
  under `BTC OPTION - PRDX`.

---

## Cross-Dataset Notes

- **Timestamp units:** Tardis timestamps are **microseconds** since epoch →
  `to_timestamp(timestamp / 1e6)` in DuckDB.
- **Deribit price units:** Option prices are in **index currency**
  (BTC for BTC options, ETH for ETH options), not USD.
- **Deribit amount units:** **Contracts** (1 BTC contract = 1 BTC notional;
  1 ETH contract = 1 ETH notional).
- **Joining Paradigm + Tardis:** Use `RFQ_ID` / `BLOCK_TRADE_ID` on the
  Paradigm side; join to Tardis by symbol + timestamp window for market
  context.
- **Paradigm options filter:** `WHERE PRODUCT LIKE '%OPTION%'`.
- **Paradigm exchange suffix:** `DBT` = Deribit, `PRDX` = Paradex,
  `BYB` = Bybit.
