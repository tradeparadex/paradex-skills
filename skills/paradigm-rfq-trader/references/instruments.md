# Instruments and strategy codes — Paradigm DRFQv2

The **live catalog** lives in the MCP server — query it via
`paradigm_drfqv2_instruments`. This file documents the durable parts:
how venue-native instrument names are formatted, what the enum values
mean, and the strategy-code lookup table. Do not treat any specific
ID, strike, or expiry in this file as live data.

The skill's initial target is `venue=PRDX` (Paradex). Other DRFQv2
venues are documented below for reference and future scope.

## The lookup step (mandatory)

Paradigm RFQs reference legs by **integer `instrument_id`**, not by
venue-native name. For each leg of an RFQ:

```
paradigm_drfqv2_instruments(venue="PRDX", venue_instrument_name="BTC-USD-PERP")
```

Response shape (illustrative — the live catalog is what counts):

```json
{
  "results": [{
    "id": 98765,
    "name": "BTC-USD-PERP",
    "venue": "PRDX",
    "kind": "FUTURE",
    "margin_kind": "LINEAR",
    "base_currency": "BTC",
    "quote_currency": "USD",
    "min_block_size": "1",
    "min_order_size_increment": "0.001",
    "min_tick_size": "0.5",
    "state": "ACTIVE"
  }]
}
```

Use `id` as `legs[].instrument_id` when calling
`paradigm_drfqv2_create_rfq` or `paradigm_drfqv2_post_order`. Cache
the id for the session; do not cache `mark_price` or sizing fields —
those change.

## Venues

| Code | Venue | RFQ-create-accepted? |
|---|---|---|
| `BIT` | Bit.com | yes |
| `BYB` | Bybit | yes |
| `DBT` | Deribit | yes |
| `PRDX` | Paradex | yes |

Other codes (`RBN`, `TTN`, `BLT`, `FBX`, `FKN`, `FTX`, `SKD`, `CME`)
appear in historical / read-only contexts and aren't valid for new
RFQs.

OKX is **not** a Paradigm venue. OKX fair-value lookups in this skill
go through OKX's public API directly (same pattern as
`paradigm-block-analyst`), not through Paradigm.

## Venue-native naming

These are the names you pass to `venue_instrument_name` when resolving
ids:

### Deribit (`DBT`)

| Product | Format | Example |
|---|---|---|
| Option | `BTC-DDMMMYY-STRIKE-C/P` | `BTC-7MAY26-90000-C` |
| Future / perp | `BTC-DDMMMYY`, `BTC-PERPETUAL` | `BTC-27JUN26` |

Day **not** zero-padded. Month uppercase 3-letter.

### Bybit (`BYB`)

| Product | Format | Example |
|---|---|---|
| Option | `BTC-DDMMMYY-STRIKE-C/P` | `BTC-07MAY26-90000-C` |

Day zero-padded.

### Bit.com (`BIT`)

| Product | Format | Example |
|---|---|---|
| Option | `BTC-DDMMMYY-STRIKE-C/P` | `BTC-07MAY26-90000-C` |

Day zero-padded.

### Paradex (`PRDX`)

| Product | Format | Example |
|---|---|---|
| Perpetual | `<BASE>-USD-PERP` | `BTC-USD-PERP` |
| Future | `<BASE>-USD-<EXPIRY>` | `BTC-USD-27JUN26` |

## Base currencies

The RFQ-create surface accepts a subset of currencies. Don't hard-code
the list here — it's spec-derived and grows. Always check the live
catalog via `paradigm_drfqv2_instruments` if a base currency is
in doubt. As of this skill's writing, common bases on the RFQ-create
filter include `BTC ETH SOL AVAX BCH TONCOIN TRX XRP`.

## Instrument kinds

- `OPTION` — calls and puts (this skill's primary scope)
- `FUTURE` — dated futures and perpetuals
- `LOAN` — structured products
- `SPOT` — out of scope at this skill version

## Margin kinds

- `INVERSE` — coin-margined (Deribit BTC options; prices in BTC)
- `LINEAR` — USDC/USDT-margined; prices in quote

The same strike can exist as both INVERSE and LINEAR on the same
venue. Filter `margin_kind` to disambiguate.

## Strategy codes (`StrategyCodeEnum`)

Paradigm tags each RFQ with a `strategy_code` inferred from the legs.
You don't set this on create — Paradigm assigns it. Use this table
to interpret it when echoing structure summaries.

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
| `IB` `VL` `VC` `IC` `IS` `VF` `VD` `IY` `VT` `VP` `IP` `ID` `IG` | Inverse variants of the above |

**Codes that differ from legacy block-tape conventions:**

- `PT` is Put in DRFQv2 (legacy: `PL`).
- `CC` is Call Calendar in DRFQv2 (legacy: Covered Call).
- `SG` is Strangle (legacy: `SN`).
- `SD` is Straddle (legacy: `ST`).
- `BF` / `CO` / `CA` / `RR` map to `CB`/`CD` / `PB`/`PD` / `CC`/`PC` /
  `CR`/`PR` depending on call vs put dominance.

When in doubt, the live `paradigm_drfqv2_*` tool surface is
authoritative — this table is for human interpretation only.

## Out of scope for this skill version

- Spot RFQ (`kind: SPOT`).
- Loan products (`kind: LOAN`).
- Custom multi-leg combos with > 4 legs.
- Inverse-strategy codes (`I*`, `V*`) — supported by the API; the
  skill's confirmation-gate UX is tuned for the linear set.

The MCP server supports all of the above; the limit is in the skill's
UX layer.
