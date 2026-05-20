// Strategy-viz JS library — pricing math + label helpers.
// Mirrors strategy_viz/pricing.py + specs.py + common.py for in-browser use.
//
// Import as ESM:
//   import { bsGreeks, payoffCurve, thesis } from "@paradex/strategy-viz";
//
// Stability: numeric outputs agree with the Python implementation to ≥3
// decimals (enforced by tests/test_parity.py).

export const SPOT = 100;
export const ASSUMED_IV = 0.60;
export const R = 0.05;

export const REASON_COLORS = {
  TP: "#16a34a", SL: "#dc2626", DTE: "#3b82f6",
  EXPIRY: "#8e44ad", MAX: "#f39c12", DTL: "#c0392b",
  IVP: "#16a085", REHEDGE: "#7f8c8d",
};

// ── numeric helpers ────────────────────────────────────────────────────
export function erf(x) {
  const s = Math.sign(x); x = Math.abs(x);
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741,
        a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const t = 1 / (1 + p * x);
  return s * (1 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-x * x));
}
export function nCdf(x) { return 0.5 * (1 + erf(x / Math.SQRT2)); }
export function nPdf(x) { return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI); }

export function bsPrice(S, K, T, sig, opt) {
  if (T <= 0) return opt === "CALL" ? Math.max(S - K, 0) : Math.max(K - S, 0);
  const d1 = (Math.log(S / K) + (R + 0.5 * sig * sig) * T) / (sig * Math.sqrt(T));
  const d2 = d1 - sig * Math.sqrt(T);
  return opt === "CALL"
    ? S * nCdf(d1) - K * Math.exp(-R * T) * nCdf(d2)
    : K * Math.exp(-R * T) * nCdf(-d2) - S * nCdf(-d1);
}

export function bsGreeks(S, K, T, sig, opt) {
  if (T <= 0 || sig <= 0) {
    const d = opt === "CALL" ? (S > K ? 1 : 0) : (S < K ? -1 : 0);
    return { delta: d, gamma: 0, vega: 0, theta: 0 };
  }
  const sq = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (R + 0.5 * sig * sig) * T) / (sig * sq);
  const d2 = d1 - sig * sq;
  const phi = nPdf(d1);
  const delta = opt === "CALL" ? nCdf(d1) : nCdf(d1) - 1;
  const gamma = phi / (S * sig * sq);
  const vega = S * phi * sq / 100;
  const thetaAnnual = opt === "CALL"
    ? -(S * phi * sig) / (2 * sq) - R * K * Math.exp(-R * T) * nCdf(d2)
    : -(S * phi * sig) / (2 * sq) + R * K * Math.exp(-R * T) * nCdf(-d2);
  return { delta, gamma, vega, theta: thetaAnnual / 365 };
}

export function strikeFromDelta(target, T, sig, opt) {
  let lo = SPOT * 0.2, hi = SPOT * 5, mid = SPOT;
  for (let i = 0; i < 80; i++) {
    mid = (lo + hi) / 2;
    let d;
    if (T <= 0) d = opt === "CALL" && SPOT > mid ? 1 : 0;
    else {
      const d1 = (Math.log(SPOT / mid) + (R + 0.5 * sig * sig) * T) / (sig * Math.sqrt(T));
      d = opt === "CALL" ? nCdf(d1) : nCdf(-d1);
    }
    if (d > target) (opt === "CALL" ? lo = mid : hi = mid);
    else            (opt === "CALL" ? hi = mid : lo = mid);
  }
  return mid;
}

// ── per-leg math ───────────────────────────────────────────────────────
export function legStrike(leg) {
  if (leg.type === "perp") return SPOT;
  const sm = leg.strikeMode ?? "delta";
  const p = leg.strikeParam ?? 0;
  const opt = leg.optionType ?? "CALL";
  const T = Math.max(leg.dteTarget ?? 14, 1) / 365;
  if (sm === "atm") return SPOT;
  if (sm === "otm_pct") return opt === "CALL" ? SPOT * (1 + p) : SPOT * (1 - p);
  return strikeFromDelta(p, T, ASSUMED_IV, opt);
}

export function legPremium(leg, K) {
  if (leg.type === "perp") return SPOT;
  const T = Math.max(leg.dteTarget ?? 14, 1) / 365;
  return bsPrice(SPOT, K, T, ASSUMED_IV, leg.optionType ?? "CALL");
}

export function legPayoffAt(leg, s, K, prem) {
  const sign = leg.side === "BUY" ? 1 : -1;
  const size = leg.size ?? 1;
  if (leg.type === "perp") return sign * size * (s - SPOT);
  const intrinsic = leg.optionType === "CALL"
    ? Math.max(s - K, 0) : Math.max(K - s, 0);
  return sign * size * (intrinsic - prem);
}

export function legPayoffVec(leg, spots) {
  const K = legStrike(leg), prem = legPremium(leg, K);
  return spots.map(s => legPayoffAt(leg, s, K, prem));
}

export function legGreeksAtEntry(leg) {
  const sign = leg.side === "BUY" ? 1 : -1;
  const size = leg.size ?? 1;
  if (leg.type === "perp") {
    return { delta: sign * size, gamma: 0, vega: 0, theta: 0, strike: SPOT };
  }
  const K = legStrike(leg);
  const T = Math.max(leg.dteTarget ?? 14, 1) / 365;
  const g = bsGreeks(SPOT, K, T, ASSUMED_IV, leg.optionType ?? "CALL");
  return {
    delta: sign * size * g.delta,
    gamma: sign * size * g.gamma,
    vega: sign * size * g.vega,
    theta: sign * size * g.theta,
    strike: K,
  };
}

export function portfolioGreeks(legs) {
  const acc = { delta: 0, gamma: 0, vega: 0, theta: 0 };
  for (const l of (legs || [])) {
    const g = legGreeksAtEntry(l);
    acc.delta += g.delta; acc.gamma += g.gamma;
    acc.vega += g.vega;   acc.theta += g.theta;
  }
  return acc;
}

export function payoffCurve(legs, nPoints = 60, lo = 0.55, hi = 1.45) {
  if (!legs?.length) return { spots: [], net: [] };
  const N = Math.max(nPoints, 2);
  const step = (hi - lo) / (N - 1);
  const spots = Array.from({ length: N }, (_, i) => SPOT * (lo + i * step));
  const net = new Array(N).fill(0);
  for (const leg of legs) {
    const K = legStrike(leg), prem = legPremium(leg, K);
    for (let i = 0; i < N; i++) net[i] += legPayoffAt(leg, spots[i], K, prem);
  }
  return { spots, net };
}

// ── text helpers (mirror specs.py / common.py) ─────────────────────────
export function hrs(h) { return h % 24 === 0 && h >= 24 ? `${h / 24}d` : `${h}h`; }

export function gateLabel(mode, gmin, n) {
  mode = (mode || "all").toLowerCase();
  if (mode === "all") return `ALL of ${n}`;
  if (mode === "any") return `ANY of ${n}`;
  if (mode === "min") return `≥${gmin} of ${n}`;
  return mode;
}

export function legLabel(leg) {
  if (leg.type === "perp") return `${leg.side} PERP`;
  const sm = leg.strikeMode ?? "delta", p = leg.strikeParam ?? 0;
  const strike = sm === "delta" ? `${p}Δ`
    : sm === "otm_pct" ? `${Math.round(p * 100)}% OTM`
    : "ATM";
  return `${leg.side} ${leg.optionType} · ${strike} · ${leg.dteTarget}d`;
}

export function entryRows(entry) {
  const e = entry || {};
  const r = [];
  if (e.rvPctile?.enabled)
    r.push(`RV pctile ${e.rvPctile.op} ${e.rvPctile.value} · ${hrs(e.rvPctile.window ?? 168)}`);
  if (e.ivPctile?.enabled)
    r.push(`IV pctile ${e.ivPctile.op} ${e.ivPctile.value} · ${hrs(e.ivPctile.window ?? 720)}`);
  if (e.rsi?.enabled)
    r.push(`RSI(14) ${e.rsi.op} ${e.rsi.value}`);
  if (e.sma?.enabled)
    r.push(`spot ${e.sma.op} SMA(${hrs(e.sma.period ?? 168)})`);
  if (e.fundingRate?.enabled)
    r.push(`funding ${e.fundingRate.op} ${e.fundingRate.value}/8h`);
  return r;
}

export function exitRows(exit_) {
  const x = exit_ || {};
  const r = [];
  if (x.profitTarget?.enabled) r.push(`profit ≥ ${x.profitTarget.value}% of premium`);
  if (x.stopLoss?.enabled)     r.push(`loss ≥ ${x.stopLoss.value}% of premium`);
  if (x.ivPctile?.enabled)     r.push(`IV pctile ${x.ivPctile.op} ${x.ivPctile.value} · ${hrs(x.ivPctile.window ?? 720)}`);
  if (x.dteFloor?.enabled)     r.push(`any leg DTE ≤ ${x.dteFloor.value}d`);
  if (x.maxHold?.enabled)      r.push(`held ≥ ${hrs(x.maxHold.value)}`);
  if (x.distToLiq?.enabled)    r.push(`liq within ${x.distToLiq.value}% of spot`);
  return r;
}

export function thesis(strat) {
  const legs = strat.legs || [];
  const nSell = legs.filter(l => l.side === "SELL").length;
  const nBuy  = legs.filter(l => l.side === "BUY").length;
  const nPerp = legs.filter(l => l.type === "perp").length;
  const e = strat.entry || {};
  const trig = [];
  if (e.ivPctile?.enabled) trig.push(e.ivPctile.op === ">" ? "elevated IV" : "low IV");
  if (e.rvPctile?.enabled) trig.push(e.rvPctile.op === "<" ? "compressed RV" : "expanded RV");
  if (e.rsi?.enabled)      trig.push(e.rsi.op === "<" ? "RSI oversold" : "RSI overbought");
  if (e.sma?.enabled)      trig.push("trend filter");
  const shape = [];
  if (nSell) shape.push(`sell ${nSell} option${nSell !== 1 ? "s" : ""}`);
  if (nBuy)  shape.push(`buy ${nBuy} option${nBuy !== 1 ? "s" : ""}`);
  if (nPerp) shape.push(`${nPerp} perp leg${nPerp !== 1 ? "s" : ""}`);
  return `${shape.join(", ")} · entry on ${trig.length ? trig.join(", ") : "no signal filter"}`;
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[c]));
}
