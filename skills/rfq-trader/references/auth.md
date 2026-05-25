# Auth — Paradigm DRFQv2 HMAC-SHA256 Signing

Paradigm REST authenticates every request with a triple of headers:
`Authorization` (bearer access key), `Paradigm-API-Timestamp` (ms since
epoch), and `Paradigm-API-Signature` (base64 HMAC-SHA256 over a canonical
string). The signing scheme below matches the reference implementation at
[`tradeparadigm/code-samples`](https://github.com/tradeparadigm/code-samples)
(`python/api/signature.py`).

## Credentials contract

The credentials proxy injects these at request time. Read at the moment of
the call — do not cache across requests.

| Env var | Meaning | Required? |
|---|---|---|
| `PARADIGM_ACCESS_KEY` | Opaque string. Sent as `Authorization: Bearer <KEY>` | Yes |
| `PARADIGM_SIGNING_KEY` | Base64-encoded HMAC key. Decoded with `base64.b64decode` before HMAC | Yes, unless proxy signs upstream (see fallback) |
| `PARADIGM_ACCOUNT` | Desk / account selector for multi-desk keys | Optional |
| `PARADIGM_ENV` | `prod` (default) or `test` — picks `api.paradigm.co` vs `api.test.paradigm.co` | Optional |

**Fallback — upstream signing.** If `PARADIGM_SIGNING_KEY` is not in the
environment, assume an upstream tool / proxy intercepts the outbound
request and attaches `Authorization`, `Paradigm-API-Timestamp`, and
`Paradigm-API-Signature` headers transparently. In that mode the skill
posts the body as-is and lets the proxy sign. Detect the mode by env
presence; do not branch on response codes.

**Never** echo any credential in a response, code snippet, log line, error
message, commit, or filename. If the user asks "what's my key?", refuse
and point at the proxy.

## Signing recipe

```python
import base64
import hashlib
import hmac
import json
import time

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

## Path canonicalisation

The `path` in the signing string is the URL path **without the host** and
**including the leading `/`**, plus the query string if any. Examples:

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
- Field order: not standardized by Paradigm, but pick one and never
  re-serialize between signing and posting.

## WebSocket auth

WS does **not** use HMAC signing. Pass the access key as a query
parameter on the connection URL:

```
wss://ws.api.paradigm.trade/v2/drfq/?api-key=${PARADIGM_ACCESS_KEY}&cancel_on_disconnect=false
```

For makers, set `cancel_on_disconnect=true` to pull all live quotes if the
connection drops. Subscribe messages use JSON-RPC 2.0 — see
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
   returned from the signing function.
2. **Clock skew.** Paradigm's timestamp window is tight (a few seconds).
   On systematic 401s with a valid key, check `date -u` against an NTP
   source.
3. **Missing `Bearer ` prefix** on the `Authorization` header.
4. **Forgot to base64-decode the signing key** before HMAC. The
   environment value is already base64; HMAC needs the decoded bytes.
5. **Path mismatch** — missing trailing slash, missing query string in the
   signing string, or signed `/v1/...` but called `/v2/...`.
6. **Stale timestamp** between signing and sending — sign immediately
   before posting; don't reuse old `ts` values.

## Testing the signing without hitting prod

Point at testnet (`PARADIGM_ENV=test`, base URL `api.test.paradigm.co`)
with a testnet key pair. Auth scheme is identical. A successful
`GET /v1/drfq/instruments/` confirms signing is correct end-to-end.
