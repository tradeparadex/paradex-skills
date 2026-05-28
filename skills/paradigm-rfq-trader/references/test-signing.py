#!/usr/bin/env python3
"""Self-test for Paradigm DRFQv2 HMAC-SHA256 signing.

Run:    python3 test-signing.py
Expect: prints "OK" lines and exits 0.

This file pins synthetic test vectors so anyone implementing or modifying
the signing helper can verify their work without hitting Paradigm. The
signing key, access key, and timestamp below are SYNTHETIC — they are
not real credentials.

The same code path is documented in auth.md; this file is the executable
form so you can re-run after refactors.
"""

import base64
import hashlib
import hmac
import json
import sys


def sign(method: str, path: str, body_bytes: bytes,
         signing_key_b64: str, ts_ms: str) -> str:
    """Return the base64 HMAC-SHA256 signature for one Paradigm request.

    body_bytes MUST be the exact bytes that will be POSTed — never re-serialize
    the JSON between this call and the request.
    """
    msg = b"\n".join([
        ts_ms.encode(),
        method.upper().encode(),
        path.encode(),
        body_bytes,
    ])
    return base64.b64encode(
        hmac.new(base64.b64decode(signing_key_b64), msg, hashlib.sha256).digest()
    ).decode()


# Synthetic test key — 32 bytes base64'd. NOT a real Paradigm credential.
TEST_SIGNING_KEY_B64 = "dGVzdC1zaWduaW5nLWtleS0zMi1ieXRlcy1wYWRkaW5nIQ=="
TEST_TS = "1745612345678"


def test_pinned_post() -> None:
    """POST /v2/drfq/rfqs/ with a small JSON body matches the pinned signature."""
    body_bytes = b'{"venue":"DBT","quantity":"100"}'
    sig = sign("POST", "/v2/drfq/rfqs/", body_bytes, TEST_SIGNING_KEY_B64, TEST_TS)
    expected = "SBwBtCGQCykWchSilPGwSAW/oNQiXpnw359h8wv+9uU="
    assert sig == expected, f"POST pinned vector mismatch: got {sig}, want {expected}"
    print(f"OK  POST  /v2/drfq/rfqs/         → {sig}")


def test_pinned_get_empty_body() -> None:
    """GET with empty body still includes the trailing newline separator."""
    sig = sign("GET", "/v2/drfq/instruments/", b"", TEST_SIGNING_KEY_B64, TEST_TS)
    expected = "n/7GHWN1wQuLewPLacgCrSQDU9KGUTn+7VJ5N4oj8pE="
    assert sig == expected, f"GET pinned vector mismatch: got {sig}, want {expected}"
    print(f"OK  GET   /v2/drfq/instruments/  → {sig}")


def test_body_byte_sensitivity() -> None:
    """Re-serializing JSON after signing breaks the signature. Prove it."""
    compact = b'{"venue":"DBT","quantity":"100"}'
    spaced = json.dumps({"venue": "DBT", "quantity": "100"}).encode()
    assert compact != spaced, "test setup wrong: compact and spaced should differ"
    sig_a = sign("POST", "/v2/drfq/rfqs/", compact, TEST_SIGNING_KEY_B64, TEST_TS)
    sig_b = sign("POST", "/v2/drfq/rfqs/", spaced, TEST_SIGNING_KEY_B64, TEST_TS)
    assert sig_a != sig_b, "signatures must differ when body bytes differ"
    print(f"OK  body re-serialization changes signature (expected)")


def test_key_sensitivity() -> None:
    """Different signing keys yield different signatures."""
    body = b'{"venue":"DBT"}'
    other_key = base64.b64encode(b"a-different-32-byte-test-keyXXXXX").decode()
    sig_a = sign("POST", "/v2/drfq/rfqs/", body, TEST_SIGNING_KEY_B64, TEST_TS)
    sig_b = sign("POST", "/v2/drfq/rfqs/", body, other_key, TEST_TS)
    assert sig_a != sig_b, "signatures must differ for different keys"
    print(f"OK  signing key affects signature (expected)")


def test_timestamp_sensitivity() -> None:
    """A stale timestamp changes the signature — sign immediately before POST."""
    body = b'{"venue":"DBT"}'
    sig_a = sign("POST", "/v2/drfq/rfqs/", body, TEST_SIGNING_KEY_B64, "1745612345678")
    sig_b = sign("POST", "/v2/drfq/rfqs/", body, TEST_SIGNING_KEY_B64, "1745612345679")
    assert sig_a != sig_b, "timestamp must affect signature"
    print(f"OK  timestamp affects signature (expected)")


if __name__ == "__main__":
    try:
        test_pinned_post()
        test_pinned_get_empty_body()
        test_body_byte_sensitivity()
        test_key_sensitivity()
        test_timestamp_sensitivity()
        print("\nAll signing tests passed.")
    except AssertionError as e:
        print(f"\nFAIL: {e}", file=sys.stderr)
        sys.exit(1)
