# Agent Configuration

## Skill Naming Convention

This repository intentionally deviates from the [AgentSkills specification](https://agentskills.io/specification)'s requirement that `name` must match the parent directory name.

### Rationale

The `paradex-` prefix in the `name` field namespaces each skill to the Paradex platform. In a shared skills registry, skills from different vendors may share generic names (e.g., `market-analyst`). The prefix avoids collisions and makes the skill's origin unambiguous to agent routers.

Short directory names (without the prefix) keep the repository easier to navigate and match common CLI convention.

### Validation

`npx skills-ref validate ./skills/execution-analyst` will fail with "Directory name 'execution-analyst' must match skill name 'paradex-execution-analyst'". This is expected and intentional.

All other validation checks (description length, name format, required fields) pass:

```bash
for skill in skills/*/; do
  result=$(npx skills-ref read-properties "$skill" 2>&1)
  echo "$skill: $(echo "$result" | head -1)"
done
```

## Content Integrity

Each skill includes a `metadata.version` field in its frontmatter. Structural changes increment the version.

**When to bump `metadata.version`:** Increment the version whenever you modify a skill and it is ready to publish. Use semver-style minor bumps for behaviour or content changes (`"1.0"` → `"1.1"`) and major bumps for breaking interface changes — renamed capabilities, removed output fields, changed trigger phrases (`"1.0"` → `"2.0"`). Do **not** bump for draft or WIP edits; bump only when the change is shippable.

For supply-chain integrity when distributing skills outside of git:
- The git commit hash is the authoritative content identifier for this repository.
- For external distribution (zip, registry), generate a SHA-256 hash of the SKILL.md body and store it as a sidecar file (`SKILL.md.sha256`) or in a distribution manifest. The hash should cover the **Markdown body only** (after the `---` frontmatter delimiter), so the hash remains stable when only metadata changes.
- There is no standardised `content_hash` field in the AgentSkills spec as of May 2026. Integrity is typically handled at the distribution layer (package registry signatures, Sigstore, or git provenance) rather than inside the file itself — embedding the hash creates a chicken-and-egg problem: the hash changes the file, which changes the hash.
