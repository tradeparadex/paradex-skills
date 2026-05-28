# `mcp-paradigm-py` — design draft (superseded)

> **Status: shipped.** The MCP server now lives at
> [`tradeparadigm/mcp-paradigm-py`](https://github.com/tradeparadigm/mcp-paradigm-py)
> with its own `DESIGN.md`. This file is the original design draft
> kept for historical context. **For the current tool surface, signer
> status, and roadmap, read the upstream `DESIGN.md`** — not this one.
>
> Tool naming differs from this draft in one important way: the live
> server exposes DRFQv2 tools as `paradigm_drfqv2_*` (e.g.
> `paradigm_drfqv2_create_rfq`), not the unprefixed `paradigm_*` shown
> below. The prefix disambiguates DRFQv2 from OBv1 / FSPD / firm-wide
> tools. The skill body in `../SKILL.md` already uses the prefixed
> names.

---

The MCP server wraps a codegen'd Python SDK (`paradigm-py`) and exposes a
typed tool surface to Claude Code / Claude Desktop / any MCP-aware
client. The skill `paradigm-rfq-trader` becomes a thin workflow document
once these tools exist.

---

## 1. Goals and non-goals

**Goals:**

1. Expose the full DRFQv2 REST surface as MCP tools, generated from
   `tradeparadigm/mono#34164`.
2. Keep the HMAC signing key out of the agent process (pluggable signing
   layer: env-var for dev, Vault Transit / AWS KMS for prod).
3. OAuth 2.1-protected per the MCP spec (2025-03-26 revision), with
   scopes that map cleanly onto tool sensitivity.
4. Surface async-first order state semantics correctly (response is
   `PENDING`; client polls or subscribes for terminal state).
5. Expose WebSocket streams via a snapshot + tail interface so MCP
   clients can consume push data without bespoke transport.

**Non-goals:**

1. Not a general Paradigm dev tool — only DRFQv2. VRFQ, FSPD, GRFQ are
   separate concerns.
2. Not a strategy engine — it executes what the caller decides. Pricing
   logic, edge application, etc. live in the calling skill.
3. Not a credential vault — it consumes a signing capability, doesn't
   manage it. Vault Transit / KMS / the credential registry are separate
   services.
4. Not a UI — agents call it; humans use the registry page to manage
   keys.

---

## 2. Architecture

```
   Agent (Claude Code / Desktop)
        │
        │   OAuth 2.1 bearer (Dex / Paradex IdP issuer)
        ▼
   mcp-paradigm-py  (FastMCP server)
        │
        ├──►  paradigm-py  (codegen SDK)
        │         │
        │         │ httpx auth hook
        │         ▼
        │     Signing layer (pluggable)
        │         │  ┌──────────────────────────┐
        │         ├──┤ EnvKeySigner            │  dev / local
        │         ├──┤ VaultTransitSigner      │  prod (recommended)
        │         ├──┤ KMSGenerateMacSigner    │  prod (AWS)
        │         └──┤ SidecarHttpSigner       │  custom
        │            └──────────────────────────┘
        │
        └──►  Paradigm REST (api.paradigm.co or api.test.paradigm.co)

   WebSocket subscriptions handled by the MCP server itself: it holds a
   single WS connection per session and exposes snapshot/tail tools.
```

---

## 3. Tool surface

Tools are grouped by responsibility. Every tool returns a JSON object
that maps 1:1 to the OpenAPI response schema, with `instrument_id` /
`rfq_id` / `order_id` / `trade_id` exposed at the top level for easy
chaining.

Conventions:

- `paradigm_*` prefix.
- Snake_case.
- Singular = "get one"; plural = "list".
- All list tools accept `cursor` and `page_size` for pagination.
- Every state-changing tool returns the created/updated entity plus a
  `label` echo for idempotency tracking.

### 3.1 Reference data (read-only, low sensitivity)

| Tool | REST | Purpose |
|---|---|---|
| `paradigm_instruments` | `GET /v2/drfq/instruments/` | List tradable instruments. Filters: `venue`, `base_currency`, `kind`, `margin_kind`, `state`, `venue_instrument_name`, `include_greeks`, `cursor`, `page_size` |
| `paradigm_instrument` | `GET /v2/drfq/instruments/{id}/` | Fetch one instrument by Paradigm id |
| `paradigm_counterparties` | `GET /v2/drfq/counterparties/` | List desks your firm can RFQ — returns `desk_name`, `firm_name`, `groups`, `venues` |
| `paradigm_platform_state` | `GET /v2/drfq/platform_state/` | Current and next platform state (maintenance windows) |

**Scope:** `paradigm:read`. No approval prompt.

### 3.2 RFQ lifecycle (taker)

| Tool | REST | Purpose | Scope | Approval |
|---|---|---|---|---|
| `paradigm_rfqs` | `GET /v2/drfq/rfqs/` | List RFQs. Filters: `state`, `role`, `venue`, `strategies`, `product_codes`, `cursor`, `page_size` | `paradigm:read` | no |
| `paradigm_rfq` | `GET /v2/drfq/rfqs/{id}/` | Fetch one RFQ | `paradigm:read` | no |
| `paradigm_rfq_bbo` | `GET /v2/drfq/rfqs/{id}/bbo/` | Best bid/offer for an RFQ — structure `mark_price`, `min_price`, `max_price`, `greeks`, per-leg bbo | `paradigm:read` | no |
| `paradigm_rfq_orders` | `GET /v2/drfq/rfqs/{id}/orders/` | Order book against an RFQ — `asks[]` / `bids[]` with price, quantity, desk | `paradigm:read` | no |
| `paradigm_create_rfq` | `POST /v2/drfq/rfqs/` | Create a new RFQ. Body: `venue`, `legs[]`, `quantity`, `counterparties[]`, `is_taker_anonymous`, `account_name`, `label`, `state` | `paradigm:create_rfq` | **yes** |
| `paradigm_cancel_rfq` | `DELETE /v2/drfq/rfqs/{id}/` | Cancel an open RFQ before expiry | `paradigm:cancel` | no (cancels are safe) |

### 3.3 Order lifecycle (covers maker quote AND taker execute)

| Tool | REST | Purpose | Scope | Approval |
|---|---|---|---|---|
| `paradigm_orders` | `GET /v2/drfq/orders/` | List your desk's orders. Filters: `rfq_id`, `state`, `venue`, `currency`, `base_currency`, `cursor`, `page_size` | `paradigm:read` | no |
| `paradigm_order` | `GET /v2/drfq/orders/{id}/` | Fetch one order | `paradigm:read` | no |
| `paradigm_post_order` | `POST /v2/drfq/orders/` | Submit an order against an RFQ. Body: `rfq_id`, `side`, `type`, `time_in_force`, `price`, `quantity`, `legs[]`, `account_name`, `label`. Maker quoting = GTC; taker crossing = FOK | `paradigm:post_order` | **yes** |
| `paradigm_update_order` | `PUT /v2/drfq/orders/{id}/` | Amend an order (price / quantity) | `paradigm:post_order` | **yes** |
| `paradigm_cancel_order` | `DELETE /v2/drfq/orders/{id}/` | Cancel one order | `paradigm:cancel` | no |
| `paradigm_cancel_orders_batch` | `DELETE /v2/drfq/orders/` | Batch-cancel by filter. Returns `successes` / `failures` (may be 207). Surface partials to caller | `paradigm:cancel` | no |

### 3.4 Trades

| Tool | REST | Purpose | Scope |
|---|---|---|---|
| `paradigm_trades` | `GET /v2/drfq/trades/` | Your desk's cleared block trades. Filters: `state`, `venue`, `product_codes`, `cursor`, `page_size` | `paradigm:read` |
| `paradigm_trade` | `GET /v2/drfq/trades/{id}/` | One trade | `paradigm:read` |
| `paradigm_trade_tape` | `GET /v2/drfq/trade_tape/` | Public anonymized trade tape across the network | `paradigm:read` |

### 3.5 Pricing and maker safety

| Tool | REST | Purpose | Scope | Approval |
|---|---|---|---|---|
| `paradigm_price_legs` | `POST /v2/drfq/pricing/` | Given `bid_price` / `ask_price` and `legs[]`, returns per-leg prices. Useful for multi-leg structure pricing | `paradigm:read` | no |
| `paradigm_mmp_status` | `GET /v2/drfq/mmp/status/` | Current `rate_limit_hit` flag | `paradigm:read` | no |
| `paradigm_mmp_reset` | `PATCH /v2/drfq/mmp/status/` | Reset MMP flag to re-arm the desk | `paradigm:mmp` | **yes** |

### 3.6 Self-test

| Tool | REST | Purpose | Scope |
|---|---|---|---|
| `paradigm_echo` | `GET` / `POST /v2/drfq/echo/` | Round-trips a payload. First call to make after wiring; 200 means signing + auth + OneCLI are all correct | `paradigm:read` |

### 3.7 WebSocket subscriptions

WS events don't map naturally to request/response tools. The server
holds one WS connection per session and exposes a buffered snapshot/tail
pattern:

| Tool | Behavior | Scope |
|---|---|---|
| `paradigm_subscribe(channel: str)` | Open a subscription. Returns `subscription_id`. Channels: `rfq`, `order`, `trade`, `trade_confirmation`, `bbo`, `mmp` | per-channel scope (see below) |
| `paradigm_poll(subscription_id, since: str?, limit: int = 100)` | Drain buffered events since the last cursor. Returns `events[]` and the next `since` cursor | as above |
| `paradigm_unsubscribe(subscription_id)` | Close a subscription | as above |

Channel-to-scope mapping mirrors the REST tools:

| Channel | Required scope |
|---|---|
| `rfq` | `paradigm:read` (or `paradigm:post_order` if the caller intends to quote) |
| `order` | `paradigm:read` |
| `trade` | `paradigm:read` |
| `trade_confirmation` | `paradigm:read` |
| `bbo` | `paradigm:read` |
| `mmp` | `paradigm:read` |

The server is responsible for `cancel_on_disconnect` handling — caller
specifies their intent at `paradigm_subscribe` time; server passes it
through on the WS URL.

---

## 4. Auth model

### 4.1 Inbound (MCP client → MCP server)

OAuth 2.1 resource-server per the MCP spec 2025-03-26 revision.

- Server publishes `WWW-Authenticate: Bearer
  resource_metadata=https://mcp-paradigm.example.com/.well-known/oauth-protected-resource`
  on 401.
- Resource-metadata document points at the trusted authorization server
  (Dex / Auth0 / Keycloak / Paradex IdP — config-driven).
- Server validates incoming JWTs against the auth server's JWKS.
- Required claims: `iss`, `aud=mcp-paradigm`, `exp`, `sub`, `scope`.
- Optional claims used for policy: `acr`, `amr` (step-up signals).

### 4.2 Outbound (MCP server → Paradigm REST)

Bearer access key + HMAC signature triple. Computed by the signing layer
at the moment of each request — the MCP server itself doesn't see the
signing key when running with Vault / KMS.

### 4.3 Scopes

Map OAuth scopes 1:1 to tool sensitivity. Tools reject calls whose
bearer is missing the required scope, *before* the request reaches the
signing layer:

| Scope | Tools |
|---|---|
| `paradigm:read` | All GET-shaped tools and pricing |
| `paradigm:create_rfq` | `paradigm_create_rfq` |
| `paradigm:post_order` | `paradigm_post_order`, `paradigm_update_order` |
| `paradigm:cancel` | `paradigm_cancel_rfq`, `paradigm_cancel_order`, `paradigm_cancel_orders_batch` |
| `paradigm:mmp` | `paradigm_mmp_reset` |
| `paradigm:subscribe` | `paradigm_subscribe`, `paradigm_poll`, `paradigm_unsubscribe` |

Step-up auth (e.g. wallet signature) can be required for
`paradigm:post_order` and `paradigm:create_rfq` by configuring the auth
server to demand it before granting those scopes.

---

## 5. Signing layer interface

The MCP server depends on this Python protocol; any implementation that
satisfies it is pluggable:

```python
from typing import Protocol

class Signer(Protocol):
    def sign(self, method: str, path: str, body_bytes: bytes) -> tuple[str, str]:
        """Return (timestamp_ms_str, base64_signature). 
        Implementations MUST sign exactly `body_bytes` and produce a
        timestamp within 30s of server time."""
```

### 5.1 Reference implementations

**`EnvKeySigner`** — reads `PARADIGM_SIGNING_KEY` from env, computes
HMAC in-process. Dev only. Vendors `references/test-signing.py` as its
unit test.

**`VaultTransitSigner`** — recommended for prod. Sends `{method, path,
body_bytes}` to Vault Transit's `hmac/{key_name}/sha256`. Vault returns
the HMAC; signing key never leaves Vault. Authenticated with a
short-TTL Vault token issued by `auth/jwt/login` (the MCP server passes
the user's OAuth bearer through, getting a per-user, per-session Vault
token scoped to that user's `transit/hmac/paradigm-rfq-{sub}` path).

**`KMSGenerateMacSigner`** — AWS KMS HMAC key with `KeyUsage:
GENERATE_VERIFY_MAC`, `KeySpec: HMAC_256`. Calls `kms:GenerateMac`. Key
never leaves KMS. IAM-gated, CloudTrail-audited.

**`SidecarHttpSigner`** — POSTs `{method, path, body_b64}` to a local
HTTP signing service (the "intermediate MCP" pattern from earlier
discussion, or a custom sidecar). Useful for vendor-specific KMSes or
for an HSM-backed signer.

### 5.2 Selection

```yaml
# config.yaml
signing:
  driver: vault_transit   # env_key | vault_transit | aws_kms | sidecar
  vault_transit:
    addr: https://vault.internal:8200
    key_name_template: "paradigm-rfq-{sub}"   # templated by JWT sub
    auth_mount: "auth/jwt"
    role: "paradigm-mcp"
```

---

## 6. SDK layout — `paradigm-py`

```
paradigm-py/
├── pyproject.toml
├── README.md
├── paradigm_py/
│   ├── __init__.py
│   ├── client.py             # ParadigmClient — top-level, holds Signer + httpx.AsyncClient
│   ├── auth.py               # AuthHook (httpx auth) wrapping a Signer
│   ├── signers/
│   │   ├── __init__.py
│   │   ├── base.py           # Signer protocol
│   │   ├── env_key.py
│   │   ├── vault_transit.py
│   │   ├── aws_kms.py
│   │   └── sidecar_http.py
│   ├── generated/            # `openapi-python-client` output, NEVER hand-edited
│   │   ├── models/           # Pydantic v2 models from spec
│   │   ├── api/              # One module per endpoint
│   │   └── client.py
│   ├── ws/
│   │   ├── __init__.py
│   │   ├── client.py         # JSON-RPC 2.0 over WS
│   │   └── channels.py
│   └── errors.py
├── tests/
│   ├── test_signing.py       # copied from references/test-signing.py
│   ├── test_signers/
│   ├── test_auth_hook.py
│   └── test_ws.py
├── scripts/
│   └── regen.sh              # pulls spec from pinned commit, runs openapi-python-client
└── spec/
    └── paradigm-openapi.yaml # pinned copy at a known commit of tradeparadigm/mono
```

### 6.1 Codegen flow

```bash
# scripts/regen.sh
PINNED_COMMIT="<commit-hash-from-tradeparadigm/mono>"
curl -sSL "https://raw.githubusercontent.com/tradeparadigm/mono/${PINNED_COMMIT}/path/to/openapi.yaml" \
  -o spec/paradigm-openapi.yaml

openapi-python-client generate \
  --path spec/paradigm-openapi.yaml \
  --output-path paradigm_py/generated \
  --overwrite \
  --config codegen.yaml
```

`codegen.yaml` sets: Pydantic v2 (`use_pydantic_v2: true`), httpx async
+ sync clients, decimals as `Decimal` (not float), no enum suffixes
(strip `Side41eEnum` → `Side`).

### 6.2 Hand-written pieces

- `auth.py` — httpx auth hook that:
  1. captures the outgoing request method, path, and body bytes
  2. calls `signer.sign(...)` to get `(ts, sig)`
  3. attaches `Authorization`, `Paradigm-API-Timestamp`,
     `Paradigm-API-Signature` headers
  4. yields the request
- `signers/*` — the implementations above
- `ws/*` — JSON-RPC 2.0 WS client (AsyncAPI is not in the spec; this is
  hand-written and probably ~200 lines)
- `errors.py` — typed exceptions per HTTP code + a generic
  `ParadigmAPIError` for unexpected payloads

---

## 7. MCP server layout — `mcp-paradigm-py`

```
mcp-paradigm-py/
├── pyproject.toml
├── README.md
├── DESIGN.md                       # this file
├── mcp_paradigm/
│   ├── __init__.py
│   ├── server.py                   # FastMCP entrypoint
│   ├── auth.py                     # OAuth resource-server bits
│   ├── config.py                   # pydantic-settings config loader
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── reference_data.py       # instruments, counterparties, platform_state
│   │   ├── rfqs.py                 # rfq lifecycle
│   │   ├── orders.py               # order lifecycle (quote + cross)
│   │   ├── trades.py               # trades + tape
│   │   ├── pricing.py              # pricing endpoint
│   │   ├── mmp.py                  # mmp status + reset
│   │   ├── echo.py                 # signing self-test
│   │   └── subscriptions.py        # WS subscribe / poll / unsubscribe
│   ├── ws_manager.py               # holds the per-session WS connection + event buffer
│   └── policy.py                   # scope checks, step-up enforcement
├── tests/
│   ├── conftest.py                 # mock paradigm + mock signer
│   ├── test_tools/
│   ├── test_auth.py
│   └── test_subscriptions.py
└── docker/
    └── Dockerfile                  # multistage; runs as non-root
```

### 7.1 Tool registration pattern

```python
# tools/orders.py
from fastmcp import FastMCP
from mcp_paradigm.policy import requires_scope, approval

def register(mcp: FastMCP, client: ParadigmClient) -> None:

    @mcp.tool()
    @requires_scope("paradigm:read")
    async def paradigm_orders(
        rfq_id: str | None = None,
        state: OrderState | None = None,
        venue: Venue | None = None,
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> PaginatedOrderList:
        return await client.orders.list(...)

    @mcp.tool(annotations={"destructive": True, "requires_approval": True})
    @requires_scope("paradigm:post_order")
    @approval(reason="Paradigm order placement — money on the wire")
    async def paradigm_post_order(
        rfq_id: str,
        side: Side,
        type: OrderType,
        time_in_force: OrderTIF,
        price: Decimal,
        quantity: Decimal,
        legs: list[OrderLeg],
        account_name: str | None = None,
        label: str | None = None,
    ) -> OrderCreateOutput:
        return await client.orders.create(...)
```

### 7.2 WS subscription manager

One `WSManager` per server process (or per OAuth session — depends on
auth model). Holds:

- the WS connection
- a per-channel ring buffer of recent events (e.g. last 1000 per
  channel, ~5MB total)
- a `subscription_id → channel + cursor` map
- a background asyncio task that drains the WS into the buffers

`paradigm_poll` walks the buffer from the caller's cursor forward,
returns up to `limit` events, advances the cursor. Buffer overflow
returns a `truncated: true` flag so the agent knows to re-subscribe or
fetch state via REST.

---

## 8. Configuration

Single YAML / env-vars hybrid via `pydantic-settings`:

```yaml
paradigm:
  base_url: https://api.paradigm.co        # or api.test.paradigm.co
  ws_url: wss://ws.api.paradigm.trade/v2/drfq/
  access_key: ${PARADIGM_ACCESS_KEY}       # OneCLI placeholder OK
  account: ${PARADIGM_ACCOUNT}             # optional, multi-desk

signing:
  driver: vault_transit
  vault_transit:
    addr: https://vault.internal:8200
    key_name_template: "paradigm-rfq-{sub}"
    auth:
      mount: auth/jwt
      role: paradigm-mcp
      forward_oauth_bearer: true

mcp:
  transport: stdio                          # stdio | sse | http
  oauth:
    issuer: https://id.paradex.io
    audience: mcp-paradigm
    jwks_uri: https://id.paradex.io/.well-known/jwks.json
  ws_buffer:
    max_events_per_channel: 1000
    ttl_seconds: 600

logging:
  level: info
  audit_destination: stdout                 # stdout | syslog | file
```

`HTTPS_PROXY` etc. picked up from env for OneCLI compatibility.

---

## 9. Testing

Three layers, all in CI:

1. **Signing unit tests** — `tests/test_signing.py` ports the pinned
   vectors from `paradigm-rfq-trader/references/test-signing.py`. Every
   `Signer` implementation must satisfy them (except KMS / Vault Transit,
   which are tested against testcontainers fixtures).
2. **Tool tests** — mock the underlying `ParadigmClient` with a stub
   that returns canned responses; assert each tool builds the right
   request and serializes the right response. No live network.
3. **Integration tests** (optional, opt-in via env flag) — point at
   `api.test.paradigm.co`, hit `paradigm_echo`, `paradigm_instruments`,
   then RFQ create + cancel against a known-safe synthetic instrument.
   Tagged `@pytest.mark.integration`; skipped by default.

Repo provides a `pytest --integration` flag and CI runs it nightly
against a testnet account.

---

## 10. Versioning and the spec pin

| Layer | Versioning |
|---|---|
| `paradigm-py` | Semver; `MAJOR` bumps when upstream spec breaks; `MINOR` for added endpoints; `PATCH` for codegen / SDK fixes |
| `mcp-paradigm-py` | Semver tied to `paradigm-py`; bumped to keep tool surface stable across SDK upgrades |
| OpenAPI spec | Pinned via a git commit hash in `spec/paradigm-openapi.yaml` + `scripts/regen.sh` |

When upstream spec changes:

1. Bump `PINNED_COMMIT` in `scripts/regen.sh`.
2. Run regen.
3. CI surfaces breaking-change diff in generated code.
4. Add a one-line entry to `CHANGELOG.md` per affected endpoint.
5. Tag a release.

---

## 11. Deployment posture

- One MCP server process per agent host (or per user, if you do
  per-user isolation). Stateless except for the WS buffer.
- Reverse-proxy behind your OAuth boundary (e.g. `docker/mcp-gateway`
  or `metamcp`) for centralized auth — saves re-implementing the
  resource-metadata flow.
- Container runs as non-root, mlock-friendly (signing layer may pin
  small amounts of memory).
- Read-only filesystem; config mounted as a configmap; signing layer
  reaches Vault / KMS over network.

---

## 12. Open questions for the new repo

These are worth deciding before first commit; flagging here so they
don't get lost.

1. **Per-session vs per-process WS connection.** Per-session
   (recommended) needs the MCP server to map OAuth `sub` to a WS
   connection identity. Per-process is simpler but conflates fills
   across users — only acceptable for single-tenant deployments.
2. **Should `paradigm_post_order` block until terminal state, or return
   immediately with `PENDING`?** Both have valid use cases. Default to
   return-immediately + tool annotation that the caller should poll
   `paradigm_order` or subscribe to `order` channel. Add an optional
   `wait_for_terminal: bool = false` param for the easy case.
3. **Step-up auth for `paradigm:post_order`** — require a fresh `acr`
   claim per call, or once per session? Per-call is safer for live
   money but breaks maker streaming. Resolution: per-session by default,
   per-call configurable.
4. **Idempotency** — Paradigm's `label` field is documented but no
   explicit idempotency-on-retry guarantee. If the server retries a
   `paradigm_post_order` that returned 5xx, do we risk a double order?
   Mitigation: never retry POSTs from inside the MCP server; surface
   the error to the caller and let the agent decide.
5. **Cancel-on-disconnect default** for WS subscriptions — `true` is
   safer for makers, `false` is safer for read-only agents. Pick a
   default and let the caller override.

---

## 13. Path from the current skill to MCP-backed skill

For the v3.0 refactor of `paradigm-rfq-trader` once `mcp-paradigm-py`
ships:

- Drop the inline JSON payload templates in Step 2 — they become tool
  parameters.
- Drop the "Step 3 — Sign and send" section entirely — the MCP server
  handles it.
- Replace Step 4a / 4b REST sections with the MCP tool sequence:
  `paradigm_instruments` → `paradigm_create_rfq` (gated) →
  `paradigm_rfq_orders` polling or `paradigm_subscribe(channel=order)`
  → `paradigm_rfq_bbo` for benchmark → `paradigm_post_order` (gated,
  FOK to cross).
- Keep the confirmation-gate logic — that's where the skill earns its
  keep. The MCP `requires_approval` annotation is a defence-in-depth
  layer at the tool boundary; the skill's confirmation gate is the
  user-facing UX.
- `references/auth.md` and `references/test-signing.py` migrate to
  `paradigm-py/tests/`. The skill keeps a stub note linking out.
- `references/endpoints.md` and `references/instruments.md` largely
  become MCP server design docs and migrate to `mcp-paradigm-py/docs/`.
- The skill body shrinks from ~430 lines to ~150.

Until v3.0 ships, the current v2.0 skill is the working interim — it
exercises the same payloads the MCP will build, so v2.0 effectively
de-risks the SDK by validating our reading of the spec against a live
endpoint.
