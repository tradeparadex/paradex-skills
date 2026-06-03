# Enums and strategy codes — Paradigm DRFQv2

Venue-independent reference for the shapes in DRFQv2 payloads.
**Per-venue instrument naming lives in [`venues.md`](venues.md)**;
this file is the irreducible reference for `StrategyCodeEnum` (which
isn't documented in a digestible form anywhere else) plus brief notes
on the smaller enums the skill writes literally into tool calls.

## Instrument lookup

Resolve every leg via `paradigm_drfqv2_instruments(venue=...,
venue_instrument_name=...)` before building an RFQ. The response
carries `id`, `kind`, `option_kind`, `strike`, `margin_kind`,
`min_block_size`, `min_order_size_increment`, `min_tick_size`,
`state`. Use `id` as `legs[].instrument_id`; use `kind` to pick the
fair-value approach (see `venues.md`). Cache id + kind for the
session; never cache `mark_price` or sizing fields — those change.

## Enum cheat sheet

Most enums are self-evident from context and the MCP tool surface.
The ones with non-obvious dot-notation or non-trivial values:

| Enum | Values |
|---|---|
| `kind` | `OPTION`, `FUTURE` (incl. perp), `LOAN`, `SPOT` |
| `margin_kind` | `INVERSE` (coin-margined, prices in base) / `LINEAR` (quote-margined) |
| RFQ `state` | `RFQState.OPEN`, `RFQState.CLOSED`, `RFQState.DRAFT` |
| Order `state` | `OrderState.OPEN`, `OrderState.CLOSED`, `OrderState.PENDING` |
| Order `side` / `type` / `time_in_force` | `BUY`/`SELL`, `LIMIT`/`HIDDEN`, `FILL_OR_KILL`/`GOOD_TILL_CANCELED` |
| RFQ `closed_reason` | `CANCELED_BY_CREATOR`, `EXPIRED`, `EXECUTION_LIMIT`, `CLOSED_DRAFT` |
| `role` | `MAKER`, `TAKER` |
| BlockTrade `state` | `FILLED`, `PENDING_SETTLEMENT`, `REJECTED` |

**Failure terminals — stop fill-polling.** A non-fill RFQ
`closed_reason` (`EXPIRED`, `EXECUTION_LIMIT`, or a rejected / errored
close), an order that reaches a terminal state without a resulting
trade, and `BlockTrade state=REJECTED` are all **failures**, not
in-progress states. When any of them appears, halt the poll loop and
surface the error (see SKILL.md Step 3a · 7) — do not keep waiting for
a fill. Quote any `error` / `reason` / `message` / `code` fields the
tool payload carries verbatim; the enums above are coarse, so the raw
payload is where the actionable detail lives.

The same strike can exist as both `INVERSE` and `LINEAR` on the
same venue — filter on `margin_kind` when resolving by name to
disambiguate.

## Strategy codes (`StrategyCodeEnum`)

Paradigm assigns each RFQ a `strategy_code` inferred from its legs.
You don't set it on create — Paradigm does. Use this table to
interpret it when echoing structure summaries.

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

**Codes that differ from legacy block-tape conventions:** `PT` is
Put (legacy: `PL`). `CC` is Call Calendar (legacy: Covered Call).
`SG` is Strangle (legacy: `SN`). `SD` is Straddle (legacy: `ST`).
`BF` / `CO` / `CA` / `RR` map to `CB`/`CD` / `PB`/`PD` / `CC`/`PC`
/ `CR`/`PR` depending on call vs put dominance.

When in doubt, the live MCP response is authoritative — this table
is for human interpretation.

## Out of scope for this skill version

Spot RFQ (`kind: SPOT`), Loan products (`kind: LOAN`), combos with
> 4 legs, and inverse-strategy codes (`I*` / `V*`). The MCP server
supports all of the above; the limit is in the skill's UX layer.
