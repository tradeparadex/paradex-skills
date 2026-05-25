"""Convert a Paradex strategy JSON (backtester or listener form) to a mermaid flowchart.

Public API:
    backtester_to_mermaid(strat) -> (mmd_source, name)
    listener_to_mermaid(strat)   -> (mmd_source, name)
    convert(strat)               -> dispatches on strategy shape
"""
from __future__ import annotations

import json
from typing import Any

from .common import hrs as _hrs, gate_label as _gate_label


def _entry_conditions(entry: dict[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    rv = entry.get("rvPctile", {})
    if rv.get("enabled"):
        rows.append(("rvPctile", f"RV pctile {rv['op']} {rv['value']} · {_hrs(rv.get('window', 168))}"))
    iv = entry.get("ivPctile", {})
    if iv.get("enabled"):
        rows.append(("ivPctile", f"IV pctile {iv['op']} {iv['value']} · {_hrs(iv.get('window', 720))}"))
    rsi = entry.get("rsi", {})
    if rsi.get("enabled"):
        rows.append(("rsi", f"RSI(14) {rsi['op']} {rsi['value']}"))
    sma = entry.get("sma", {})
    if sma.get("enabled"):
        rows.append(("sma", f"spot {sma['op']} SMA({_hrs(sma.get('period', 168))})"))
    fr = entry.get("fundingRate", {})
    if fr.get("enabled"):
        rows.append(("funding", f"funding {fr['op']} {fr['value']}/8h"))
    return rows


def _exit_conditions(exit_: dict[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    pt = exit_.get("profitTarget", {})
    if pt.get("enabled"):
        rows.append(("profitTarget", f"profit ≥ {pt['value']}% of premium"))
    sl = exit_.get("stopLoss", {})
    if sl.get("enabled"):
        rows.append(("stopLoss", f"loss ≥ {sl['value']}% of premium"))
    iv = exit_.get("ivPctile", {})
    if iv.get("enabled"):
        rows.append(("ivPctile", f"IV pctile {iv['op']} {iv['value']} · {_hrs(iv.get('window', 720))}"))
    df = exit_.get("dteFloor", {})
    if df.get("enabled"):
        rows.append(("dteFloor", f"any leg DTE ≤ {df['value']}d"))
    mh = exit_.get("maxHold", {})
    if mh.get("enabled"):
        rows.append(("maxHold", f"held ≥ {_hrs(mh['value'])}"))
    dl = exit_.get("distToLiq", {})
    if dl.get("enabled"):
        rows.append(("distToLiq", f"liq within {dl['value']}% of spot"))
    return rows


def _leg_label(leg: dict[str, Any]) -> str:
    t = leg["type"]
    side = leg["side"]
    size = leg["size"]
    sm = leg.get("sizeMode", "contracts")
    size_str = f"{size}×" if sm == "contracts" else f"{size:.0%} cap"
    if t == "perp":
        return f"{side} PERP · {size_str}"
    ot = leg.get("optionType", "?")
    mode = leg.get("strikeMode", "delta")
    param = leg.get("strikeParam", 0)
    if mode == "delta":
        strike = f"{param:g}Δ"
    elif mode == "atm":
        strike = "ATM"
    elif mode == "otm_pct":
        strike = f"{param:.0%} OTM"
    else:
        strike = f"{mode}({param})"
    dte = leg.get("dteTarget", "?")
    return f"{side} {ot} · {strike} · {dte}d · {size_str}"


def _sanitize(s: str) -> str:
    return s.replace('"', "'").replace("\n", " ")


def backtester_to_mermaid(strat: dict[str, Any]) -> str:
    name = strat.get("name", "strategy")
    underlying = strat.get("underlying", "?")
    capital = strat.get("capital", 0)
    margin = strat.get("marginMode", "XM")
    entry = strat.get("entry", {})
    exit_ = strat.get("exit", {})
    legs = strat.get("legs", [])
    hedge = strat.get("deltaHedge", {})
    freq = entry.get("frequency", 168)

    entry_rows = _entry_conditions(entry)
    exit_rows = _exit_conditions(exit_)
    e_gate = _gate_label(entry.get("gateMode", "all"), entry.get("gateMin", 1), len(entry_rows))
    x_gate = _gate_label(exit_.get("gateMode", "any"), exit_.get("gateMin", 1), len(exit_rows))

    lines: list[str] = []
    lines.append("flowchart TD")
    lines.append(f'  Start(["⟳ Cycle: every {_hrs(freq)}<br/>{underlying} · ${capital:,.0f} · {margin}"])')
    lines.append("")

    # Entry gate
    if entry_rows:
        lines.append(f'  EGate{{"Entry gate<br/>{e_gate}"}}')
        lines.append("  Start --> EGate")
        for i, (key, label) in enumerate(entry_rows):
            node = f"E{i}"
            lines.append(f'  {node}["{_sanitize(label)}"]')
            lines.append(f"  EGate --> {node}")
        lines.append("  EGate -->|pass| Open")
        lines.append("  EGate -.->|fail| Skip([\"⤺ skip cycle\"])")
        lines.append("  Skip -.-> Start")
    else:
        lines.append('  EGate["Entry gate: always pass"]')
        lines.append("  Start --> EGate --> Open")

    # Legs
    lines.append("")
    lines.append('  Open["📂 Open positions"]')
    for i, leg in enumerate(legs):
        node = f"L{i}"
        lines.append(f'  {node}["{_sanitize(_leg_label(leg))}"]')
        lines.append(f"  Open --> {node}")
        lines.append(f"  {node} --> Hold")

    # Hold + hedge
    lines.append("")
    lines.append('  Hold[["⏱ Hold position"]]')
    if hedge.get("enabled"):
        band = hedge.get("band", 0.1)
        lines.append(f'  Hedge["Δ-hedge perp<br/>band {band:g}"]')
        lines.append(f"  Hold -->|Δ drift > band| Hedge")
        lines.append("  Hedge --> Hold")

    # Exit gate
    lines.append("")
    if exit_rows:
        lines.append(f'  XGate{{"Exit gate<br/>{x_gate}"}}')
        lines.append("  Hold --> XGate")
        for i, (key, label) in enumerate(exit_rows):
            node = f"X{i}"
            lines.append(f'  {node}["{_sanitize(label)}"]')
            lines.append(f"  XGate --> {node}")
        lines.append("  XGate -->|trigger| Close")
        lines.append("  XGate -.->|none| Hold")
    else:
        lines.append("  Hold --> Close")

    # EXPIRY override
    has_option = any(l.get("type") == "option" for l in legs)
    if has_option:
        lines.append('  Expiry["DTE = 0 (EXPIRY override)"]')
        lines.append("  Hold -. any leg expires .-> Expiry")
        lines.append("  Expiry --> Close")

    lines.append('  Close(["💰 Close all positions"])')
    lines.append("  Close --> Start")

    # Styling
    lines.append("")
    lines.append("  classDef gate fill:#fff3cd,stroke:#f0ad4e,color:#222")
    lines.append("  classDef leg fill:#e7f3ff,stroke:#3186c4,color:#222")
    lines.append("  classDef state fill:#e9f7ef,stroke:#28a745,color:#222")
    lines.append("  classDef warn  fill:#fde2e2,stroke:#c0392b,color:#222")
    lines.append("  class EGate,XGate gate")
    if legs:
        lines.append("  class " + ",".join(f"L{i}" for i in range(len(legs))) + " leg")
    lines.append("  class Start,Open,Hold,Close state")
    if has_option:
        lines.append("  class Expiry warn")
    return "\n".join(lines), name


# --------------------- listener-form -------------------------


def _operand_label(op: dict[str, Any]) -> str:
    if "const" in op:
        return f"const {op['const']}"
    if "indicator" in op:
        parts = [op["indicator"]]
        for k in ("period", "window", "length"):
            if k in op:
                parts.append(f"{k}={op[k]}")
        return "·".join(parts)
    if "event" in op:
        return f"event {op['event']}"
    if "var" in op:
        return f"var {op['var']}"
    return json.dumps(op)


def _expr_nodes(expr: dict[str, Any], counter: list[int], lines: list[str],
                parent: str | None, prefix: str = "N") -> str:
    """Render an expression tree node and return the node id.
    `prefix` is the literal node-id prefix (e.g. "E0N" for evaluator 0) — it is
    NOT applied via string replace, so it never touches label text."""
    idx = counter[0]
    counter[0] += 1
    if "all" in expr or "any" in expr:
        op = "all" if "all" in expr else "any"
        nid = f"{prefix}{idx}"
        lines.append(f'  {nid}{{"{op.upper()}"}}')
        if parent is not None:
            lines.append(f"  {parent} --> {nid}")
        for child in expr[op]:
            _expr_nodes(child, counter, lines, nid, prefix)
        return nid
    if "not" in expr:
        nid = f"{prefix}{idx}"
        lines.append(f'  {nid}{{"NOT"}}')
        if parent is not None:
            lines.append(f"  {parent} --> {nid}")
        _expr_nodes(expr["not"], counter, lines, nid, prefix)
        return nid
    if "op" in expr:
        nid = f"{prefix}{idx}"
        lhs = _operand_label(expr.get("lhs", {}))
        rhs = _operand_label(expr.get("rhs", {}))
        label = f"{lhs} {expr['op']} {rhs}"
        lines.append(f'  {nid}["{_sanitize(label)}"]')
        if parent is not None:
            lines.append(f"  {parent} --> {nid}")
        return nid
    nid = f"{prefix}{idx}"
    lines.append(f'  {nid}["{_sanitize(json.dumps(expr))}"]')
    if parent is not None:
        lines.append(f"  {parent} --> {nid}")
    return nid


def listener_to_mermaid(strat: dict[str, Any]) -> tuple[str, str]:
    name = strat.get("name", "listener")
    underlying = strat.get("underlying", "?")
    bar = strat.get("barSize", "?")
    mode = strat.get("dataMode", "?")
    subs = strat.get("subscriptions", {}).get("market", [])
    evaluators = strat.get("evaluators", [])

    lines: list[str] = ["flowchart TD"]
    lines.append(
        f'  Feed(["📡 {underlying} feed · bar {bar} · {mode}<br/>{", ".join(subs)}"])'
    )
    lines.append("")

    for ei, ev in enumerate(evaluators):
        eid = f"EV{ei}"
        on = ", ".join(ev.get("on", []))
        throttle = ev.get("throttle", "—")
        cooldown = ev.get("cooldownAfterFire", "—")
        lines.append(
            f'  {eid}["Evaluator: {ev.get("id", eid)}<br/>on {on}<br/>throttle {throttle} · cooldown {cooldown}"]'
        )
        lines.append(f"  Feed --> {eid}")

        if "expression" in ev:
            counter = [0]
            prefix = f"E{ei}N"
            root = _expr_nodes(ev["expression"], counter, lines, parent=None, prefix=prefix)
            lines.append(f"  {eid} --> {root}")
            decision_src = root
        else:
            conds = ev.get("conditions", {})
            mode_ = conds.get("gateMode", "all").upper()
            keys = [k for k in ("rsi", "rvPctile", "ivPctile", "sma", "fundingRate")
                    if k in conds and conds[k].get("enabled")]
            gnode = f"G{ei}"
            lines.append(f'  {gnode}{{"{mode_} of {len(keys)}"}}')
            lines.append(f"  {eid} --> {gnode}")
            for ki, k in enumerate(keys):
                c = conds[k]
                if k == "sma":
                    label = f"spot {c['op']} SMA({_hrs(c.get('period', 168))})"
                else:
                    label = f"{k} {c['op']} {c['value']}"
                cn = f"C{ei}_{ki}"
                lines.append(f'  {cn}["{_sanitize(label)}"]')
                lines.append(f"  {gnode} --> {cn}")
            decision_src = gnode

        wh = ev.get("webhook", {})
        whn = f"WH{ei}"
        url = wh.get("url", "?")
        msg = wh.get("messageTemplate", "")
        lines.append(f'  {whn}[/"🪝 webhook<br/>{_sanitize(url)}<br/>{_sanitize(msg)}"/]')
        lines.append(f"  {decision_src} -->|fire| {whn}")

    lines.append("")
    lines.append("  classDef feed fill:#e9f7ef,stroke:#28a745")
    lines.append("  classDef ev   fill:#e7f3ff,stroke:#3186c4")
    lines.append("  classDef hook fill:#fff3cd,stroke:#f0ad4e")
    lines.append("  class Feed feed")
    if evaluators:
        lines.append("  class " + ",".join(f"EV{i}" for i in range(len(evaluators))) + " ev")
        lines.append("  class " + ",".join(f"WH{i}" for i in range(len(evaluators))) + " hook")

    return "\n".join(lines), name


def convert(strat: dict[str, Any]) -> tuple[str, str]:
    if "evaluators" in strat:
        return listener_to_mermaid(strat)
    return backtester_to_mermaid(strat)

