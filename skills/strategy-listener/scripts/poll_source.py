"""
poll_source.py — HTTP polling fallback that emits the same event shape as ws_source.

Used when dataMode=poll, or in dataMode=auto after WS gives up. Polls the
Paradex public REST endpoints at `interval_sec` and synthesises events for
each declared channel token.

Polling tracks a "last seen" cursor per stream so we only emit *new*
trades/fills/funding rows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

import httpx


log = logging.getLogger("listener.poll")


class PollSource:
    """
    Polls REST endpoints and pushes normalised events onto out_queue.

    Channel coverage is intentionally smaller than WS: bbo, funding, trades,
    fills are supported. Orders / positions / orderbook are WS-only for v1.
    """

    def __init__(
        self,
        api_url: str,
        channels: list[str],
        out_queue: asyncio.Queue,
        *,
        interval_sec: int = 15,
        bearer_token: Optional[str] = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.channels = list(dict.fromkeys(channels))
        self.out_queue = out_queue
        self.interval_sec = max(1, interval_sec)
        self.bearer_token = bearer_token
        self._stop = asyncio.Event()
        # cursors per channel
        self._last_trade_ts: dict[str, int] = {}
        self._last_fill_id: Optional[str] = None

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        headers = {"User-Agent": "paradex-strategy-listener/1.0"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), headers=headers) as client:
            log.info(json.dumps({"event": "poll_started",
                                 "interval_sec": self.interval_sec,
                                 "channels": self.channels}))
            while not self._stop.is_set():
                start = time.monotonic()
                try:
                    await self._poll_once(client)
                except Exception as e:
                    log.error(json.dumps({"event": "poll_error",
                                          "error": f"{type(e).__name__}: {e}"}))
                elapsed = time.monotonic() - start
                await asyncio.sleep(max(0.1, self.interval_sec - elapsed))

    async def _poll_once(self, client: httpx.AsyncClient) -> None:
        for token in self.channels:
            head, _, market = token.partition(".")
            if head == "bbo" and market:
                await self._poll_bbo(client, market)
            elif head == "funding" and market:
                await self._poll_funding(client, market)
            elif head == "trades" and market:
                await self._poll_trades(client, market)
            elif head == "fills":
                await self._poll_fills(client)
            elif head == "markets_summary":
                await self._poll_summary(client)
            # Other channels: not supported in poll mode (logged once at start)

    # ── per-stream pollers ────────────────────────────────────────────────────

    async def _poll_bbo(self, client: httpx.AsyncClient, market: str) -> None:
        r = await client.get(f"{self.api_url}/bbo/{market}")
        if r.status_code != 200:
            return
        data = r.json() or {}
        bid = _f(data.get("bid"))
        ask = _f(data.get("ask"))
        await self.out_queue.put({
            "type": f"bbo.{market}",
            "market": market,
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2 if bid is not None and ask is not None else None,
        })

    async def _poll_funding(self, client: httpx.AsyncClient, market: str) -> None:
        r = await client.get(f"{self.api_url}/markets/summary",
                             params={"market": market})
        if r.status_code != 200:
            return
        results = (r.json() or {}).get("results") or []
        if not results:
            return
        row = results[0]
        await self.out_queue.put({
            "type": f"funding.{market}",
            "market": market,
            "funding": _f(row.get("funding_rate")),
            "mark_price": _f(row.get("mark_price")),
        })

    async def _poll_trades(self, client: httpx.AsyncClient, market: str) -> None:
        r = await client.get(f"{self.api_url}/trades",
                             params={"market": market, "page_size": 50})
        if r.status_code != 200:
            return
        results = (r.json() or {}).get("results") or []
        last = self._last_trade_ts.get(market, 0)
        new_last = last
        for t in reversed(results):  # oldest → newest
            ts = int(t.get("created_at") or 0)
            if ts <= last:
                continue
            new_last = max(new_last, ts)
            await self.out_queue.put({
                "type": f"trades.{market}",
                "market": market,
                "price": _f(t.get("price")),
                "size": _f(t.get("size")),
                "side": t.get("side"),
                "ts": ts,
            })
        self._last_trade_ts[market] = new_last

    async def _poll_fills(self, client: httpx.AsyncClient) -> None:
        if not self.bearer_token:
            return
        r = await client.get(f"{self.api_url}/fills", params={"page_size": 50})
        if r.status_code != 200:
            return
        results = (r.json() or {}).get("results") or []
        if not results:
            return
        emitted = 0
        for f in reversed(results):  # oldest → newest
            fid = f.get("id") or f.get("fill_id")
            if fid == self._last_fill_id:
                # we've caught up
                break
        else:
            # last_fill_id wasn't found — emit only the newest 5 to avoid backfill spam
            results = results[:5]
        for f in reversed(results):
            fid = f.get("id") or f.get("fill_id")
            if self._last_fill_id and fid == self._last_fill_id:
                continue
            await self.out_queue.put({
                "type": "fills",
                "market": f.get("market"),
                "side": f.get("side"),
                "size": _f(f.get("size")),
                "price": _f(f.get("price")),
                "fill_id": fid,
                "order_id": f.get("order_id"),
            })
            emitted += 1
        if results:
            self._last_fill_id = results[0].get("id") or results[0].get("fill_id")
        if emitted:
            log.info(json.dumps({"event": "poll_fills_emitted", "count": emitted}))

    async def _poll_summary(self, client: httpx.AsyncClient) -> None:
        r = await client.get(f"{self.api_url}/markets/summary")
        if r.status_code != 200:
            return
        results = (r.json() or {}).get("results") or []
        for row in results:
            mkt = row.get("symbol")
            await self.out_queue.put({
                "type": f"funding.{mkt}",
                "market": mkt,
                "funding": _f(row.get("funding_rate")),
                "mark_price": _f(row.get("mark_price")),
            })


def _f(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
