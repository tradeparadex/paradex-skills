# Paradex Skills

Intelligence skills for AI agents trading on [Paradex](https://paradex.trade) — the zero-fee perpetual futures DEX.

Built on top of the [Paradex MCP server](https://github.com/tradeparadex/mcp-paradex-py), which handles raw data retrieval, authentication, and order management. These skills add the analytical and decision-making layer that turns raw market data into actionable intelligence.

## Skills

| Skill | What it does |
|-------|-------------|
| [market-analyst](./skills/market-analyst/) | Technical indicators, funding arb scanning, orderbook analysis, regime classification |
| [vault-intelligence](./skills/vault-intelligence/) | Vault discovery, comparison, risk-adjusted ranking, recommendation engine |
| [risk-guardian](./skills/risk-guardian/) | Margin health, liquidation distance, stress testing, portfolio risk scoring |
| [portfolio-copilot](./skills/portfolio-copilot/) | Conversational portfolio briefings for personal accounts and vaults — positions, P&L, balance |
| [trading-recap](./skills/trading-recap/) | Time-period activity summary — realized P&L from fills, fill rate, win rate, per-market breakdown |
| [execution-analyst](./skills/execution-analyst/) | Order replay and execution quality — arrival price slippage, VWAP benchmark, execution score 1-10 |
| [strategy-builder](./skills/strategy-builder/) | Natural language → structured strategy specs with historical validation |
| [strategy-listener](./skills/strategy-listener/) | Real-time WS / polling subscriber that evaluates strategy specs on market & user events and POSTs OpenCLAW webhooks on fires |
| [pm-analyzer](./skills/pm-analyzer/) | Margin calculation engine (XM and PM scenario scan) and delta-hedge order tool |
| [order-builder](./skills/order-builder/) | Order sizing and multi-leg execution — collateral %, position scaling, risk-based sizing, confirmation gate |
| [options-pricer](./skills/options-pricer/) | Options chain viewer, greek calculator (Δ/Γ/Θ/V), IV skew analysis, and sell-candidate ranker |
| [webchat-ui-renderer](./skills/webchat-ui-renderer/) | Renders structured JSON UI specs for the Paradex webchat terminal — metric cards, positions tables, charts, data tables, alert banners |

## Quick start

### Claude (via MCP)

These skills work with the Paradex MCP server connected to Claude. Add the MCP server first:

```json
{
  "mcpServers": {
    "paradex": {
      "command": "uvx",
      "args": ["mcp-paradex"],
      "env": {
        "PARADEX_ENVIRONMENT": "mainnet",
        "PARADEX_ACCOUNT_PRIVATE_KEY": "your_private_key"
      }
    }
  }
}
```

Then point Claude at this repo or copy individual skill folders into your workspace.

### OpenClaw

```
install the market-analyst skill from https://github.com/tradeparadex/paradex-skills
```

Or install individually:

```
install the risk-guardian skill from https://github.com/tradeparadex/paradex-skills
```

### Claude Code

```bash
# Clone into your skills directory
git clone https://github.com/tradeparadex/paradex-skills.git ~/.agents/skills/paradex-skills

# Or install a single skill
cp -r skills/market-analyst ~/.agents/skills/paradex-market-analyst
```

### skills.sh

```bash
npx skills add tradeparadex/paradex-skills --skill market-analyst
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Agent Platforms                                     │
│  Claude · OpenClaw · Claude Code · Any MCP client    │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────────┐
│  Paradex Skills (this repo)                          │
│  Analysis · Risk · Briefings · Recap · Execution · Strategy │
└──────────────────────┬──────────────────────────────┘
                       │ orchestrates
┌──────────────────────┴──────────────────────────────┐
│  Paradex MCP Server                                  │
│  16+ tools: markets, klines, trades, orderbook,      │
│  funding, vaults, account, orders (with auth)        │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────────┐
│  Paradex REST & WebSocket API                        │
│  250+ markets · Zero fees · Privacy · L2 on StarkNet │
└─────────────────────────────────────────────────────┘
```

## Prerequisites

- [Paradex MCP server](https://github.com/tradeparadex/mcp-paradex-py) connected to your agent
- For authenticated features (account, positions, orders): Paradex account with API key or subkey
- For read-only analysis (market data, vaults): No authentication needed

Each skill is self-contained — copy any `skills/<name>/` folder into your agent's skill directory and it works independently.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for details on adding new skills or improving existing ones.

Skills follow the [AgentSkills](https://agentskills.so) open standard — compatible with Claude, OpenClaw, Cursor, Windsurf, and other SKILL.md-compatible agents.

## License

MIT
