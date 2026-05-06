# Strategy Listener Grammar

Field-level reference for live-listener strategy JSON. Indicator-condition
fields (`rsi`, `sma`, `rvPctile`, `ivPctile`, `fundingRate`, `gateMode`)
are **identical** to those in
[`strategy-backtester/references/grammar.md`](../../strategy-backtester/references/grammar.md);
this file documents the listener-only additions.

## Top-level fields

| Field             | Type     | Required | Default | Notes                                                          |
| ----------------- | -------- | -------- | ------- | -------------------------------------------------------------- |
| `name`            | string   | yes      | —       | Used in logs and webhook correlation ids                       |
| `underlying`      | string   | yes      | —       | `BTC` \| `ETH` \| `SOL`                                        |
| `barSize`         | string   | no       | `1m`    | `1m` \| `5m` \| `15m` \| `1h`                                  |
| `dataMode`        | string   | no       | `ws`    | `ws` \| `poll` \| `auto`                                       |
| `pollIntervalSec` | int      | no       | `15`    | Used only when `dataMode=poll` (or `auto` after WS gives up)   |
| `subscriptions`   | object   | yes      | —       | See below                                                      |
| `evaluators`      | array    | yes      | —       | Min 1                                                          |

## `subscriptions`

```jsonc
{
  "market": ["bbo.BTC-USD-PERP", "trades.BTC-USD-PERP", "funding.BTC-USD-PERP"],
  "user":   ["fills", "orders", "positions"]
}
```

Public channels — no auth.
User channels (`fills`, `orders`, `positions`, `account`) require
`PARADEX_ACCOUNT_PRIVATE_KEY` and `PARADEX_L1_ADDRESS`.

For full channel inventory and payload shapes see
[channels.md](channels.md).

## `evaluators[]`

| Field               | Type   | Required        | Notes                                                      |
| ------------------- | ------ | --------------- | ---------------------------------------------------------- |
| `id`                | string | yes             | Unique within the strategy                                 |
| `on`                | array  | yes             | Event tokens that re-evaluate this rule                    |
| `throttle`          | string | no (default 0)  | Min gap between **evaluations**. `30s`, `5m`, `1h`         |
| `cooldownAfterFire` | string | no (default 0)  | Min gap between **fires** (after webhook ack)              |
| `conditions`        | object | one of these    | Indicator gate (same schema as backtester `entry`)         |
| `expression`        | object | one of these    | JSON expression tree DSL (agent-friendly)                  |
| `match`             | object | one of these    | Raw event-shape match                                      |
| `webhook`           | object | yes             | OpenCLAW webhook config                                    |

Exactly one of `conditions`, `expression`, or `match` must be present.

### `on` event tokens

| Token                          | Fires when                                                   |
| ------------------------------ | ------------------------------------------------------------ |
| `bar_close.<MARKET>`           | A `barSize`-aligned bar closes (drives indicators)           |
| `bbo.<MARKET>`                 | BBO update arrives                                           |
| `trades.<MARKET>`              | Trade tick                                                   |
| `funding.<MARKET>`             | Funding-rate update                                          |
| `fills`                        | User fill                                                    |
| `orders`                       | User order state change                                      |
| `positions`                    | User position update                                         |

Each token must correspond to a channel listed in `subscriptions`.

### `conditions` (indicator-driven)

Same schema as the backtester `entry` block. Listener-relevant fields:

```jsonc
{
  "gateMode": "all",                                     // "all" | "any" | "min"
  "gateMin": 2,                                          // when gateMode = "min"
  "rsi":         { "enabled": true, "op": "<",     "value": 30 },
  "sma":         { "enabled": true, "op": "above", "period": 24 },
  "rvPctile":    { "enabled": true, "op": ">",     "value": 80, "window": 168 },
  "ivPctile":    { "enabled": true, "op": ">",     "value": 60, "window": 720 },
  "fundingRate": { "enabled": true, "op": ">",     "value": 0.01 }
}
```

`window` is in **bars** of `barSize` (e.g. `window: 168` at `barSize: "1h"`
= 168 hours = 7 days). The listener backfills `max(window)` bars per
indicator on startup via REST.

### `expression` (DSL-driven)

JSON expression tree. Use this when an agent generates conditions, when you
want to compare two indicators, or when the legacy gate-modes (all/any/min)
aren't expressive enough.

```jsonc
"expression": {
  "all": [
    { "op": "<",     "lhs": { "indicator": "rsi" },        "rhs": { "const": 30 } },
    { "op": "above", "lhs": { "event": "close" },          "rhs": { "indicator": "sma", "period": 24 } },
    { "not": { "op": ">", "lhs": { "indicator": "fundingPct" }, "rhs": { "const": 5 } } }
  ]
}
```

| Node shape                              | Meaning                              |
| --------------------------------------- | ------------------------------------ |
| `{"const": NUMBER}`                     | numeric literal                      |
| `{"event": FIELD}`                      | reads an event field (`close`, `bid`, `ask`, `mid`, `price`, `size`, `volume`, `open`, `high`, `low`) |
| `{"indicator": NAME, ...args}`          | reads a registered indicator         |
| `{"op": OP, "lhs": NUM, "rhs": NUM}`    | comparison; `OP` ∈ `<` `>` `<=` `>=` `==` `!=` `above` `below` |
| `{"all": [BOOL...]}`                    | AND, short-circuits on False         |
| `{"any": [BOOL...]}`                    | OR, short-circuits on True           |
| `{"not": BOOL}`                         | negation                             |

Available indicators (run `uv run scripts/paradex_listener.py --catalog`
for the live list, including arg defaults):

| `indicator`     | Required args | Default args      | Description                                  |
| --------------- | ------------- | ----------------- | -------------------------------------------- |
| `rsi`           |               | `period: 14`      | Wilder's RSI 0..100                          |
| `sma`           | `period`      |                   | Simple moving average over `period` bars     |
| `rvPctile`      |               | `window: 168`     | Realized vol percentile 0..100               |
| `fundingRate`   |               |                   | 8h funding as decimal (0.01 = 1%)            |
| `fundingPct`    |               |                   | 8h funding as percent (1.0 = 1%)             |
| `ivPctile`      |               | `window: 720`     | ATM IV percentile (event-supplied)           |

Validation runs at strategy load time AND when calling `--check`. Errors
reference exact JSON paths (e.g. `$.all[1].rhs.indicator`) so an agent can
fix one node at a time.

### `match` (event-driven)

Match raw event fields. Currently supported for `fills` / `orders` /
`positions` events:

```jsonc
{
  "market":   "BTC-USD-PERP",
  "side":     "BUY",
  "minSize":  0.1,
  "minNotionalUsd": 10000
}
```

All listed fields must match (AND). Omit a field to ignore it.

### `webhook`

```jsonc
{
  "url":              "https://gw.openclaw.example/hooks/agent",
  "tokenEnv":         "OPENCLAW_TOKEN",
  "messageTemplate":  "BTC RSI={rsi:.1f} → consider long {size_pct}%",
  "extra":            { "agentId": "dave", "timeoutSeconds": 30 }
}
```

| Field             | Required | Notes                                                                            |
| ----------------- | -------- | -------------------------------------------------------------------------------- |
| `url`             | yes      | Full OpenCLAW hook URL                                                           |
| `tokenEnv`        | no       | Env var name holding the bearer token (default `OPENCLAW_TOKEN`)                 |
| `messageTemplate` | yes      | Python-format template; receives indicator + event values as kwargs              |
| `extra`           | no       | Extra fields merged into the POST body (e.g. `agentId`, `timeoutSeconds`)        |

The listener always adds a `correlationId` field of the form
`<strategy.name>/<evaluator.id>/<event_ts_ms>` to the body for client-side
dedupe across retries.

#### Template variables available

| Source           | Variables                                                                         |
| ---------------- | --------------------------------------------------------------------------------- |
| Always           | `strategy`, `evaluator`, `ts`, `iso_ts`, `underlying`, `correlation_id`           |
| `bar_close.*`    | `open`, `high`, `low`, `close`, `volume`, `market`                                |
| `bbo.*`          | `bid`, `ask`, `mid`, `market`                                                     |
| `trades.*`       | `price`, `size`, `side`, `market`                                                 |
| `funding.*`      | `funding`, `funding_8h_pct`, `market`                                             |
| Indicator gates  | `rsi`, `sma`, `rv_pctile`, `iv_pctile`                                            |
| `fills`          | `market`, `side`, `size`, `price`, `notional`, `order_id`, `fill_id`              |
| `orders`         | `market`, `side`, `size`, `price`, `status`, `order_id`                           |
| `positions`      | `market`, `side`, `size`, `entry_price`, `unrealized_pnl`                         |

Missing values render as `None`.

## Validation

The runner validates strategies at load time and exits with a clear error
on:

- Unknown channel in `subscriptions.market` (see [channels.md](channels.md))
- Evaluator `on` token referencing a channel not in `subscriptions`
- Both `conditions` and `match` set on one evaluator
- `messageTemplate` referencing a variable not available for the
  declared `on` events
- User channel declared but auth env vars missing

Validation is strict — fail fast before subscribing.
