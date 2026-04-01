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
name: your-skill-name
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

- **No `paradex-` prefix** — skills are already in the `paradex-skills` repo
- Use descriptive names: `market-analyst` not `analyzer`
- Prefer noun or gerund form: `risk-guardian`, `strategy-builder`

## PR checklist

Before submitting:

- [ ] `name` field matches directory name
- [ ] `description` field explains what AND when (under 1024 chars)
- [ ] Name is lowercase with hyphens only (max 64 chars)
- [ ] SKILL.md body is under 500 lines
- [ ] References are one level deep from SKILL.md
- [ ] MCP tools table lists all tools the skill uses
- [ ] Output format section has concrete examples
- [ ] Caveats section is present and honest
- [ ] Tested with the Paradex MCP server connected

## Improving existing skills

PRs that improve existing skills are welcome. Common improvements:
- Better output format templates
- Additional MCP tool usage patterns
- Expanded reference material
- Bug fixes in methodology or calculations

## Code of conduct

Be constructive. This is a community project for making Paradex more accessible to AI agents and their users.
