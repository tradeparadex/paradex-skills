# Auth — REST fallback for Paradigm DRFQv2

This file is for the **fallback path** when the
[`mcp-paradigm-py`](https://github.com/tradeparadigm/mcp-paradigm-py)
MCP server is not available. The MCP server handles signing in its
own process; nothing here is needed when it's installed.

## Scheme

Every authenticated REST call carries three headers:

- `Authorization: Bearer <PARADIGM_ACCESS_KEY>`
- `Paradigm-API-Timestamp: <ms-since-epoch>`
- `Paradigm-API-Signature: <base64(HMAC_SHA256(decoded_signing_key, msg))>`

The signing `msg` is the literal string:

```
<timestamp_ms>\n<METHOD>\n<path-with-query>\n<body>
```

`<body>` is the raw request bytes you will post (empty for GET).
`<METHOD>` is uppercase. Timestamps outside ±30 s of server time are
rejected.

## Recipe

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

**Use the returned `body_bytes` verbatim.** Re-serializing the dict
after signing introduces whitespace and breaks the signature.

To verify a signing implementation end-to-end against a live endpoint,
call `GET /v2/drfq/echo/` with proper headers — a 200 confirms the
full stack (key, signature, headers, transport) is correct. The
`mcp-paradigm-py` repo has unit tests with pinned synthetic vectors
if you need to reproduce the math offline.

## Credentials

| Env var | Meaning |
|---|---|
| `PARADIGM_ACCESS_KEY` | Bearer access key |
| `PARADIGM_SIGNING_KEY` | Base64-encoded HMAC key |
| `PARADIGM_ACCOUNT` | Optional desk selector for multi-desk keys |

Never echo, log, or commit these values. If the user asks "what's my
key?", refuse and point at the MCP config or the upstream key portal
at `app.paradigm.co`.

[OneCLI](https://onecli.sh) can substitute the placeholder `Bearer`
header via `HTTPS_PROXY` regardless of whether you're on the MCP or
the REST path. It does not generate signatures — those still come
from the `sign()` helper above.

## Base URLs

| Env | REST | WS |
|---|---|---|
| Prod | `https://api.prod.paradigm.co` | `wss://ws.api.prod.paradigm.trade/v2/drfq/` |
| Testnet | `https://api.testnet.paradigm.co` | `wss://ws.api.testnet.paradigm.trade/v2/drfq/` |

WS auth does not use HMAC — pass the access key as the `api-key` query
parameter on the connection URL.

## Path canonicalisation

The `path` in the signing string is the URL path **with leading `/`**
and the query string if any. Trailing slashes matter.

| Request | Signing-string `path` |
|---|---|
| `POST /v2/drfq/rfqs/` | `/v2/drfq/rfqs/` |
| `GET /v2/drfq/rfqs/?state=RFQState.OPEN` | `/v2/drfq/rfqs/?state=RFQState.OPEN` |
| `DELETE /v2/drfq/rfqs/rfq_abc` | `/v2/drfq/rfqs/rfq_abc` |

## 401 root causes (diagnose in order)

1. **Body re-serialized after signing.** Pass exact bytes both to the
   signer and to the request.
2. **Clock skew** — ±30 s window. Check `date -u` against NTP.
3. **Missing `Bearer ` prefix** on `Authorization`.
4. **Forgot to base64-decode the signing key** before HMAC.
5. **Path mismatch** — missing trailing slash, missing query string,
   wrong API version.
6. **Stale timestamp** — sign immediately before sending.

First call to make after wiring: `GET /v2/drfq/echo/` (or
`POST /v2/drfq/echo/` for body-byte verification). 200 means signing
+ auth are correct.
