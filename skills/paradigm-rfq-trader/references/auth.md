# Auth — Paradigm DRFQv2 HMAC-SHA256 Signing

Paradigm REST authenticates every request with a triple of headers:
`Authorization` (bearer access key), `Paradigm-API-Timestamp` (ms since
epoch), and `Paradigm-API-Signature` (base64 HMAC-SHA256 over a canonical
string). The signing scheme below matches the reference implementation at
[`tradeparadigm/code-samples`](https://github.com/tradeparadigm/code-samples)
(`python/api/signature.py`).

## Credentials contract

| Env var | Meaning | Required? |
|---|---|---|
| `PARADIGM_ACCESS_KEY` | Opaque string. Sent as `Authorization: Bearer <KEY>` | Yes |
| `PARADIGM_SIGNING_KEY` | Base64-encoded HMAC key. Decoded with `base64.b64decode` before HMAC | **Yes, always — see OneCLI section below** |
| `PARADIGM_ACCOUNT` | Desk / account selector for multi-desk keys | Optional |
| `PARADIGM_ENV` | `prod` (default) or `test` — picks `api.paradigm.co` vs `api.test.paradigm.co` | Optional |

**Never** echo any credential in a response, code snippet, log line, error
message, commit, or filename. If the user asks "what's my key?", refuse
and point at the proxy.

## Signing recipe

```python
import base64, hashlib, hmac, json, time

def sign(method: str, path: str, body: dict | None,
         access_key: str, signing_key_b64: str) -> tuple[bytes, dict]:
    """Return (body_bytes_to_post, headers). Post the SAME body_bytes."""
    body_bytes = json.dumps(body, separators=(",", ":")).encode() if body else b""
    ts = str(int(time.time() * 1000))
    msg = b"\n".join([ts.encode(), method.upper().encode(), path.encode(), body_bytes])
    sig = base64.b64encode(
        hmac.new(base64.b64decode(signing_key_b64), msg, hashlib.sha256).digest()
    ).decode()
    headers = {
        "Authorization": f"Bearer {access_key}",
        "Paradigm-API-Timestamp": ts,
        "Paradigm-API-Signature": sig,
        "Content-Type": "application/json",
    }
    return body_bytes, headers
```

**Use the returned `body_bytes` verbatim.** Do not call `json.dumps` again
on the dict before posting — Python's default separators add spaces that
were not in the signed bytes, and the signature will mismatch.

## Self-test the signing locally

This directory ships [`test-signing.py`](test-signing.py) with pinned
synthetic vectors. Run it to confirm the implementation is byte-identical
to the reference:

```bash
python3 skills/paradigm-rfq-trader/references/test-signing.py
```

Expected output ends with `All signing tests passed.`. The tests cover
the POST-with-body case, the GET-with-empty-body case, body-byte
sensitivity (proving why re-serialization breaks auth), key sensitivity,
and timestamp sensitivity. Run after any change to the signing helper.

## Credentials proxy — OneCLI

The Paradex environment uses [OneCLI](https://onecli.sh) as the
credentials proxy. **Important: OneCLI is a header-substitution proxy,
not a signing proxy.** It does not understand HMAC schemes — it cannot
generate `Paradigm-API-Signature` or `Paradigm-API-Timestamp`. So the
skill **must always compute the signature itself** using
`PARADIGM_SIGNING_KEY`. OneCLI handles only the static parts:

| Header / value | Who fills it |
|---|---|
| `Authorization: Bearer <access-key>` | OneCLI swaps a placeholder for the real key at proxy time |
| `Paradigm-API-Timestamp` | **The skill** (generated per-request) |
| `Paradigm-API-Signature` | **The skill** (HMAC computed locally per-request) |

How it works in practice:

1. `HTTPS_PROXY=http://localhost:10255` is set in the agent's environment.
2. Agent's code holds a **placeholder** access key (e.g. the literal
   string `ONECLI_PARADIGM_ACCESS_KEY`). The signing key
   (`PARADIGM_SIGNING_KEY`) is in the env directly — OneCLI does not
   substitute it because HMAC computation happens in-process, not at the
   proxy layer.
3. Skill builds the request, signs it with the real signing key, sets
   the Authorization header to `Bearer ONECLI_PARADIGM_ACCESS_KEY`.
4. HTTPS request goes through `localhost:10255`. OneCLI matches the
   target host (`api.paradigm.co`) against its rule set, rewrites the
   Authorization header to `Bearer <real-access-key>`, and forwards.
5. Paradigm receives the request with the real access key in the header
   and the signature already computed with the real signing key.

This is why both keys are still required to be reachable by the skill —
OneCLI removes only the access key from the agent's view, the signing
key still lives in the agent's process. For stricter setups, store the
signing key in a different secrets manager and inject only at start.

### Setting up your Paradigm key in OneCLI

If a user asks how to register a Paradigm key in OneCLI, walk them
through this. Do **not** ask the user to paste keys into the chat — keys
go directly into the OneCLI dashboard, not through the agent.

1. **Install OneCLI** (one-time, on the machine that runs the agent):

   ```bash
   curl -fsSL https://onecli.sh/install | sh
   ```

2. **Open the OneCLI admin dashboard** (default `http://localhost:10255/admin`).

3. **Add a new credential** with these fields:

   | Field | Value |
   |---|---|
   | Name / label | `paradigm-access-key` (any identifier) |
   | Placeholder | `ONECLI_PARADIGM_ACCESS_KEY` (the literal string the agent will send) |
   | Secret | the real Paradigm access key |
   | Host match | `api.paradigm.co` (and `api.test.paradigm.co` for testnet) |
   | Header match | `Authorization` |

4. **Set the agent's environment** before launching:

   ```bash
   export HTTPS_PROXY=http://localhost:10255
   export PARADIGM_ACCESS_KEY=ONECLI_PARADIGM_ACCESS_KEY   # placeholder, not real
   export PARADIGM_SIGNING_KEY=<base64 signing key>        # real value — see note
   ```

   The signing key is **not** proxied through OneCLI (HMAC happens
   in-process). Put it in env directly, or front it with a secrets
   manager that materializes it at process start.

5. **Verify**:

   ```bash
   python3 skills/paradigm-rfq-trader/references/test-signing.py
   ```

   Then a low-risk live call against testnet:

   ```bash
   curl -i https://api.test.paradigm.co/v1/drfq/instruments/ \
        -H "Authorization: Bearer $PARADIGM_ACCESS_KEY" \
        -H "Paradigm-API-Timestamp: <ts>" \
        -H "Paradigm-API-Signature: <sig>"
   ```

   A 200 with an instruments list confirms OneCLI swapped the header and
   the signature was valid. A 401 means the signing key is wrong or the
   placeholder didn't match a OneCLI rule — see the 401 root-cause list
   below.

## Path canonicalisation

The `path` in the signing string is the URL path **without the host** and
**including the leading `/`**, plus the query string if any.

| Request | Signing-string `path` |
|---|---|
| `POST /v1/drfq/rfqs/` | `/v1/drfq/rfqs/` |
| `GET /v1/drfq/rfqs/?status=ACTIVE` | `/v1/drfq/rfqs/?status=ACTIVE` |
| `DELETE /v1/drfq/rfqs/rfq_abc123` | `/v1/drfq/rfqs/rfq_abc123` |

Trailing slashes matter — match the documented endpoint exactly.

## Body canonicalisation

- `GET` / `DELETE` with no body: signing-string body segment is empty.
  The newline separator before it is still required (`ts\nMETHOD\npath\n`).
- `POST` / `PATCH`: pass the exact bytes you will POST. Use compact JSON
  (`separators=(",", ":")`) or any other serialization — just be
  consistent.

## WebSocket auth

WS does **not** use HMAC signing. Pass the access key as a query
parameter on the connection URL:

```
wss://ws.api.paradigm.trade/v2/drfq/?api-key=${PARADIGM_ACCESS_KEY}&cancel_on_disconnect=false
```

OneCLI does not proxy WebSocket traffic out of the box. For WS, either
keep the real access key in env, or run the WS leg outside the OneCLI
boundary.

For makers, set `cancel_on_disconnect=true` to pull all live quotes if
the connection drops. Subscribe messages use JSON-RPC 2.0 — see
`endpoints.md`.

## Base URLs

| Env | REST | WS |
|---|---|---|
| Prod (`PARADIGM_ENV=prod` / default) | `https://api.paradigm.co` | `wss://ws.api.paradigm.trade/v2/drfq/` |
| Testnet (`PARADIGM_ENV=test`) | `https://api.test.paradigm.co` | `wss://ws.api.testnet.paradigm.trade/v2/drfq/` |

## Common 401 root causes

Diagnose in this order — most failures are at the top.

1. **Body re-serialized after signing.** The signature was computed over
   one byte sequence, you POSTed another. Always pass the exact bytes
   returned from the signing function. The `test-signing.py` body-byte
   sensitivity test demonstrates this.
2. **Clock skew.** Paradigm's timestamp window is tight (a few seconds).
   On systematic 401s with a valid key, check `date -u` against an NTP
   source.
3. **OneCLI didn't substitute the Authorization header.** Confirm the
   placeholder string in env exactly matches the placeholder in the
   OneCLI rule, and that `HTTPS_PROXY` is set. A request hitting
   `api.paradigm.co` directly with the placeholder still in the
   Authorization header will 401.
4. **Missing `Bearer ` prefix** on the `Authorization` header.
5. **Forgot to base64-decode the signing key** before HMAC. The env
   value is already base64; HMAC needs the decoded bytes.
6. **Path mismatch** — missing trailing slash, missing query string in
   the signing string, or signed `/v1/...` but called `/v2/...`.
7. **Stale timestamp** between signing and sending — sign immediately
   before posting; don't reuse old `ts` values.

## SDK / codegen status

There is no published Paradigm RFQ SDK on PyPI / npm. The only first-party
client today is
[`tradeparadigm/code-samples`](https://github.com/tradeparadigm/code-samples)
— reference scripts, not a packaged SDK.

**An official OpenAPI spec is being added in `tradeparadigm/mono#34164`.**
Once that lands, codegen becomes the recommended path:

| Tool | Output | Notes |
|---|---|---|
| `openapi-python-client` | Async + sync Python client with `httpx`, full Pydantic models | Pick this if you want typed request/response models out of the box |
| `datamodel-code-generator` | Pydantic models only | Pair with a hand-written transport layer if you want fine control over signing/retries |
| `openapi-generator-cli` | Multi-language (Python, TS, Go) | Use for cross-language SDKs; output is larger but reusable beyond Python |

Codegen output **does not** know about Paradigm's HMAC scheme — the
generated client will assume Bearer-only auth. Wrap the generated
transport with the `sign()` helper above (or an `httpx` auth hook that
calls it) so every request leaves with `Paradigm-API-Timestamp` and
`Paradigm-API-Signature` set correctly.

The recommended layering once the spec is merged:

```
mcp-paradigm-py (FastMCP)        ← per-tool surface for agents
        │
        ▼
paradigm-py (codegen + signing)  ← typed client wrapped with HMAC auth
        │
        ▼
Paradigm REST + WS               ← upstream
```

Track the spec PR rather than vendoring — pin to a commit hash and
re-generate when it advances. Until the PR merges, the in-skill HMAC
helper above remains authoritative.
