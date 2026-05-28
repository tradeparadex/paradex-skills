# Instruments and strategy codes — Paradigm DRFQv2

Paradigm RFQs reference legs by **integer `instrument_id`**, not by
venue-native name. The skill must look up IDs before building an RFQ
payload.

## The lookup step (mandatory)

For any RFQ or order, resolve venue-native instrument names to Paradigm
ids:

```
GET /v2/drfq/instruments/?venue=DBT&venue_instrument_name=BTC-7MAY26-90000-C
```

Response (`PaginatedInstrumentList`):

```json
{
  "count": 1,
  "results": [
    {
      "id": 12345,
      "name": "BTC-7MAY26-90000-C",
      "venue": "DBT",
      "venue_instrument_name": "BTC-7MAY26-90000-C",
      "kind": "OPTION",
      "option_kind": "CALL",
      "strike": "90000",
      "margin_kind": "INVERSE",
      "base_currency": "BTC",
      "quote_currency": "BTC",
      "min_block_size": "0.1",
      "min_order_size_increment": "0.1",
      "min_tick_size": "0.0001",
      "product_code": "DO",
      "state": "ACTIVE"
    }
  ]
}
```

Use `id` as `legs[].instrument_id` in the RFQ create payload.

Cache lookups for the duration of a session — `id` is stable per
instrument, but `mark_price` / `min_block_size` etc. change. Don't cache
the full record beyond a few seconds.

## Venues

| Code | Venue | RFQ create accepts? |
|---|---|---|
| `BIT` | Bit.com | yes |
| `BYB` | Bybit | yes |
| `DBT` | Deribit | yes |
| `PRDX` | Paradex | yes |
| `RBN` `TTN` `BLT` `FBX` `FKN` `FTX` `SKD` `CME` | Other (read-only, historical, or settlement-only) | no for RFQ create |

Note: **OKX is not a DRFQv2 venue.** Cross-venue fair value lookups for
OKX in this skill go through OKX's public API directly (same pattern as
`paradigm-block-analyst`), not through Paradigm.

## Venue-native instrument naming

These are the names you'll paste into `venue_instrument_name` when
looking up an instrument id. For deeper venue notes see
[`../block-analyst/references/venues.md`](../../block-analyst/references/venues.md).

### Deribit (`venue: DBT`)

| Product | Format | Example |
|---|---|---|
| BTC option | `BTC-DDMMMYY-STRIKE-C/P` | `BTC-7MAY26-90000-C` |
| ETH option | `ETH-DDMMMYY-STRIKE-C/P` | `ETH-10MAY26-2375-P` |
| BTC future / perp | `BTC-DDMMMYY`, `BTC-PERPETUAL` | `BTC-27JUN26` |

Day not zero-padded. Month uppercase 3-letter.

### Bybit (`venue: BYB`)

| Product | Format | Example |
|---|---|---|
| BTC option | `BTC-DDMMMYY-STRIKE-C/P` | `BTC-07MAY26-90000-C` |

Day **is** zero-padded.

### Bit.com (`venue: BIT`)

| Product | Format | Example |
|---|---|---|
| BTC option | `BTC-DDMMMYY-STRIKE-C/P` | `BTC-07MAY26-90000-C` |

Day zero-padded.

### Paradex (`venue: PRDX`)

| Product | Format | Example |
|---|---|---|
| Perpetual | `<BASE>-USD-PERP` | `BTC-USD-PERP` |
| Future | `<BASE>-USD-<EXPIRY>` | `BTC-USD-27JUN26` |

Paradex doesn't have options on this venue at v1.0.

## Base currencies (RFQ-facing)

The OpenAPI restricts RFQ creation filter to these base currencies:
`AVAX BCH BTC ETH SOL TONCOIN TRX XRP`. The instrument catalog includes
far more (~140 entries) but those are read-only / legacy.

## Instrument kinds

- `OPTION` — calls and puts
- `FUTURE` — dated futures and perpetuals (Paradex perp shows up here)
- `LOAN` — used for some structured products
- `SPOT` — spot RFQ (out of scope for v1.0 of this skill)

## Margin kinds

- `INVERSE` — coin-margined (e.g. Deribit BTC options; prices in BTC)
- `LINEAR` — USDC/USDT-margined; prices in quote

The same option strike may exist as both INVERSE and LINEAR on the same
venue. Filter `margin_kind` to disambiguate.

## Strategy codes

Paradigm tags each RFQ with a `strategy_code` derived from the legs.
You **don't** set this when creating — Paradigm infers it. But recognise
the codes when echoing structure summaries.

| Code | Strategy |
|---|---|
| `CL` | Call |
| `CB` | Call Butterfly |
| `CC` | Call Calendar |
| `CD` | Call Condor |
| `CR` | Risk Reversal (Call) |
| `CS` | Call Spread |
| `CM` | Custom (multi-leg combo) |
| `PT` | Put |
| `PB` | Put Butterfly |
| `PC` | Put Calendar |
| `PD` | Put Condor |
| `PR` | Risk Reversal (Put) |
| `PS` | Put Spread |
| `SD` | Straddle |
| `SG` | Strangle |
| `FT` | Future |
| `FS` | Future Spread |
| `FF` | Iron Butterfly |
| `FD` | Iron Condor |
| `IB` | Inverse Call Butterfly |
| `VL` | Inverse Call Calendar |
| `VC` | Inverse Call Condor |
| `IC` | Inverse Call Spread |
| `IS` | Inverse Future Spread |
| `VF` | Inverse Iron Butterfly |
| `VD` | Inverse Iron Condor |
| `IY` | Inverse Put Butterfly |
| `VT` | Inverse Put Calendar |
| `VP` | Inverse Put Condor |
| `IP` | Inverse Put Spread |
| `ID` | Inverse Straddle |
| `IG` | Inverse Strangle |

**Important changes from the old block-analyst code-list:**

- `PT` is **Put** in DRFQv2 (was `PL` in some block-tape contexts).
- `CC` is **Call Calendar** (not Covered Call).
- `SG` is Strangle (was `SN`).
- `SD` is Straddle (was `ST`).
- `BF/CO/CA/RR` from the old list map to `CB/CD/CC/CR` (call side) or
  `PB/PD/PC/PR` (put side) depending on which leg type dominates.

The `paradigm-block-analyst` references file
[`strategy-codes.md`](../../block-analyst/references/strategy-codes.md)
predates this spec — when conflicts arise, the OpenAPI enum here is
authoritative.

## Out of scope for v1.0 of the skill

- Spot RFQ (`kind: SPOT`).
- Loan products (`kind: LOAN`).
- Custom multi-leg combos with > 4 legs.
- Inverse-strategy codes (`I*`, `V*`) — supported by the API but the
  skill's confirmation-gate UX is tuned for the linear strategy set.

The OpenAPI itself supports all of the above; the v1.0 limit is in the
skill's UX layer.
