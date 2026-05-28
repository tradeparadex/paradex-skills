# Enums and strategy codes — Paradigm DRFQv2

Venue-independent reference for the enum values you'll see in
RFQ / order payloads and `paradigm_drfqv2_instruments` responses.

**Per-venue instrument naming lives in
[`venues.md`](venues.md)**, not here. This file documents the
shapes and codes that are the same regardless of where an RFQ
settles.

## The lookup step (mandatory)

Paradigm RFQs reference legs by **integer `instrument_id`**, not by
venue-native name. For each leg of an RFQ:

```
paradigm_drfqv2_instruments(venue=<venue>, venue_instrument_name=<name>)
```

The response carries:

```json
{
  "results": [{
    "id": <integer>,
    "name": "<venue-native name>",
    "venue": "<venue code>",
    "kind": "OPTION" | "FUTURE" | "LOAN" | "SPOT",
    "option_kind": "CALL" | "PUT" | null,
    "strike": "<decimal string>",
    "margin_kind": "INVERSE" | "LINEAR",
    "base_currency": "<code>",
    "quote_currency": "<code>",
    "min_block_size": "<decimal>",
    "min_order_size_increment": "<decimal>",
    "min_tick_size": "<decimal>",
    "state": "ACTIVE" | "EXPIRED"
  }]
}
```

Use `id` as `legs[].instrument_id` when calling `create_rfq` /
`post_order`. Use `kind` to pick the fair-value approach (see
`venues.md`). Cache id + kind for the session; never cache
`mark_price` or sizing fields — those change.

## Instrument kinds

| Kind | Meaning |
|---|---|
| `OPTION` | Calls and puts |
| `FUTURE` | Dated futures and perpetuals (perp is a FUTURE with no expiry) |
| `LOAN` | Structured-product leg |
| `SPOT` | Spot pairs (out of skill scope) |

## Margin kinds

| Kind | Meaning |
|---|---|
| `INVERSE` | Coin-margined. Prices and PnL in the base currency. Common for Deribit BTC options |
| `LINEAR` | Quote-margined (USDC / USDT). Prices and PnL in quote |

The same strike can exist as both `INVERSE` and `LINEAR` on the same
venue. Filter on `margin_kind` when resolving by `venue_instrument_name`
to disambiguate.

## Strategy codes (`StrategyCodeEnum`)

Paradigm assigns each RFQ a `strategy_code` inferred from the legs.
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
| `FT` | Future (outright perp / dated future) |
| `FS` | Future Spread (calendar) |
| `FF` | Iron Butterfly |
| `FD` | Iron Condor |
| `IB` `VL` `VC` `IC` `IS` `VF` `VD` `IY` `VT` `VP` `IP` `ID` `IG` | Inverse variants of the above |

**Codes that differ from legacy block-tape conventions:**

- `PT` is Put in DRFQv2 (legacy: `PL`).
- `CC` is Call Calendar in DRFQv2 (legacy: Covered Call).
- `SG` is Strangle (legacy: `SN`).
- `SD` is Straddle (legacy: `ST`).
- `BF` / `CO` / `CA` / `RR` map to `CB`/`CD` / `PB`/`PD` /
  `CC`/`PC` / `CR`/`PR` depending on call vs put dominance.

When in doubt, the live `paradigm_drfqv2_*` tool surface is
authoritative — this table is for human interpretation only.

## Base currencies

The RFQ-create surface accepts a subset of currencies that grows
over time. Don't hard-code the list here; the live catalog via
`paradigm_drfqv2_instruments` is the source of truth. Common bases
include `BTC ETH SOL AVAX BCH TONCOIN TRX XRP`.

## RFQ / order state enums

| Enum | Values |
|---|---|
| RFQ state | `RFQState.OPEN`, `RFQState.CLOSED`, `RFQState.DRAFT` |
| Order state | `OrderState.OPEN`, `OrderState.CLOSED`, `OrderState.PENDING` |
| BlockTrade state | `FILLED`, `PENDING_SETTLEMENT`, `REJECTED` |
| RFQ closed reason | `CANCELED_BY_CREATOR`, `EXPIRED`, `EXECUTION_LIMIT`, `CLOSED_DRAFT` |
| Role | `MAKER`, `TAKER` |

## Order create enums

| Enum | Values |
|---|---|
| Side | `BUY`, `SELL` |
| Type | `LIMIT`, `HIDDEN` |
| Time in force | `FILL_OR_KILL`, `GOOD_TILL_CANCELED` |

## Out of scope for this skill version

- Spot RFQ (`kind: SPOT`).
- Loan products (`kind: LOAN`).
- Custom multi-leg combos with > 4 legs.
- Inverse-strategy codes (`I*`, `V*`) — supported by the API; the
  skill's confirmation-gate UX is tuned for the linear set.

The MCP server supports all of the above; the limit is in the
skill's UX layer.
