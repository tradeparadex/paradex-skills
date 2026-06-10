# Available Market Data — S3 Catalog

Comprehensive map of market data accessible via DuckDB + S3. Covers crypto
options (Paradigm RFQ flow, exchange option data, Bullish), ETF
options (IBIT), crypto futures top-of-book, and the on-chain Paradex perp
trade tape.

> **Date ranges below are point-in-time as of last verification: 2026-05-11.**
> Coverage expands over time — for any "recent date" question, run the glob
> probe in `SKILL.md` Step 3 to confirm.

---

## S3 Bucket

- **Bucket:** `s3://terminal-dime-prod`
- **Region:** `ap-northeast-1`
- **Auth:** IRSA (EKS web identity → STS) — see `s3-access.md`.

---

## Dataset 1 — Paradigm Block Trade Tape

Paradigm RFQ block flow. Primary source for options block trades executed via
Paradigm across Deribit, Paradex, and Bybit.

### 1a. `paradigm_trade_tape_slim` — Executed Block Trades

- **Path:** `s3://terminal-dime-prod/paradigm_data/paradigm_trade_tape_slim.csv.gz`
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

- **Path:** `s3://terminal-dime-prod/paradigm_data/paradigm_rfq_tape_slim.csv.gz`
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

## Dataset 2 — Deribit & OKX Options

Raw exchange market data. Useful for vol surface
construction, execution benchmarking, and Greeks calculation.

### 2a. Deribit — Option Trades

- **Path:** `s3://terminal-dime-prod/external/tardis/v1/trades/option/deribit/YYYY/MM/DD/deribit-OPTIONS-YYYY-MM-DD.csv.gz`
- **Last verified coverage:** 2026-02-01 → 2026-04-30 (1 file/day)
- **Layout:** Daily partitioned. Most consistently populated options dataset. Likely extends forward.
- **Assets:** BTC + ETH dated options
- **Instrument format:** `BTC-27FEB26-60000-P`, `ETH-6FEB26-3000-C`

| Column | Type | Notes |
|---|---|---|
| `exchange` | varchar | Always `deribit` |
| `symbol` | varchar | e.g. `BTC-27FEB26-95000-C` |
| `timestamp` | bigint | Microseconds since epoch (UTC) |
| `local_timestamp` | bigint | Receipt timestamp (µs) |
| `id` | varchar | Trade ID |
| `side` | varchar | `buy` / `sell` (taker side) |
| `price` | double | In BTC/ETH (index currency, not USD) |
| `amount` | double | Contracts |

**Example query:**

```sql
SELECT symbol, side, price, amount,
  to_timestamp(timestamp / 1e6) AS ts
FROM read_csv_auto('s3://terminal-dime-prod/external/tardis/v1/trades/option/deribit/2026/03/01/deribit-OPTIONS-2026-03-01.csv.gz')
WHERE symbol LIKE 'BTC-%'
ORDER BY timestamp;
```

---

### 2b. Deribit — Option Quotes / Top-of-Book

- **Path:** `s3://terminal-dime-prod/external/tardis/v1/quotes/option/deribit/YYYY/MM/DD/deribit-OPTIONS-YYYY-MM-DD.csv.gz`
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

- **Path:** `s3://terminal-dime-prod/external/tardis/v1/quotes/combo/deribit/YYYY/MM/DD/deribit-<ASSET>-<STRATEGY>-<LEGS>-YYYY-MM-DD.csv.gz`
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
  's3://terminal-dime-prod/external/tardis/v1/quotes/combo/deribit/2026/03/15/deribit-BTC-*.csv.gz'
);
```

---

### 2d. OKX — Option Trades

- **Path:** `s3://terminal-dime-prod/external/tardis/v1/trades/option/okex-options/YYYY/MM/DD/okex-options-OPTIONS-YYYY-MM-DD.csv.gz`
- **Last verified coverage:** 2026-02-01 → 2026-04-30 (~87 files)
- **Layout:** Daily partitioned. Parallel to Deribit trades but may diverge — verify independently.
- **Schema:** Same as Deribit option trades (`exchange, symbol, timestamp, local_timestamp, id, side, price, amount`).

---

### 2e. Future Quotes / Top-of-Book (Deribit, Bybit, OKX)

Top-of-book quote snapshots for dated and perpetual futures across three
venues. Same schema as Deribit option quotes.

| Venue | Path | Last verified coverage | Files |
|---|---|---|---|
| Deribit | `s3://terminal-dime-prod/external/tardis/v1/quotes/future/deribit/YYYY/MM/DD/` | 2026-01-01 → 2026-05-09 | ~2,186 |
| Bybit | `s3://terminal-dime-prod/external/tardis/v1/quotes/future/bybit/YYYY/MM/DD/` | 2026-01-01 → 2026-05-09 | ~4,442 |
| OKX | `s3://terminal-dime-prod/external/tardis/v1/quotes/future/okex-futures/YYYY/MM/DD/` | 2026-03-01 → 2026-05-09 | ~1,858 |

**Schema** (identical to Deribit option quotes):

| Column | Type | Notes |
|---|---|---|
| `exchange` | varchar | `deribit` / `bybit` / `okex-futures` |
| `symbol` | varchar | Venue-native future symbol |
| `timestamp` | bigint | Microseconds since epoch (UTC) |
| `local_timestamp` | bigint | Receipt timestamp (µs) |
| `ask_amount` | double | |
| `ask_price` | double | |
| `bid_price` | double | |
| `bid_amount` | double | |

Use these for funding-context, basis, and term-structure analysis alongside
the option datasets.

---

## Dataset 3 — Bullish (Options)

Live snapshots of option chain and order book from the Bullish exchange.
Unlike the option/future feed data, the chain snapshot dataset includes **greeks and IV directly**.

### 3a. Bullish — Option Chain Snapshots

- **Path:** `s3://terminal-dime-prod/paradigm_data/bullish_option_chain_snapshots/date=YYYY-MM-DD/*.csv.gz`
- **Last verified coverage:** 2026-05-07 → 2026-05-11 (very recent, ongoing)
- **Layout:** Hive-style date partition (`date=YYYY-MM-DD`).

| Column | Type | Notes |
|---|---|---|
| `SNAPSHOT_AT` | timestamp | Snapshot time (UTC) |
| `SYMBOL` | varchar | Bullish option symbol |
| `BASE_SYMBOL` | varchar | Underlying (e.g. `BTC`) |
| `EXPIRY` | timestamp/date | Option expiry |
| `STRIKE` | double | |
| `OPTION_TYPE` | varchar | `CALL` / `PUT` |
| `BID` | double | |
| `BID_QTY` | double | |
| `ASK` | double | |
| `ASK_QTY` | double | |
| `BID_IV_PCT` | double | Bid IV (percent) |
| `ASK_IV_PCT` | double | Ask IV (percent) |
| `IV` | double | Mid/mark IV |
| `MARK_PRICE` | double | |
| `LAST` | double | Last trade price |
| `OPEN_INTEREST` | double | |
| `OPEN_INTEREST_USD` | double | |
| `UNDERLYING_PRICE` | double | |
| `SETTLEMENT_ASSET` | varchar | |
| `DELTA` | double | |
| `GAMMA` | double | |
| `THETA` | double | |
| `VEGA` | double | |

This is the **only dataset in the catalog with native greeks/IV** — preferred
over computing them from raw option trades when Bullish coverage applies.

---

### 3b. Bullish — Options Order Book (Historical, Top-2 Levels)

- **Path:** `s3://terminal-dime-prod/paradigm_data/bullish_options_orderbook_historical/date=YYYY-MM-DD/*.csv.gz`
- **Last verified coverage:** 2026-02-01 → 2026-04-25 (~84 files)
- **Layout:** Hive-style date partition (`date=YYYY-MM-DD`).
- **Depth:** Top-2 bid + top-2 ask levels.

| Column | Type | Notes |
|---|---|---|
| `SNAPSHOT_TIME` | timestamp | When snapshot was taken |
| `EXCHANGE_TIMESTAMP` | timestamp | Bullish-side timestamp |
| `MARKET_PAIR` | varchar | Bullish market pair identifier |
| `ASK1_PRICE` | double | Best ask price |
| `ASK1_QTY` | double | Best ask size |
| `ASK2_PRICE` | double | Second-best ask price |
| `ASK2_QTY` | double | Second-best ask size |
| `BID1_PRICE` | double | Best bid price |
| `BID1_QTY` | double | Best bid size |
| `BID2_PRICE` | double | Second-best bid price |
| `BID2_QTY` | double | Second-best bid size |

---

## Dataset 4 — IBIT ETF Options Trades

Equity-side vol data: trades on options for the IBIT Bitcoin ETF.
Not crypto-native — useful as a cross-asset reference against Deribit
BTC option flow.

- **Path:** `s3://terminal-dime-prod/paradigm_data/ibit_options_trades/date=YYYY-MM-DD/*.csv.gz`
- **Last verified coverage:** 2025-12-01 → 2026-05-08 (~110 files, trading-day partitioned)
- **Layout:** Hive-style date partition. Weekends/holidays absent (US equity calendar).

| Column | Type | Notes |
|---|---|---|
| `TRADE_DATE` | date | |
| `TS_RECV` | timestamp | Receipt timestamp |
| `SYMBOL` | varchar | IBIT option symbol (OCC-style) |
| `PRICE` | double | Trade price (USD) |
| `SIZE` | bigint | Contracts (1 contract = 100 shares) |
| `SIDE` | varchar | `BUY` / `SELL` |

**Use cases:** ETF vs crypto-native skew comparison, weekend-gap analysis,
spot-ETF flow vs crypto-spot flow.

---

## Dataset 5 — Paradex DEX Trade Tape

On-chain Paradex perpetual trades — the *executed-trade* tape of the
Paradex DEX. This is **historical Paradex trade data** and is in scope for
this catalog; live Paradex markets, positions, orderbook, funding, and
account data remain out of scope (route those to the Paradex-specific
skills).

- **Path:** `s3://terminal-dime-prod/paradex_data/paradex_trade_tape.csv.gz`
  (plus Parquet parts in the same prefix)
- **Last verified coverage:** starts 2024-12-26 (full range TBC — run the
  coverage probe before assuming an end-date).
- **Layout:** Flat CSV + Parquet parts. Confirm with `glob()` before querying.

| Column | Type | Notes |
|---|---|---|
| `IS_TRADEBUST` | boolean | True if trade was busted / cancelled |
| `MARKET` | varchar | Paradex market symbol (e.g. `BTC-USD-PERP`) |
| `PRICE` | double | Trade price (USD) |
| `SIZE` | double | Contracts |
| `TAKER_SIDE` | varchar | `BUY` / `SELL` (taker direction) |
| `TRADE_AT` | timestamp | Trade time (UTC) |

**Filter out busted trades:** `WHERE NOT IS_TRADEBUST`.

---

## What Is NOT Here

- **Deribit option quotes beyond 2026-01-01** — only trades are consistently available.
- **OKX combo quotes** — not present.
- **OKX option quotes** — not present in this catalog.
- **Greeks / IV in the option/future feed data** — not present; either compute them from
  trades/underlying, or use Bullish option chain snapshots (Dataset 3a) where
  coverage overlaps.
- **Paradex options data** — Paradex options are everlasting/perpetual style
  with no expiry date and are excluded from this catalog. Paradex options
  *block-trade* flow is visible in the Paradigm trade tape under
  `BTC OPTION - PRDX`. Paradex *perp* trade flow is in Dataset 5.
- **Live / streaming feeds** — everything here is historical S3-backed data.
  For live tickers, marks, and account state, route to the live-data skills.

---

## Cross-Dataset Notes

- **Timestamp units:**
  - Option/future market data (`timestamp`, `local_timestamp`): **microseconds** since epoch →
    `to_timestamp(timestamp / 1e6)` in DuckDB.
  - Paradigm tapes: split `DATE` + `TIME` columns.
  - Bullish, IBIT, Paradex tape: native `timestamp` types (seconds resolution).
- **Deribit option price units:** **index currency** (BTC for BTC options,
  ETH for ETH options), not USD.
- **Deribit amount units:** contracts (1 BTC contract = 1 BTC notional;
  1 ETH contract = 1 ETH notional).
- **IBIT amount units:** US-equity-option contracts (1 contract = 100 shares).
- **Joining Paradigm + market data:** Use `RFQ_ID` / `BLOCK_TRADE_ID` on the
  Paradigm side; join to the option/future market data by symbol + timestamp
  window for market context. For Bullish or IBIT cross-references, join on
  symbol + snapshot time.
- **Paradigm options filter:** `WHERE PRODUCT LIKE '%OPTION%'`.
- **Paradigm exchange suffix:** `DBT` = Deribit, `PRDX` = Paradex,
  `BYB` = Bybit.
- **Greeks/IV preference:** Bullish chain snapshots (Dataset 3a) are the
  only source of native greeks/IV. Where coverage overlaps with the
  option/future market data, prefer Bullish for delta/gamma/theta/vega/IV
  rather than computing.
- **Hive-style partitions:** IBIT, Bullish chain, Bullish orderbook use
  `date=YYYY-MM-DD/` prefixes (different from the daily `YYYY/MM/DD/`
  partition used for option/future market data). The coverage-probe regex
  in `SKILL.md` Step 3 only matches the daily layout — for Hive-partitioned
  datasets, use:

  ```sql
  SELECT
    MIN(regexp_extract(file, 'date=(\d{4}-\d{2}-\d{2})', 1)) AS earliest,
    MAX(regexp_extract(file, 'date=(\d{4}-\d{2}-\d{2})', 1)) AS latest,
    COUNT(*) AS file_count
  FROM glob('<path-with-**>');
  ```
