# Contributing to Paradex Skills

Thanks for your interest in contributing! This repo follows the [AgentSkills](https://agentskills.so) open standard.

## Adding a new skill

### 1. Choose your skill idea

Good Paradex skills:
- Solve a specific analytical or decision-making need
- Orchestrate one or more [Paradex MCP tools](./docs/mcp-tools.md)
- Are distinct from existing skills (check the [skill table](./README.md#skills))

### 2. Create the directory

```
skills/your-skill-name/
├── SKILL.md              # Required
└── references/           # Optional — for detailed reference material
    └── your-reference.md
```

### 3. Write the SKILL.md

Every skill needs a `SKILL.md` with YAML frontmatter and markdown instructions.

**Required frontmatter fields:**

| Field | Rules |
|-------|-------|
| `name` | Must match directory name. Lowercase letters, numbers, hyphens only. Max 64 characters. No leading/trailing/consecutive hyphens. |
| `description` | What the skill does AND when to use it. Max 1024 characters. Include trigger phrases for discovery. |

**Optional frontmatter fields:** `license`, `compatibility`, `metadata`

**Example:**

```yaml
---
name: paradex-your-skill-name
description: >
  One-paragraph description of what this skill does and when an agent should
  activate it. Include specific trigger phrases like "analyze my positions",
  "check risk", etc.
---

# Your Skill Name

One-liner about what this skill does.

## Available MCP Tools

| Tool | What it provides |
|------|-----------------|
| `paradex_tool_name` | Description |

## Capabilities

### 1. Capability Name
[How to use the MCP tools to deliver this capability]

## Output Format
[Templates for how results should be presented]

## Caveats
[Limitations, disclaimers, what this skill does NOT do]
```

### 4. Body guidelines

- Keep the SKILL.md body under **500 lines**
- Use [progressive disclosure](https://agentskills.so): put detailed reference material in `references/` and link from the main file
- Reference files should be one level deep (don't chain references to other references)
- Write in a conversational but precise tone
- Include concrete output format examples
- Always state caveats and limitations

### 5. Naming conventions

- **Directory names**: no `paradex-` prefix (e.g., `skills/market-analyst/`)
- **`name` field**: include `paradex-` prefix for discoverability on registries (e.g., `name: paradex-market-analyst`)
- Use descriptive names: `market-analyst` not `analyzer`
- Prefer noun or gerund form: `risk-guardian`, `strategy-builder`

## Evals

Every skill should include an `evals/evals.json` file with 2–5 test cases. Evals stay in the source repo — they are excluded from packaged `.skill` files and never shipped to end users.

### Directory structure

```
skills/your-skill-name/
├── SKILL.md
├── references/
│   └── your-reference.md
└── evals/
    └── evals.json
```

### evals.json format

```json
{
  "skill_name": "paradex-your-skill-name",
  "requires_auth": false,
  "evals": [
    {
      "id": 1,
      "prompt": "Realistic user message — how someone would actually type it",
      "expected_output": "Human-readable description of what a good response looks like",
      "assertions": [
        "The output includes X",
        "Y is broken down into components A, B, and C",
        "The section Z is absent when condition W is not met"
      ]
    }
  ]
}
```

**`requires_auth`** — set to `true` if any capability in this eval uses authenticated MCP tools (`paradex_account_*`, `paradex_orders_*`). See [MCP authentication](#mcp-authentication-for-evals) below.

**Assertion guidelines:**

- ✅ Observable: `"The output includes a net P&L figure"`, `"Per-market table is sorted by net P&L descending"`
- ✅ Conditional: `"Win rate section is absent when fewer than 3 closing fills are present"`
- ❌ Too brittle: `"Output contains the exact phrase 'Net P&L: $270.00'"`
- ❌ Too vague: `"The output is good and helpful"`

Write 4–6 assertions per eval case. Grade outcomes, not paths — check what the skill produced, not which tools it called.

### Two kinds of evals

**1. Output quality** (`evals.json`) — does the skill produce correct outputs?
Include a variety of prompt styles for each skill:
- Casual: `"how'd i do today"`
- Precise: `"show me realized P&L and fill rate for the last 7 days"`
- Edge case: `"i only placed 1 order today"` (tests suppression of win rate section)

**2. Trigger accuracy** — does the skill fire for the right queries?
Add `"trigger_evals"` (train set, ~10-12 entries) and `"trigger_evals_validation"`
(held-out set, ~6 entries) arrays. Optimize descriptions against the train set only;
use validation to check generalization.

```json
{
  "skill_name": "paradex-trading-recap",
  "trigger_evals": [
    { "query": "recap my trading today", "should_trigger": true },
    { "query": "what are my current positions", "should_trigger": false },
    { "query": "how many trades did i place this week", "should_trigger": true },
    { "query": "check my margin health", "should_trigger": false }
  ]
}
```

If your skill is under-triggering in practice, improve the `description` field — not the body. The description is the primary routing mechanism.

### How to run evals

**Using the included runner (recommended):**

```bash
# Set API key (and optionally Paradex key for auth-required skills)
export ANTHROPIC_API_KEY=sk-ant-...
export PARADEX_ACCOUNT_PRIVATE_KEY=...   # optional — enables live account data

# Run all skills (uv handles dependencies automatically)
uv run run_evals.py

# Run a specific skill
uv run run_evals.py market-analyst

# Run multiple skills
uv run run_evals.py trading-recap execution-analyst

# Fastest check (first eval case only)
uv run run_evals.py --smoke

# Show per-assertion pass/fail detail
uv run run_evals.py -v

# Force simulation mode explicitly
uv run run_evals.py --simulate

# Run with real MCP tools (disables auto-simulation for non-auth skills)
uv run run_evals.py --live-mcp

# Save full results as JSON
uv run run_evals.py --output results.json
```

All skills run in **simulation mode** by default: the eval runner has no MCP server
connection, so the agent is told to fabricate realistic example values for testing
format and structure. Use `--live-mcp` to disable this for non-auth skills when a
real Paradex MCP server is available. Skills marked `requires_auth: true` always
simulate when `PARADEX_ACCOUNT_PRIVATE_KEY` is unset, even with `--live-mcp`.

**Dual-run (with vs. without skill):**

The most informative check: compare quality with and without the skill loaded. A
pass-rate delta of ≥30 points justifies the token cost of installing the skill.

```bash
# Run with baseline comparison (doubles API calls but shows skill value)
uv run run_evals.py trading-recap --with-baseline
```

The `--with-baseline` flag re-runs each case without the skill system prompt and
shows a Δ score per skill.

### Which model

| Role | Recommended model | Why |
|------|------------------|-----|
| Agent being tested | `claude-sonnet-4-6` | Balanced quality/cost for iteration |
| Assertion grading | `claude-haiku-4-5` | Fast and cheap for repetitive PASS/FAIL judgments |
| Description optimization | `claude-opus-4-6` | Better reasoning for generalizing from failures |

### MCP authentication for evals

Skills that use account-data tools (`paradex_account_*`, `paradex_orders_*`) require the Paradex MCP server running with `PARADEX_ACCOUNT_PRIVATE_KEY` configured. Mark these with `"requires_auth": true` in `evals.json`.

For CI/automated evals, use a **testnet account** with small balances. Never commit private keys — pass them as environment variables.

Skills using only public tools (`paradex_market_*`, `paradex_vaults`, `paradex_klines`, etc.) set `"requires_auth": false` and can run in any environment.

## PR checklist

Before submitting:

- [ ] `name` field is `paradex-` + directory name (see [agents.md](./agents.md) for naming convention)
- [ ] `description` field explains what AND when (under 1024 chars)
- [ ] `compatibility: Requires Paradex MCP server (mcp-paradex-py)` is set
- [ ] `metadata: author: tradeparadex` and `metadata: version: "X.Y"` are set
- [ ] Name is lowercase with hyphens only (max 64 chars)
- [ ] SKILL.md body is under 500 lines
- [ ] References are one level deep from SKILL.md
- [ ] MCP tools table lists all tools the skill uses
- [ ] Output format section has concrete examples
- [ ] Caveats section is present and honest
- [ ] Tested with the Paradex MCP server connected
- [ ] `evals/evals.json` present with at least 2 test cases and 3+ assertions each
- [ ] Evals pass at ≥80%: `uv run run_evals.py your-skill`
- [ ] skills-ref validates: `npx skills-ref validate ./skills/your-skill` (naming mismatch is expected — see agents.md)

## Improving existing skills

PRs that improve existing skills are welcome. Common improvements:
- Better output format templates
- Additional MCP tool usage patterns
- Expanded reference material
- Bug fixes in methodology or calculations

## Publishing to skill registries

Once skills are merged to main, they can be listed on skill registries for broader discovery.

### skills.sh (automatic)

No action needed. As users install via the CLI, skills get indexed automatically:

```bash
npx skills add tradeparadex/paradex-skills
```

### ClawHub

```bash
npm install -g clawhub
clawhub publish ./skills/your-skill-name --slug paradex-your-skill-name --version 1.0.0
```

### agentskills.so

Contact via [Discord](https://discord.gg/gwyWY8v9Ed) or email support@agentskills.so to get skills listed.

### SkillDock.io

Submit via the web interface at [skilldock.io](https://skilldock.io).

## Code of conduct

Be constructive. This is a community project for making Paradex more accessible to AI agents and their users.
