// Parity harness: reads JSON from stdin describing per-strategy probes,
// computes the same outputs as the Python side via strategy_viz/js/index.mjs,
// writes the result as JSON on stdout.
//
// Input:  { samples: [{name, strat}], probes: { spots: [n] } }
// Output: { sample_name: { thesis, entry_lines, exit_lines,
//                          per_leg_greeks, portfolio_greeks,
//                          payoff_at_spots: { spot: net_pnl } } }
import * as SViz from "../strategy_viz/js/index.mjs";

const chunks = [];
for await (const c of process.stdin) chunks.push(c);
const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));

const out = {};
for (const { name, strat } of input.samples) {
  const legs = strat.legs || [];
  const perLeg = legs.map(l => {
    const g = SViz.legGreeksAtEntry(l);
    return { delta: g.delta, gamma: g.gamma, vega: g.vega, theta: g.theta };
  });
  const portfolio = SViz.portfolioGreeks(legs);
  const payoff_at_spots = {};
  for (const s of input.probes.spots) {
    let total = 0;
    for (const l of legs) {
      const K = SViz.legStrike(l), prem = SViz.legPremium(l, K);
      total += SViz.legPayoffAt(l, s, K, prem);
    }
    payoff_at_spots[s] = total;
  }
  out[name] = {
    thesis: SViz.thesis(strat),
    entry_lines: SViz.entryRows(strat.entry || {}),
    exit_lines: SViz.exitRows(strat.exit || {}),
    per_leg_greeks: perLeg,
    portfolio_greeks: portfolio,
    payoff_at_spots,
  };
}
process.stdout.write(JSON.stringify(out));
