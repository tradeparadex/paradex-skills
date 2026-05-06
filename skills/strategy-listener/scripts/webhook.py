"""
webhook.py — async OpenCLAW POST worker.

Contract: see references/webhook-contract.md.

Behaviour:
  - bearer token from env var named in webhook.tokenEnv
  - retry policy: 3 attempts with 0s/1s/4s back-off, only on 5xx / network errors
  - 4xx (other than 429) is a permanent failure — no retry
  - includes correlationId, firedAt, source in every body
  - dry-run mode logs the would-be POST and skips the network call
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx


log = logging.getLogger("listener.webhook")


# ── Public API ────────────────────────────────────────────────────────────────


class WebhookSender:
    """
    Owns one shared httpx.AsyncClient and an async queue of POST jobs.
    Up to `concurrency` POSTs run in parallel.
    """

    def __init__(self, *, dry_run: bool = False, concurrency: int = 4):
        self.dry_run = dry_run
        self._client: Optional[httpx.AsyncClient] = None
        self._sem = asyncio.Semaphore(concurrency)

    async def __aenter__(self) -> "WebhookSender":
        if not self.dry_run:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def fire(
        self,
        *,
        webhook_cfg: dict,
        message: str,
        correlation_id: str,
    ) -> dict:
        """
        Build and send the POST. Returns a dict describing the outcome:
          {ok: bool, status: int|None, attempts: int, error: str|None,
           correlation_id: str, url: str}
        Never raises — failure is reported via the return value.
        """
        url = webhook_cfg["url"]
        token_env = webhook_cfg.get("tokenEnv") or "OPENCLAW_TOKEN"
        token = os.environ.get(token_env)

        body: dict[str, Any] = dict(webhook_cfg.get("extra") or {})
        # OpenCLAW /hooks/wake uses `text`; everything else uses `message`.
        # We emit both for safety — extra fields are ignored by the gateway.
        body.setdefault("message", message)
        body.setdefault("text", message)
        body["correlationId"] = correlation_id
        body["firedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        body["source"] = "paradex-strategy-listener"

        if self.dry_run:
            log.info(json.dumps({
                "event": "webhook_dryrun",
                "url": url,
                "correlation_id": correlation_id,
                "body_preview": _preview(body),
            }))
            return {"ok": True, "status": None, "attempts": 0,
                    "error": None, "correlation_id": correlation_id, "url": url}

        if not token:
            log.warning(json.dumps({
                "event": "webhook_skip_no_token",
                "url": url,
                "token_env": token_env,
                "correlation_id": correlation_id,
            }))
            return {"ok": False, "status": None, "attempts": 0,
                    "error": f"missing env {token_env}",
                    "correlation_id": correlation_id, "url": url}

        async with self._sem:
            return await self._send_with_retry(url, token, body, correlation_id)

    # ── internals ────────────────────────────────────────────────────────────

    async def _send_with_retry(
        self,
        url: str,
        token: str,
        body: dict,
        correlation_id: str,
    ) -> dict:
        assert self._client is not None
        delays = (0.0, 1.0, 4.0)
        last_status: Optional[int] = None
        last_error: Optional[str] = None

        for attempt, delay in enumerate(delays, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await self._client.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
                last_status = resp.status_code
                if 200 <= resp.status_code < 300:
                    log.info(json.dumps({
                        "event": "webhook_ok",
                        "url": url,
                        "status": resp.status_code,
                        "attempts": attempt,
                        "correlation_id": correlation_id,
                    }))
                    return {"ok": True, "status": resp.status_code,
                            "attempts": attempt, "error": None,
                            "correlation_id": correlation_id, "url": url}
                # 4xx (except 429) — permanent, don't retry
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    last_error = f"http {resp.status_code}: {resp.text[:200]}"
                    break
                last_error = f"http {resp.status_code}"
            except (httpx.HTTPError, asyncio.TimeoutError) as e:
                last_error = f"{type(e).__name__}: {e}"
                last_status = None

        log.error(json.dumps({
            "event": "webhook_failed",
            "url": url,
            "status": last_status,
            "attempts": len(delays),
            "error": last_error,
            "correlation_id": correlation_id,
        }))
        return {"ok": False, "status": last_status, "attempts": len(delays),
                "error": last_error, "correlation_id": correlation_id, "url": url}


def _preview(body: dict) -> dict:
    """Truncate long string fields for log readability."""
    return {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v)
            for k, v in body.items()}


def render_message(template: str, vars: dict[str, Any]) -> str:
    """
    Format `template` with `vars`. Missing keys render as None instead of
    raising — a partially-populated template is better than a hard failure
    in a long-running daemon.
    """
    class _SafeDict(dict):
        def __missing__(self, key: str) -> Any:
            return None
    try:
        return template.format_map(_SafeDict(vars))
    except (KeyError, IndexError, ValueError) as e:
        log.warning(json.dumps({
            "event": "template_render_error",
            "error": str(e),
            "template_preview": template[:100],
        }))
        return template


def correlation_id(strategy_name: str, evaluator_id: str, event_ts_ms: Optional[int] = None) -> str:
    ts = event_ts_ms if event_ts_ms is not None else int(time.time() * 1000)
    return f"{strategy_name}/{evaluator_id}/{ts}"
