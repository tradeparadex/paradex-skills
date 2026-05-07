---
name: paradex-strategy-listener
description: >
  Real-time strategy evaluator that subscribes to Paradex WebSocket feeds (or
  HTTP-polls when WS is unavailable), evaluates one or more strategy JSON
  specs against each market and user event, and POSTs to an OpenCLAW Gateway
  webhook (`/hooks/agent` or `/hooks/wake`) when triggers fire. Use whenever
  the user says "alert me when", "watch the market for", "trigger a webhook",
  "fire when RSI crosses", "monitor my fills", "live signal", "real-time
  evaluator", "subscribe to BBO", "strategy listener", "OpenCLAW webhook",
  "mirror fills to a hook", or asks to deploy a backtested strategy as a live
  alert. Reuses the same gate schema as paradex-strategy-backtester (rsi, sma,
  rvPctile, ivPctile, fundingRate with all|any|min gates), so a strategy
  validated in backtest can be promoted to a live listener with minimal edits.
  Pairs with paradex-strategy-builder (NL → spec) and paradex-strategy-backtester
  (historical validation) — listener is the live deployment leg.
compatibility: Requires Paradex MCP server (mcp-paradex-py) for live data + paradex-py SDK for the WS client. Python script requires uv. Public market channels (bbo, trades, funding) need no auth. User channels (fills, orders, positions) require either PARADEX_JWT_TOKEN (preferred — works with API keys from the dashboard or MCP) OR PARADEX_ACCOUNT_PRIVATE_KEY + PARADEX_L1_ADDRESS. OpenCLAW token via OPENCLAW_TOKEN env var.
metadata:
  author: tradeparadex
  version: "1.2"
---

# Paradex Strategy Listener

Live-evaluator counterpart to `paradex-strategy-backtester`. Same strategy-spec
shape, but instead of replaying historical bars it subscribes to the Paradex
WebSocket feed (or polls REST endpoints), evaluates the gate on each event,
and POSTs to an OpenCLAW webhook when conditions fire.

```
┌──────────────────┐     ┌─────────────────┐     ┌──────────────┐
│  Paradex WS/REST │────▶│  Listener       │────▶│  OpenCLAW    │
│  (market + user) │     │  (evaluators)   │     │  /hooks/...  │
└──────────────────┘     └─────────────────┘     └──────────────┘
```

The listener **does not place orders.** It generates signals; the OpenCLAW
agent on the receiving end decides what to do with them.

## Quick start

```bash
# Public market data only — dry-run, no auth
PARADEX_ENVIRONMENT=testnet uv run scripts/paradex_listener.py \
    examples/btc-rsi-alert.json --dry-run

# Live, market + user channels — preferred: JWT from the dashboard or MCP
export PARADEX_ENVIRONMENT=testnet
export PARADEX_JWT_TOKEN=<paradex-api-key-jwt>
export OPENCLAW_TOKEN=<gateway-bearer-token>
uv run scripts/paradex_listener.py strategies/

# Alternative: raw L1 keys (SDK signs the auth tx itself)
export PARADEX_ACCOUNT_PRIVATE_KEY=0x...
export PARADEX_L1_ADDRESS=0x...
uv run scripts/paradex_listener.py strategies/

# Watch mode — drop new *.json files into strategies/ to submit them live
uv run scripts/paradex_listener.py strategies/ --watch

# Force HTTP polling (when WS is blocked or for low-frequency strategies)
uv run scripts/paradex_listener.py strategy.json --data-mode poll

# Auto: try WS, fall back to poll on persistent failure
uv run scripts/paradex_listener.py strategy.json --data-mode auto
```

For the complete field-level grammar, read
[references/grammar.md](references/grammar.md).
For the WS channel inventory and payload shapes, read
[references/channels.md](references/channels.md).
For the OpenCLAW webhook contract, read
[references/webhook-contract.md](references/webhook-contract.md).

## Your role

1. **Translate alert ideas into a listener strategy JSON** — same gate schema
   as the backtester, plus `subscriptions`, `evaluators[]`, and per-evaluator
   `webhook` configs.
2. **Promote a backtested strategy to a live listener** — take an existing
   backtester strategy, lift the entry/exit conditions into one or more live
   evaluators, point them at an OpenCLAW hook URL.
3. **Interpret listener logs** — fires, throttle skips, reconnects, webhook
   responses. Stdout is structured JSON, tailable.
4. **Run smoke tests** — `--dry-run` mode validates a strategy end-to-end
   without calling external webhooks.

## Strategy JSON shape

Minimal skeleton (indicator evaluator + fill-mirror):

```jsonc
{
  "name": "btc-rsi-alert",
  "underlying": "BTC",
  "barSize": "1m",
  "subscriptions": { "market": ["bbo.BTC-USD-PERP", "funding.BTC-USD-PERP"] },
  "evaluators": [{
    "id": "rsi-oversold",
    "on": ["bar_close.BTC-USD-PERP"],
    "throttle": "5m",
    "cooldownAfterFire": "15m",
    "conditions": { "gateMode": "all",
      "rsi": { "enabled": true, "op": "<", "value": 30 },
      "fundingRate": { "enabled": true, "op": ">", "value": 0.01 } },
    "webhook": { "url": "https://gw.openclaw.example/hooks/agent",
      "tokenEnv": "OPENCLAW_TOKEN",
      "messageTemplate": "BTC RSI={rsi:.1f} → consider long" }
  }]
}
```

Full field reference: [`references/grammar.md`](references/grammar.md).

### Two evaluator flavors

- **Indicator-driven (`conditions`)**: identical to the backtester `entry`
  block (rsi/sma/rvPctile/ivPctile/fundingRate, gateMode all/any/min).
  Re-evaluated on the events listed in `on`. Indicators run on rolling
  in-memory bar buffers seeded by a startup REST backfill.
- **Event-driven (`match`)**: raw event-shape match (e.g. fills with
  side=BUY, minSize=0.1). No bar history required — fires immediately on
  matching event.

A single evaluator may use **either** `conditions` or `match`, not both.

For `on` event tokens, see [`references/grammar.md`](references/grammar.md).

`throttle` (e.g. `"5m"`) is a minimum gap between **evaluations** for that
evaluator. `cooldownAfterFire` is a minimum gap between **fires** — only
applies after a successful webhook ack.

## Workflow A: Backtested strategy → live listener

User has a strategy that backtested well; promote it to a live alert.

1. Read the backtester JSON, extract `underlying` and the `entry` block.
2. Wrap into a listener evaluator:
   - `conditions` ← backtester `entry` (drop `frequency`, `entryDuration` —
     listener semantics differ; replace with `throttle`/`cooldownAfterFire`).
   - `on` ← `["bar_close.<UNDERLYING>-USD-PERP"]`.
   - `subscriptions.market` ← whatever the indicators need:
     - `rsi` / `sma` / `rvPctile` → `bbo.<MARKET>` or `trades.<MARKET>`
     - `fundingRate` → `funding.<MARKET>`
     - `ivPctile` → option-chain summary endpoint (HTTP poll fallback —
       no public WS for option marks).
3. Confirm the OpenCLAW URL with the user. Default to `/hooks/agent`.
4. Render the message template — include enough context for the agent on
   the other end to act (price, indicator values, suggested action).

## Workflow B: Fresh alert from natural language

User: "Tell me when BTC RSI drops under 30 and funding is positive."

1. Pick `underlying = BTC`, `barSize = 1m` (default), market subscriptions
   `[bbo.BTC-USD-PERP, funding.BTC-USD-PERP]`.
2. Build one evaluator with `gateMode: "all"`, `rsi {op: "<", value: 30}`,
   `fundingRate {op: ">", value: 0.01}`.
3. Ask for the OpenCLAW hook URL. Set `tokenEnv: "OPENCLAW_TOKEN"`.
4. Suggest `throttle: "5m"`, `cooldownAfterFire: "15m"` to avoid spam.

## Workflow E: Agent-generated conditions via the expression DSL

For arbitrary boolean logic that doesn't fit the legacy `conditions` block
(comparing two indicators, deeper AND/OR/NOT trees, multi-period SMA, etc.),
use the `expression` field. It's a small JSON expression tree that:

- An LLM can generate reliably (strict named-key shape, no positional args).
- The listener validates **before** accepting — `--check` exits non-zero
  with a list of issues so the agent can self-correct.
- Falls back to None ("missing-data") instead of raising at runtime.

### Generation → check → submit loop (agent-facing)

```bash
# 1. Discover the supported indicators / fields / operators
uv run scripts/paradex_listener.py --catalog

# 2. Agent writes a strategy JSON using the catalog. Example:
#    expression: {"all": [{"op": "<", "lhs": {"indicator": "rsi"}, "rhs": {"const": 30}}]}

# 3. Validate before sending — exits 0 on success, 1 on errors
uv run scripts/paradex_listener.py strategies/draft.json --check

# 4. On success, drop into the watched directory (or run directly)
mv strategies/draft.json strategies/btc-rsi-30.json
```

`--check` performs:
- Structural validation of the strategy file (same as load-time)
- DSL validation per evaluator (every node, type-checked, args-checked)
- A smoke evaluation of each expression against synthetic state — proves
  it doesn't raise even on edge inputs

If the agent submits invalid JSON to a `--watch` directory, the listener
logs `event=watch_load_error` and the file stays quarantined until it's
fixed. Always run `--check` first.

Full DSL node types, operators, and legacy ↔ expression equivalences:
[`references/grammar.md`](references/grammar.md).

See [examples/btc-expression-dsl.json](examples/btc-expression-dsl.json)
for a multi-evaluator file mixing AND, OR, NOT, and indicator-vs-event
comparisons.

## Workflow D: Submit a strategy to a running listener

With `--watch strategies/`: write a `.json` file into the directory — the
listener reloads within ~2s, subscribes to any new channels, and starts
evaluating. Remove a file to unload its evaluators. Modify a file to reload
it (throttle/cooldown counters for unchanged evaluator IDs are preserved).

## Workflow C: Fill mirror

User: "POST every BTC perp buy over 0.1 BTC to my OpenCLAW hook."

1. `subscriptions.user = ["fills"]` (requires auth env vars).
2. Single `match`-style evaluator with `market`, `side: "BUY"`,
   `minSize: 0.1`.
3. `messageTemplate` includes `{side} {size} {market} @ {price}`.

## Tests + benchmark

```bash
# Pytest suite — covers RSI/SMA/funding/whale-fill/multi-strategy patterns
uv run -m pytest skills/strategy-listener/tests -v

# Throughput + latency benchmark (10 strategies × 100k events by default)
uv run skills/strategy-listener/tests/benchmark.py
uv run skills/strategy-listener/tests/benchmark.py --strategies 50 --events 500000
```

The benchmark reports events/sec and p50/p95/p99 dispatch latency. Use it
to validate listener capacity before deploying many strategies on one
process.

## Smoke test (always before deploying)

```bash
PARADEX_ENVIRONMENT=testnet uv run scripts/paradex_listener.py \
    examples/btc-rsi-alert.json --dry-run
```

Expect to see, in stdout JSON:
- `event=subscribed channel=bbo.BTC-USD-PERP`
- `event=backfilled bars=60 market=BTC-USD-PERP`
- `event=tick` lines as data flows
- `event=fire evaluator=rsi-oversold` followed by
  `event=webhook_dryrun url=https://...` when conditions match

If the strategy never fires, lower the threshold in the conditions to test
the dispatch path; reset before deploying.

## Operational notes

- **Reconnect**: WS disconnects trigger exponential-backoff reconnect (1s →
  4s → 16s → 60s cap). On reconnect, indicator buffers are re-backfilled
  for any gap > 1 bar.
- **Multi-strategy**: pass a directory; the runner unions all channels and
  shares one WS connection. Each evaluator is namespaced
  `<strategy.name>/<evaluator.id>` in logs.
- **Auth**: only required when any `subscriptions.user` channel is
  declared. Public market data is unauthenticated. Two paths for user
  channels: (a) `PARADEX_JWT_TOKEN` — pre-issued JWT from the dashboard
  API-keys page or `mcp-paradex-py` (preferred — no raw key on disk);
  (b) `PARADEX_ACCOUNT_PRIVATE_KEY` + `PARADEX_L1_ADDRESS` — SDK signs
  the auth tx itself. JWT wins when both are set.
- **Idempotency**: OpenCLAW does not dedupe server-side; the listener
  embeds a correlation id `<strategy>/<evaluator>/<event-ts>` in the
  message body and retries with the same id, so the receiving agent can
  dedupe if it wants to.
- **Failure mode**: webhook 4xx/5xx after 3 retries → log
  `event=webhook_failed` and continue. Listener never crashes on a webhook
  failure.

## Integration with sibling skills

- `paradex-strategy-builder` produces a strategy spec → feed entry block
  into a listener evaluator.
- `paradex-strategy-backtester` validates historical performance → confirm
  fire frequency before going live.
- `paradex-risk-guardian` can be the consumer on the OpenCLAW side
  (receive fire → re-check margin → decide whether to act).
