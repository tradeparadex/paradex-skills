"""
conditions.py — single source of truth for indicator + event-field lookups.

Every supported leaf in the expression DSL (and every condition key in the
legacy `conditions` block) routes through this registry. New conditions land
in one place and are immediately usable by:
  - the legacy {"rsi": {...}, "sma": {...}, ...} grammar in evaluator.py
  - the new JSON-expression grammar in expression.py
  - the strategy validator (so unknown indicators fail at load time)
  - the agent-facing --check command (catalog discovery)

Math lives in indicators.py (rolling state) and is read here without
re-implementation. Adding a new indicator is one INDICATORS entry plus a
matching method on IndicatorState if the math isn't already there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ── Type aliases ──────────────────────────────────────────────────────────────


# Read state + event → numeric value (or None if data is missing)
Reader = Callable[["IndicatorState | None", dict, dict], Optional[float]]


@dataclass
class IndicatorDef:
    """Metadata for an indicator usable from JSON."""
    name: str
    args: dict[str, Any] = field(default_factory=dict)   # arg_name → default
    required: list[str] = field(default_factory=list)    # arg_name list (no default)
    read: Reader = lambda s, e, a: None
    description: str = ""

    def coerce(self, raw_args: dict) -> dict:
        """Fill defaults; reject unknown keys; require `required`."""
        out: dict[str, Any] = {}
        for k in self.required:
            if k not in raw_args:
                raise ValueError(f"indicator {self.name!r} missing required arg {k!r}")
            out[k] = raw_args[k]
        for k, default in self.args.items():
            out[k] = raw_args.get(k, default)
        unknown = set(raw_args) - set(self.required) - set(self.args)
        if unknown:
            raise ValueError(
                f"indicator {self.name!r} got unknown args {sorted(unknown)}; "
                f"expected {sorted(set(self.required) | set(self.args))}"
            )
        return out


# ── Registry ──────────────────────────────────────────────────────────────────


def _rsi(state, event, args):
    return state.rsi(int(args["period"])) if state else None


def _sma(state, event, args):
    return state.sma(int(args["period"])) if state else None


def _rv_pctile(state, event, args):
    return state.realized_vol_pctile(0, int(args["window"])) if state else None


def _funding_rate(state, event, args):
    """8h funding rate as decimal (0.01 = 1%) — the way users specify thresholds."""
    if state is None:
        return None
    pct = state.funding_8h_pct()
    return None if pct is None else pct / 100.0


def _funding_pct(state, event, args):
    """Same as funding_rate but expressed as percent (1.0 = 1%) for legacy schema."""
    return state.funding_8h_pct() if state else None


def _iv_pctile(state, event, args):
    """IV percentile is delivered through the event payload (REST poll)."""
    return event.get("iv_pctile")


INDICATORS: dict[str, IndicatorDef] = {
    "rsi": IndicatorDef(
        name="rsi",
        args={"period": 14},
        read=_rsi,
        description="Wilder's RSI on rolling closes. Returns 0..100.",
    ),
    "sma": IndicatorDef(
        name="sma",
        required=["period"],
        read=_sma,
        description="Simple moving average over the last `period` bars.",
    ),
    "rvPctile": IndicatorDef(
        name="rvPctile",
        args={"window": 168},
        read=_rv_pctile,
        description="Percentile rank of current realized vol vs `window` bars. 0..100.",
    ),
    "fundingRate": IndicatorDef(
        name="fundingRate",
        read=_funding_rate,
        description="Last 8h funding rate as decimal (0.01 = 1%).",
    ),
    "fundingPct": IndicatorDef(
        name="fundingPct",
        read=_funding_pct,
        description="Last 8h funding rate as percent (1.0 = 1%). Legacy alias.",
    ),
    "ivPctile": IndicatorDef(
        name="ivPctile",
        args={"window": 720},
        read=_iv_pctile,
        description="Percentile rank of current ATM IV vs `window` bars (event-supplied).",
    ),
}


# ── Event-field accessors ─────────────────────────────────────────────────────


# Map agent-friendly names → event-payload keys.
EVENT_FIELDS: dict[str, str] = {
    "close":  "close",
    "open":   "open",
    "high":   "high",
    "low":    "low",
    "volume": "volume",
    "bid":    "bid",
    "ask":    "ask",
    "mid":    "mid",
    "price":  "price",
    "size":   "size",
}


def read_event_field(name: str, event: dict) -> Optional[float]:
    key = EVENT_FIELDS.get(name)
    if key is None:
        return None
    v = event.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Public catalog (agents call this to discover what they can use) ──────────


def catalog() -> dict:
    """Machine-readable list of supported indicators + event fields. The
    --check command emits this so an agent can self-correct before submit."""
    return {
        "indicators": {
            name: {
                "args": {k: d for k, d in ind.args.items()},
                "required": list(ind.required),
                "description": ind.description,
            }
            for name, ind in INDICATORS.items()
        },
        "event_fields": list(EVENT_FIELDS),
        "operators": [">", "<", ">=", "<=", "==", "!=", "above", "below"],
        "combinators": ["all", "any", "not"],
    }
