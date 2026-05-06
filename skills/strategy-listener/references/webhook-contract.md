# OpenCLAW Webhook Contract

Mirrors the public spec at <https://docs.openclaw.ai/automation/webhook>.
Refresh this file if the upstream changes.

## Endpoints

OpenCLAW Gateway exposes three webhook routes under the gateway base URL:

| Path             | Purpose                                                       | Best for                                     |
| ---------------- | ------------------------------------------------------------- | -------------------------------------------- |
| `/hooks/wake`    | Enqueue a system event for the **main** session               | Background nudges, cron-style reminders      |
| `/hooks/agent`   | Run an **isolated** agent turn                                | Strategy fires (recommended default)         |
| `/hooks/<name>`  | Custom hook resolved via the gateway's `hooks.mappings` config | Server-side payload transformation           |

The listener doesn't care which route is used — the strategy `webhook.url`
is a full URL, so any of the three works.

## HTTP method

`POST` only. `GET` / `PUT` / `DELETE` are rejected.

## Authentication

Shared bearer token. Either header is accepted:

- `Authorization: Bearer <token>`  ← preferred
- `x-openclaw-token: <token>`

Query-string tokens are rejected.

The listener reads the token from the env var named in `webhook.tokenEnv`
(default `OPENCLAW_TOKEN`). One token per gateway; if you target multiple
gateways, point each evaluator at a different env var.

## Request body

### `/hooks/wake`

```jsonc
{
  "text": "<required>",            // the message body
  "mode": "now"                    // "now" | "next-heartbeat"
}
```

### `/hooks/agent`

```jsonc
{
  "message":         "<required>",  // the prompt for the isolated agent
  "agentId":         "...",         // optional: pin a specific agent
  "name":            "...",         // optional: human label
  "wakeMode":        "...",         // optional
  "deliver":         true,          // optional
  "channel":         "...",         // optional
  "to":              "...",         // optional
  "model":           "...",         // optional: override agent model
  "fallbacks":       [...],         // optional
  "thinking":        "...",         // optional
  "timeoutSeconds":  30             // optional, default = gateway config
}
```

Only `message` is required. All other fields are pass-through.

### Listener additions

The listener always merges these fields into the body:

```jsonc
{
  "correlationId":  "<strategy>/<evaluator>/<event_ts_ms>",
  "firedAt":        "2026-05-05T12:34:56.789Z",
  "source":         "paradex-strategy-listener"
}
```

`correlationId` is stable across retries — the receiving agent can use it
to dedupe.

## Response

Any 2xx is treated as success. Body shape is **not specified** by the
upstream contract; the listener does not parse it (logs the first 200 bytes
on debug only).

Non-2xx responses count as failures and trigger retry.

## Idempotency

OpenCLAW does **not** dedupe server-side. The listener handles
at-least-once delivery client-side:

1. Each fire gets a stable `correlationId` (deterministic from
   `<strategy>/<evaluator>/<event_ts_ms>`).
2. Retries (after 5xx / network errors) reuse the same body, including
   the same `correlationId`.
3. The receiving OpenCLAW agent can keep a small dedupe table keyed on
   `correlationId` if duplicate-fire matters.

## Rate limits

Not documented upstream. The listener enforces:

- Per-evaluator `throttle` (min gap between evaluations)
- Per-evaluator `cooldownAfterFire` (min gap between fires after ack)
- Global outbound queue with concurrent POSTs capped at 4

Tune `throttle` / `cooldownAfterFire` to match the receiving agent's
budget.

## Retry policy

Exponential backoff, max 3 attempts:

| Attempt | Delay before |
| ------- | ------------ |
| 1       | 0s           |
| 2       | 1s           |
| 3       | 4s           |
| (give up) | log `event=webhook_failed` |

`4xx` responses (other than 429) are **not** retried — they indicate a
malformed request and re-sending won't help.

## Smoke check

```bash
curl -sS -X POST https://<gateway>/hooks/agent \
    -H "Authorization: Bearer $OPENCLAW_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"message": "smoke test from strategy-listener", "correlationId": "smoke/1/0"}'
```

Expected: 2xx with a small JSON ack. Anything else → check the gateway
logs and the token.
