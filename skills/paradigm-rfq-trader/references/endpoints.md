# Endpoints — REST fallback for Paradigm DRFQv2

For the **fallback path** when `mcp-paradigm-py` isn't available. On
the MCP path, callers use the typed tools listed in `../SKILL.md` and
don't need to know endpoint paths or payload shapes.

Authoritative source: the OpenAPI spec at
[`tradeparadigm/mono#34164`](https://github.com/tradeparadigm/mono/pull/34164).
The MCP server is generated from that spec. When the spec advances,
regenerate; don't hand-edit this file beyond what's needed for
fallback diagnosis.

## Mental model

DRFQv2 has **two object types**: `RFQ` and `Order`. Maker quotes and
taker crosses both use `POST /v2/drfq/orders/` against an existing
RFQ — side + time_in_force distinguish them.

## MCP tool → REST endpoint map

| MCP tool | REST |
|---|---|
| `paradigm_echo` | `GET` / `POST /v2/drfq/echo/` |
| `paradigm_drfqv2_instruments` | `GET /v2/drfq/instruments/` (and `{id}/`) |
| `paradigm_drfqv2_counterparties` | `GET /v2/drfq/counterparties/` |
| `paradigm_drfqv2_rfqs` | `GET /v2/drfq/rfqs/` (and `{id}/`) |
| `paradigm_drfqv2_rfq_snapshot` | composite — `GET /v2/drfq/rfqs/{id}/` + `/bbo/` + `/orders/` |
| `paradigm_drfqv2_create_rfq` | `POST /v2/drfq/rfqs/` |
| `paradigm_drfqv2_orders` | `GET /v2/drfq/orders/` (and `{id}/`) |
| `paradigm_drfqv2_post_order` | `POST /v2/drfq/orders/` |
| `paradigm_drfqv2_cancel` | `DELETE /v2/drfq/rfqs/{id}` or `DELETE /v2/drfq/orders/` (batch) or `DELETE /v2/drfq/orders/{id}` |
| `paradigm_drfqv2_trades` | `GET /v2/drfq/trades/` (and `{id}/`); plus `/v2/drfq/trade_tape/` for public anonymous tape |
| `paradigm_drfqv2_price_legs` | `POST /v2/drfq/pricing/` |
| `paradigm_drfqv2_mmp` | `GET` and `PATCH /v2/drfq/mmp/status/` |
| `paradigm_desk_overview` | composite — multiple reads across products |
| `paradigm_kill_switch` | composite — cancel-all across products |

## Key payload shapes

### `POST /v2/drfq/rfqs/`

```json
{
  "account_name": "trading-desk-1",
  "counterparties": ["LP1", "LP2"],
  "is_taker_anonymous": false,
  "label": "rfq-trader-1745612345678",
  "legs": [
    {"instrument_id": 12345, "ratio": "1", "side": "BUY",  "price": "0.0041"},
    {"instrument_id": 12346, "ratio": "1", "side": "SELL", "price": "0.0020"}
  ],
  "quantity": "100",
  "venue": "DBT",
  "state": "RFQState.OPEN"
}
```

### `POST /v2/drfq/orders/` (maker quote OR taker cross)

```json
{
  "account_name": "trading-desk-1",
  "label": "rfq-trader-q-1745612345678",
  "rfq_id": "rfq_abc123",
  "side": "BUY",
  "type": "LIMIT",
  "time_in_force": "GOOD_TILL_CANCELED",
  "price": "0.0045",
  "quantity": "100",
  "legs": [{"instrument_id": 12345, "price": "0.0045"}]
}
```

Endpoint is **async-first**: response is
`state: "OrderState.PENDING"` with zeroed fill quantities. Poll the
order to see terminal state.

### `DELETE /v2/drfq/orders/` (batch)

Returns `{successes: {count, order_ids[]}, failures: {count,
order_ids[]}}`. 200 = all succeeded; **207 = partial** — surface
partials, do not treat as failure.

## Enums

| Enum | Values |
|---|---|
| Venue (RFQ create) | `BIT BYB DBT PRDX` |
| Side | `BUY SELL` |
| Order type | `LIMIT HIDDEN` |
| Order TIF | `FILL_OR_KILL GOOD_TILL_CANCELED` |
| Instrument kind | `OPTION FUTURE LOAN SPOT` |
| Option kind | `CALL PUT` |
| Margin kind | `INVERSE LINEAR` |
| RFQ state | `RFQState.OPEN RFQState.CLOSED RFQState.DRAFT` |
| Order state | `OrderState.OPEN OrderState.CLOSED OrderState.PENDING` |
| BlockTrade state | `FILLED PENDING_SETTLEMENT REJECTED` |
| Role | `MAKER TAKER` |

See `instruments.md` for `StrategyCodeEnum` (32 codes) and venue
naming.

## WebSocket (planned in the MCP, available now via direct connect)

`wss://ws.api.paradigm.trade/v2/drfq/?api-key=<KEY>&cancel_on_disconnect=false`

JSON-RPC 2.0 subscribe message:

```json
{"jsonrpc":"2.0","id":1,"method":"subscribe","params":{"channel":"rfq"}}
```

Channels: `rfq` (incoming for makers), `order` (state changes —
DRFQv2's analogue of other venues' `quote`), `trade_confirmation`
(your fills), `bbo`, `mmp`. Set `cancel_on_disconnect=true` for
makers.

## Status codes

| Code | Meaning |
|---|---|
| 200 / 201 / 204 | Success |
| 207 | Multi-status (batch delete partial) — not a failure |
| 400 | Bad payload — surface response body |
| 401 | Auth failed — see `auth.md` |
| 403 | Forbidden (counterparty / RFQ not visible) |
| 404 | Wrong env (prod vs test) or expired id |
| 429 | Rate-limited — back off |
| 5xx | Paradigm-side — retry once with backoff |

## Pagination

Cursor-based: `{count, next, results[]}`. Follow `next` until `null`.
`page_size` controls page length where supported.
