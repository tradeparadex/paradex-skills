"""
evaluator.py — gate logic + per-evaluator throttle/cooldown.

Mirrors _gate_passes from skills/strategy-backtester/scripts/paradex_backtest_engine.py
(line 294). Same all/any/min semantics, same condition-block keys, so a strategy
spec is portable between the two skills.

A live evaluator has two flavors:
  - conditions: indicator-driven (rsi/sma/rvPctile/ivPctile/fundingRate)
  - match:      raw event-shape match (fields like market/side/minSize)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from indicators import IndicatorState, percentile_rank


# ── Public API ────────────────────────────────────────────────────────────────


@dataclass
class EvaluatorState:
    """Per-evaluator runtime state — throttle / cooldown timestamps."""

    evaluator_id: str
    last_eval_ts: float = 0.0           # monotonic seconds
    last_fire_ts: float = 0.0           # monotonic seconds
    fire_count: int = 0
    eval_count: int = 0


@dataclass
class FireResult:
    """Outcome of a single evaluation."""

    fired: bool
    reason: str                         # "throttled" | "cooldown" | "match" | "no-match" | "missing-data"
    template_vars: dict[str, Any] = field(default_factory=dict)


def evaluate(
    evaluator: dict,
    state: EvaluatorState,
    event: dict,
    indicators: dict[str, IndicatorState],
    now: Optional[float] = None,
) -> FireResult:
    """
    Run an evaluator against one inbound event.

    evaluator    — the JSON evaluator block (id, on, throttle, cooldownAfterFire,
                   conditions or match, webhook)
    state        — EvaluatorState for this evaluator (mutated)
    event        — normalized inbound event {type, market?, ...payload}
    indicators   — per-market IndicatorState dict (only used for `conditions`)
    now          — override current time (tests)
    """
    now = now if now is not None else time.monotonic()

    throttle_s = _parse_duration(evaluator.get("throttle") or "0s")
    cooldown_s = _parse_duration(evaluator.get("cooldownAfterFire") or "0s")

    if throttle_s and (now - state.last_eval_ts) < throttle_s:
        return FireResult(False, "throttled")
    if cooldown_s and state.last_fire_ts and (now - state.last_fire_ts) < cooldown_s:
        return FireResult(False, "cooldown")

    state.last_eval_ts = now
    state.eval_count += 1

    if "match" in evaluator:
        return _eval_match(evaluator, state, event, now)
    if "expression" in evaluator:
        return _eval_expression(evaluator, state, event, indicators, now)
    if "conditions" in evaluator:
        return _eval_conditions(evaluator, state, event, indicators, now)
    return FireResult(False, "no-match")


# ── expression (DSL gate) ─────────────────────────────────────────────────────


def _eval_expression(
    evaluator: dict,
    state: EvaluatorState,
    event: dict,
    indicators: dict,
    now: float,
) -> FireResult:
    """
    Evaluate evaluator.expression — the JSON DSL form (see expression.py).
    Same fire / throttle semantics as _eval_conditions; the only difference
    is the gate is one tree instead of a flat AND/OR/MIN over named keys.
    """
    from expression import evaluate as eval_expr
    market = _market_from_event(event)
    ind = indicators.get(market) if market else None

    template_vars: dict[str, Any] = {}
    result = eval_expr(evaluator["expression"], ind, event, template_vars)

    if result is None:
        return FireResult(False, "missing-data", template_vars)
    if not result:
        return FireResult(False, "no-match", template_vars)

    state.last_fire_ts = now
    state.fire_count += 1
    for k in ("close", "open", "high", "low", "volume", "bid", "ask",
              "mid", "price", "size", "side", "market"):
        if k in event:
            template_vars.setdefault(k, event[k])
    return FireResult(True, "match", template_vars)


# ── conditions (indicator gate) ───────────────────────────────────────────────


def _eval_conditions(
    evaluator: dict,
    state: EvaluatorState,
    event: dict,
    indicators: dict[str, IndicatorState],
    now: float,
) -> FireResult:
    cond = evaluator["conditions"]
    market = _market_from_event(event)
    ind = indicators.get(market) if market else None

    # Pull current values from the rolling state. Missing data = skip the
    # gate (rather than fire on stale assumptions).
    template_vars: dict[str, Any] = {}
    gate_results: list[bool] = []
    missing: list[str] = []

    rsi_cfg = cond.get("rsi") or {}
    if rsi_cfg.get("enabled"):
        rsi_val = ind.rsi() if ind else None
        template_vars["rsi"] = rsi_val
        if rsi_val is None:
            missing.append("rsi")
        else:
            gate_results.append(_apply_op(rsi_val, rsi_cfg.get("op"), float(rsi_cfg["value"])))

    sma_cfg = cond.get("sma") or {}
    if sma_cfg.get("enabled"):
        period = int(sma_cfg.get("period") or 24)
        sma_val = ind.sma(period) if ind else None
        template_vars["sma"] = sma_val
        spot = event.get("close") or event.get("mid") or event.get("price")
        if sma_val is None or spot is None:
            missing.append("sma")
        else:
            op = sma_cfg.get("op") or "above"
            gate_results.append(spot > sma_val if op == "above" else spot < sma_val)

    rv_cfg = cond.get("rvPctile") or {}
    if rv_cfg.get("enabled"):
        win = int(rv_cfg.get("window") or 168)
        rv_pct = ind.realized_vol_pctile(0, win) if ind else None
        template_vars["rv_pctile"] = rv_pct
        if rv_pct is None:
            missing.append("rvPctile")
        else:
            gate_results.append(_apply_op(rv_pct, rv_cfg.get("op"), float(rv_cfg["value"])))

    iv_cfg = cond.get("ivPctile") or {}
    if iv_cfg.get("enabled"):
        # IV percentile is fed in via event["iv_pctile"] from the poll/IV source;
        # listener does not WS-subscribe to option marks.
        iv_pct = event.get("iv_pctile")
        template_vars["iv_pctile"] = iv_pct
        if iv_pct is None:
            missing.append("ivPctile")
        else:
            gate_results.append(_apply_op(iv_pct, iv_cfg.get("op"), float(iv_cfg["value"])))

    fr_cfg = cond.get("fundingRate") or {}
    if fr_cfg.get("enabled"):
        fr_pct = ind.funding_8h_pct() if ind else None
        template_vars["funding"] = (fr_pct / 100.0) if fr_pct is not None else None
        if fr_pct is None:
            missing.append("fundingRate")
        else:
            gate_results.append(_apply_op(fr_pct, fr_cfg.get("op"), float(fr_cfg["value"])))

    if missing and not gate_results:
        return FireResult(False, "missing-data", template_vars)

    fired = _gate_passes(
        gate_results,
        cond.get("gateMode") or "all",
        int(cond.get("gateMin") or 1),
    )
    if fired:
        state.last_fire_ts = now
        state.fire_count += 1
        # Carry through event fields for the message template
        for k in ("close", "open", "high", "low", "volume", "bid", "ask",
                  "mid", "price", "size", "side", "market"):
            if k in event:
                template_vars.setdefault(k, event[k])
        return FireResult(True, "match", template_vars)
    return FireResult(False, "no-match", template_vars)


# ── match (raw event) ─────────────────────────────────────────────────────────


def _eval_match(
    evaluator: dict,
    state: EvaluatorState,
    event: dict,
    now: float,
) -> FireResult:
    rules = evaluator["match"]
    for key, expected in rules.items():
        if key == "minSize":
            size = float(event.get("size") or 0)
            if size < float(expected):
                return FireResult(False, "no-match")
        elif key == "minNotionalUsd":
            size = float(event.get("size") or 0)
            price = float(event.get("price") or 0)
            if size * price < float(expected):
                return FireResult(False, "no-match")
        else:
            if event.get(key) != expected:
                return FireResult(False, "no-match")
    state.last_fire_ts = now
    state.fire_count += 1
    template_vars: dict[str, Any] = {
        k: v for k, v in event.items()
        if k in ("market", "side", "size", "price", "status",
                 "order_id", "fill_id", "notional", "unrealized_pnl",
                 "entry_price")
    }
    if "size" in template_vars and "price" in template_vars:
        template_vars["notional"] = float(template_vars["size"]) * float(template_vars["price"])
    return FireResult(True, "match", template_vars)


# ── gate primitive (mirrors backtester) ───────────────────────────────────────


def _gate_passes(results: list[bool], mode: str, gate_min: int = 1) -> bool:
    """all / any / min — empty results always pass."""
    if not results:
        return True
    count = sum(results)
    if mode == "any":
        return count >= 1
    if mode == "min":
        return count >= min(max(1, gate_min), len(results))
    return count == len(results)  # "all"


def _apply_op(value: float, op: Optional[str], threshold: float) -> bool:
    if op == ">":
        return value > threshold
    if op == "<":
        return value < threshold
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    if op == "==":
        return value == threshold
    return False


# ── duration parsing ──────────────────────────────────────────────────────────


_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd])\s*$", re.IGNORECASE)


def _parse_duration(s: str) -> float:
    """Parse "30s", "5m", "1h", "1d" → seconds. "0s" / "0" → 0."""
    if not s or s == "0" or s == "0s":
        return 0.0
    m = _DURATION_RE.match(str(s))
    if not m:
        raise ValueError(f"invalid duration: {s!r}")
    n = float(m.group(1))
    unit = m.group(2).lower()
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _market_from_event(event: dict) -> Optional[str]:
    if "market" in event:
        return event["market"]
    return None
