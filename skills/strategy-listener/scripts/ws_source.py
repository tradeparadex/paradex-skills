"""
ws_source.py — Paradex WebSocket subscription + reconnect.

Wraps paradex_py.ws_client. Translates the listener's dotted-token form
("bbo.BTC-USD-PERP") into the SDK's (ParadexWebsocketChannel, params) pair,
normalises inbound messages into a uniform event shape, and drops them on
an asyncio.Queue for the dispatcher.

Reconnect: exponential backoff 1s → 4s → 16s → 60s cap. Re-subscribes on
every reconnect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from paradex_py.api.ws_client import ParadexWebsocketChannel


log = logging.getLogger("listener.ws")


# ── Token → (channel enum, params) ────────────────────────────────────────────


_PARAMETERISED = {
    "bbo": ParadexWebsocketChannel.BBO,
    "trades": ParadexWebsocketChannel.TRADES,
    "funding": ParadexWebsocketChannel.FUNDING_DATA,
    "fills": ParadexWebsocketChannel.FILLS,
    "orders": ParadexWebsocketChannel.ORDERS,
    "mark_price": ParadexWebsocketChannel.MARKETS_SUMMARY,  # mark price ships in markets_summary
}

_GLOBAL = {
    "positions": ParadexWebsocketChannel.POSITIONS,
    "account": ParadexWebsocketChannel.ACCOUNT,
    "balance_events": ParadexWebsocketChannel.BALANCE_EVENTS,
    "markets_summary": ParadexWebsocketChannel.MARKETS_SUMMARY,
    "tradebusts": ParadexWebsocketChannel.TRADEBUSTS,
}

# Token aliases — the listener treats `fills` / `orders` without a market as
# "all markets" (the SDK MARKETS_SUMMARY pattern). Some user channels
# require a market param; for those we expand to ALL.
_USER_DEFAULT_ALL = {"fills", "orders"}

_TOKEN_RE = re.compile(r"^([a-z_]+)(?:\.(.+))?$")


def parse_token(token: str) -> tuple[ParadexWebsocketChannel, dict]:
    """
    Translate a strategy-file token into (SDK channel, params).

      "bbo.BTC-USD-PERP"     → (BBO, {"market": "BTC-USD-PERP"})
      "trades.BTC-USD-PERP"  → (TRADES, {"market": "BTC-USD-PERP"})
      "funding.BTC-USD-PERP" → (FUNDING_DATA, {"market": "BTC-USD-PERP"})
      "fills"                → (FILLS, {"market": "ALL"})
      "fills.BTC-USD-PERP"   → (FILLS, {"market": "BTC-USD-PERP"})
      "positions"            → (POSITIONS, {})
      "account"              → (ACCOUNT, {})
    """
    m = _TOKEN_RE.match(token.strip())
    if not m:
        raise ValueError(f"unparseable channel token: {token!r}")
    head, tail = m.group(1), m.group(2)
    if head in _PARAMETERISED:
        channel = _PARAMETERISED[head]
        market = tail if tail else ("ALL" if head in _USER_DEFAULT_ALL else None)
        if market is None:
            raise ValueError(f"channel {head!r} requires a market: use {head}.<MARKET>")
        return channel, {"market": market}
    if head in _GLOBAL:
        return _GLOBAL[head], {}
    raise ValueError(f"unknown channel token: {token!r}")


# ── Event normalisation ───────────────────────────────────────────────────────


def normalise(channel_name: str, payload: dict) -> dict:
    """
    Map an SDK ws message → uniform event dict the dispatcher consumes.

    The SDK delivers `(ws_channel, message)` where message is the JSON body.
    We tag the event with a `type` token (matching `on:` strings in evaluators)
    and project the most relevant fields up to the top level so message
    templates can use them directly.
    """
    head = channel_name.split(".", 1)[0]
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}

    market = data.get("market") or _market_from_channel(channel_name)
    out: dict = {"_raw_channel": channel_name, "market": market}

    if head == "bbo":
        out["type"] = f"bbo.{market}" if market else "bbo"
        bid = _f(data.get("bid"))
        ask = _f(data.get("ask"))
        out["bid"] = bid
        out["ask"] = ask
        if bid is not None and ask is not None:
            out["mid"] = (bid + ask) / 2
    elif head == "trades":
        out["type"] = f"trades.{market}" if market else "trades"
        out["price"] = _f(data.get("price"))
        out["size"] = _f(data.get("size"))
        out["side"] = data.get("side")
    elif head == "funding_data":
        out["type"] = f"funding.{market}" if market else "funding"
        out["funding"] = _f(data.get("funding_rate"))
        out["funding_index"] = _f(data.get("funding_index"))
    elif head == "fills":
        out["type"] = "fills"
        out["side"] = data.get("side")
        out["size"] = _f(data.get("size"))
        out["price"] = _f(data.get("price"))
        out["fill_id"] = data.get("id") or data.get("fill_id")
        out["order_id"] = data.get("order_id")
    elif head == "orders":
        out["type"] = "orders"
        out["side"] = data.get("side")
        out["size"] = _f(data.get("size"))
        out["price"] = _f(data.get("price"))
        out["status"] = data.get("status")
        out["order_id"] = data.get("id") or data.get("order_id")
    elif head == "positions":
        out["type"] = "positions"
        out["side"] = data.get("side")
        out["size"] = _f(data.get("size"))
        out["entry_price"] = _f(data.get("average_entry_price"))
        out["unrealized_pnl"] = _f(data.get("unrealized_pnl"))
    elif head == "markets_summary":
        out["type"] = f"funding.{market}" if market else "markets_summary"
        out["funding"] = _f(data.get("funding_rate"))
        out["mark_price"] = _f(data.get("mark_price"))
    else:
        out["type"] = head

    return out


def _market_from_channel(channel_name: str) -> Optional[str]:
    parts = channel_name.split(".")
    return parts[1] if len(parts) >= 2 else None


def _f(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Source loop ───────────────────────────────────────────────────────────────


class WSSource:
    """
    Owns the SDK ws_client, manages connect / subscribe / reconnect.
    Pushes normalised events onto `out_queue`.
    """

    def __init__(self, paradex, channels: list[str], out_queue: asyncio.Queue):
        self.paradex = paradex
        self.channels = list(dict.fromkeys(channels))  # de-dup, preserve order
        self.out_queue = out_queue
        self._stop = asyncio.Event()
        self._gap_callback = None  # set by runner; called on reconnect
        self._connected_once = False

    def on_gap(self, fn) -> None:
        self._gap_callback = fn

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        delays = [1, 4, 16, 60]
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._connect_and_subscribe()
                attempt = 0
                # Block until disconnect / stop.
                while not self._stop.is_set():
                    await asyncio.sleep(1.0)
                    if not getattr(self.paradex.ws_client, "is_connected", lambda: True)():
                        log.warning(json.dumps({"event": "ws_disconnected"}))
                        break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(json.dumps({"event": "ws_error", "error": f"{type(e).__name__}: {e}"}))

            if self._stop.is_set():
                break
            delay = delays[min(attempt, len(delays) - 1)]
            log.info(json.dumps({"event": "ws_reconnect_wait", "seconds": delay}))
            await asyncio.sleep(delay)
            attempt += 1

    async def subscribe_one(self, token: str) -> bool:
        """
        Subscribe to a single additional channel at runtime (used by --watch).
        Returns True if newly subscribed, False if already in our channel set.
        """
        if token in self.channels:
            return False
        self.channels.append(token)
        try:
            channel, params = parse_token(token)
            await self.paradex.ws_client.subscribe(
                channel,
                callback=self._make_callback(token),
                params=params,
            )
            log.info(json.dumps({"event": "subscribed", "channel": token,
                                 "via": "watch"}))
            return True
        except Exception as e:
            log.error(json.dumps({"event": "subscribe_error",
                                  "channel": token,
                                  "error": f"{type(e).__name__}: {e}"}))
            self.channels.remove(token)
            return False

    async def _connect_and_subscribe(self) -> None:
        await self.paradex.ws_client.connect()
        log.info(json.dumps({"event": "ws_connected"}))
        # Only re-backfill on reconnects, not on first connect (the runner
        # has already backfilled before starting the source).
        if self._connected_once and self._gap_callback is not None:
            try:
                await self._gap_callback()
            except Exception as e:
                log.error(json.dumps({"event": "gap_callback_error", "error": str(e)}))
        self._connected_once = True

        for token in self.channels:
            channel, params = parse_token(token)
            await self.paradex.ws_client.subscribe(
                channel,
                callback=self._make_callback(token),
                params=params,
            )
            log.info(json.dumps({"event": "subscribed", "channel": token}))

    def _make_callback(self, token: str):
        async def on_message(ws_channel, message: dict) -> None:
            try:
                # ws_channel is the SDK enum; the resolved channel name is `token`
                event = normalise(token, message)
                event.setdefault("token", token)
                await self.out_queue.put(event)
            except Exception as e:
                log.error(json.dumps({
                    "event": "ws_callback_error",
                    "channel": token,
                    "error": f"{type(e).__name__}: {e}",
                }))
        return on_message
