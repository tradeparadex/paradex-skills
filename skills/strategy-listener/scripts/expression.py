"""
expression.py — JSON expression DSL for agent-generated conditions.

Goals (in order):
  1. Reliable for an LLM to generate. Strict shape, named keys, no positional.
  2. Validatable before execution. Every error tells the agent exactly what
     to fix.
  3. Robust at runtime. Missing data → None propagates up; never raises.
  4. Trivial to extend by editing conditions.py only.

Grammar (one of the following dicts at every node):

  bool nodes:
    {"op": OP, "lhs": <num>, "rhs": <num>}        OP ∈ {<, >, <=, >=, ==, !=, above, below}
    {"all": [<bool>, <bool>, ...]}                AND, short-circuits on False
    {"any": [<bool>, <bool>, ...]}                OR,  short-circuits on True
    {"not": <bool>}                               negation

  num nodes:
    {"const": NUMBER}
    {"event": FIELD}                              FIELD ∈ EVENT_FIELDS
    {"indicator": NAME, ...args}                  NAME ∈ INDICATORS

The root must be a bool node. Comparisons coerce both sides to float; missing
data on either side yields None and the surrounding bool combinator treats
None as "unknown" (same semantics as the legacy 'missing-data' reason).

Equivalences with the legacy `conditions` form (so users / agents can pick
either):

  legacy                            expression
  ─────────────────────────────────────────────────────────────────────────
  rsi {op: <, value: 30}            {op: <, lhs: {indicator: rsi}, rhs: {const: 30}}
  sma {op: above, period: 24}       {op: above, lhs: {event: close}, rhs: {indicator: sma, period: 24}}
  fundingRate {op: >, value: 0.5}   {op: >, lhs: {indicator: fundingPct}, rhs: {const: 0.5}}
  gateMode: all + N conditions      {all: [<comparison>, ...]}
  gateMode: any + N conditions      {any: [<comparison>, ...]}

Any condition expressible in the legacy grammar can be expressed here. The
reverse is not true (you can compare two indicators, build deeper trees,
mix all/any/not).
"""

from __future__ import annotations

from typing import Any, Optional

from conditions import INDICATORS, EVENT_FIELDS, read_event_field


_COMPARE_OPS = {"<", ">", "<=", ">=", "==", "!=", "above", "below"}
_COMBINATORS = {"all", "any", "not"}


# ── Validation ────────────────────────────────────────────────────────────────


class ExpressionError(ValueError):
    """Raised by validate() with a complete list of issues."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate(expr: Any, *, root: bool = True, path: str = "$") -> list[str]:
    """
    Returns a list of human-readable error strings. Empty list = valid.

    `root=True` enforces that the top-level node yields a bool. Inner calls
    use `root=False` so num leaves can validate as themselves.
    """
    errors: list[str] = []
    _validate(expr, expected="bool" if root else "any", path=path, errors=errors)
    return errors


def validate_or_raise(expr: Any) -> None:
    errs = validate(expr)
    if errs:
        raise ExpressionError(errs)


def _validate(node: Any, *, expected: str, path: str, errors: list[str]) -> None:
    if not isinstance(node, dict):
        errors.append(f"{path}: expected JSON object, got {type(node).__name__}")
        return
    keys = set(node.keys())

    # Combinators
    if "all" in keys or "any" in keys:
        which = "all" if "all" in keys else "any"
        if expected == "num":
            errors.append(f"{path}: '{which}' yields bool, expected number")
        children = node[which]
        if not isinstance(children, list) or not children:
            errors.append(f"{path}.{which}: must be a non-empty array")
        else:
            for i, child in enumerate(children):
                _validate(child, expected="bool",
                          path=f"{path}.{which}[{i}]", errors=errors)
        _check_extra_keys(node, allowed={which}, path=path, errors=errors)
        return

    if "not" in keys:
        if expected == "num":
            errors.append(f"{path}: 'not' yields bool, expected number")
        _validate(node["not"], expected="bool",
                  path=f"{path}.not", errors=errors)
        _check_extra_keys(node, allowed={"not"}, path=path, errors=errors)
        return

    # Comparison
    if "op" in keys:
        if expected == "num":
            errors.append(f"{path}: comparison yields bool, expected number")
        op = node.get("op")
        if op not in _COMPARE_OPS:
            errors.append(f"{path}.op: unknown operator {op!r}; "
                          f"expected one of {sorted(_COMPARE_OPS)}")
        if "lhs" not in node or "rhs" not in node:
            errors.append(f"{path}: comparison requires 'lhs' and 'rhs'")
        else:
            _validate(node["lhs"], expected="num",
                      path=f"{path}.lhs", errors=errors)
            _validate(node["rhs"], expected="num",
                      path=f"{path}.rhs", errors=errors)
        _check_extra_keys(node, allowed={"op", "lhs", "rhs"},
                          path=path, errors=errors)
        return

    # Numeric leaves
    if "const" in keys:
        if expected == "bool":
            errors.append(f"{path}: 'const' yields number, expected bool")
        v = node["const"]
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            errors.append(f"{path}.const: must be a number, got {type(v).__name__}")
        _check_extra_keys(node, allowed={"const"}, path=path, errors=errors)
        return

    if "event" in keys:
        if expected == "bool":
            errors.append(f"{path}: 'event' yields number, expected bool")
        f = node["event"]
        if f not in EVENT_FIELDS:
            errors.append(f"{path}.event: unknown field {f!r}; "
                          f"expected one of {sorted(EVENT_FIELDS)}")
        _check_extra_keys(node, allowed={"event"}, path=path, errors=errors)
        return

    if "indicator" in keys:
        if expected == "bool":
            errors.append(f"{path}: 'indicator' yields number, expected bool")
        name = node["indicator"]
        ind = INDICATORS.get(name)
        if ind is None:
            errors.append(f"{path}.indicator: unknown indicator {name!r}; "
                          f"expected one of {sorted(INDICATORS)}")
        else:
            args = {k: v for k, v in node.items() if k != "indicator"}
            try:
                ind.coerce(args)
            except ValueError as e:
                errors.append(f"{path}: {e}")
        return

    errors.append(
        f"{path}: object has no recognized key. "
        f"Use one of: op, all, any, not, const, event, indicator. "
        f"Got keys: {sorted(keys)}"
    )


def _check_extra_keys(node: dict, *, allowed: set, path: str,
                      errors: list[str]) -> None:
    extra = set(node.keys()) - allowed
    if extra:
        errors.append(f"{path}: unexpected keys {sorted(extra)}; "
                      f"this node accepts only {sorted(allowed)}")


# ── Evaluation ────────────────────────────────────────────────────────────────


def evaluate(expr: dict, state, event: dict,
             template_vars: Optional[dict] = None) -> Optional[bool]:
    """
    Evaluate a (validated) expression. Returns:
      True/False  — the gate decision
      None        — a leaf was missing (e.g. RSI needs more bars)

    `template_vars` is mutated to record indicator/event values seen on the
    way down — handy for message templates ('rsi=29.4' even if the gate
    didn't fire because of another condition).
    """
    if template_vars is None:
        template_vars = {}
    return _eval_bool(expr, state, event, template_vars)


def _eval_bool(node: dict, state, event: dict, vars: dict) -> Optional[bool]:
    if "all" in node:
        seen_unknown = False
        for child in node["all"]:
            r = _eval_bool(child, state, event, vars)
            if r is False:
                return False
            if r is None:
                seen_unknown = True
        return None if seen_unknown else True
    if "any" in node:
        seen_unknown = False
        for child in node["any"]:
            r = _eval_bool(child, state, event, vars)
            if r is True:
                return True
            if r is None:
                seen_unknown = True
        return None if seen_unknown else False
    if "not" in node:
        r = _eval_bool(node["not"], state, event, vars)
        return None if r is None else (not r)
    if "op" in node:
        return _eval_compare(node, state, event, vars)
    raise RuntimeError(f"non-bool node reached _eval_bool: keys={list(node)}")


def _eval_compare(node: dict, state, event: dict,
                  vars: dict) -> Optional[bool]:
    lhs = _eval_num(node["lhs"], state, event, vars)
    rhs = _eval_num(node["rhs"], state, event, vars)
    if lhs is None or rhs is None:
        return None
    op = node["op"]
    if op in (">", "above"):
        return lhs > rhs
    if op in ("<", "below"):
        return lhs < rhs
    if op == ">=":
        return lhs >= rhs
    if op == "<=":
        return lhs <= rhs
    if op == "==":
        return lhs == rhs
    if op == "!=":
        return lhs != rhs
    raise RuntimeError(f"unreachable op: {op!r}")


def _eval_num(node: dict, state, event: dict, vars: dict) -> Optional[float]:
    if "const" in node:
        return float(node["const"])
    if "event" in node:
        v = read_event_field(node["event"], event)
        vars.setdefault(node["event"], v)
        return v
    if "indicator" in node:
        ind = INDICATORS[node["indicator"]]
        args = ind.coerce({k: v for k, v in node.items() if k != "indicator"})
        v = ind.read(state, event, args)
        # Record under the indicator name plus an args-suffix when non-default
        label = node["indicator"]
        if args and any(args.get(k) != ind.args.get(k) for k in args):
            label = f"{label}_{'_'.join(f'{k}{args[k]}' for k in sorted(args))}"
        vars.setdefault(label, v)
        return v
    raise RuntimeError(f"non-num node reached _eval_num: keys={list(node)}")


# ── Smoke-eval (used by --check) ─────────────────────────────────────────────


def required_window(expr: dict) -> int:
    """
    Walk an expression tree and return the largest indicator window/period
    referenced. Used by the runner to size the indicator backfill.
    """
    out = 0
    _collect_windows(expr, out_list := [])
    return max(out_list, default=0)


def _collect_windows(node: Any, out_list: list[int]) -> None:
    if not isinstance(node, dict):
        return
    if "indicator" in node:
        ind = INDICATORS.get(node["indicator"])
        if ind is not None:
            try:
                args = ind.coerce({k: v for k, v in node.items()
                                   if k != "indicator"})
                # convention: 'window' or 'period' is the look-back size
                for k in ("window", "period"):
                    if k in args:
                        out_list.append(int(args[k]))
            except ValueError:
                pass
        return
    for key in ("all", "any"):
        if key in node:
            for child in node[key]:
                _collect_windows(child, out_list)
            return
    if "not" in node:
        _collect_windows(node["not"], out_list)
        return
    if "op" in node:
        _collect_windows(node.get("lhs"), out_list)
        _collect_windows(node.get("rhs"), out_list)


def smoke_eval(expr: dict) -> Optional[bool]:
    """
    Evaluate the expression against a synthetic state with all indicators
    at None and an event with all fields at zero. The point isn't the
    boolean result — it's that evaluation completes without raising. Any
    runtime error here means the validator missed a structural problem.
    """
    from indicators import IndicatorState
    state = IndicatorState(market="SMOKE", max_window=10)
    state.seed_closes([100.0] * 5)
    state.update_funding(0.0)
    event = {f: 0.0 for f in EVENT_FIELDS}
    event["type"] = "smoke"
    return evaluate(expr, state, event, {})
