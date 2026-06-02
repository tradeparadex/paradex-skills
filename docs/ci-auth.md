# CI Authentication — Claude on Bedrock via GitHub OIDC

How the `@claude` bot and any Claude-powered CI run against **Amazon Bedrock**
without storing long-lived cloud credentials. The pattern: GitHub Actions mints
a short-lived OIDC token, AWS STS exchanges it for temporary Bedrock credentials,
and a per-run GitHub App token handles repo access. No static AWS keys and no
Anthropic API key live in the repository.

> **Why not the hosted Claude GitHub App?** The one-click app at
> `github.com/apps/claude` (and the `/install-github-app` quickstart) is
> **direct Claude API only** — it cannot use Bedrock. Bedrock requires the
> self-hosted workflow in [`.github/workflows/claude.yml`](../.github/workflows/claude.yml).
> See <https://code.claude.com/docs/en/github-actions>.

## What you need

| Secret | What it is |
|---|---|
| `AWS_ROLE_TO_ASSUME` | ARN of the IAM role GitHub OIDC may assume (Bedrock-only perms) |
| `APP_ID` | GitHub App ID (the branded bot identity) |
| `APP_PRIVATE_KEY` | The GitHub App private key (`.pem` contents) |

The role ARN is not sensitive — you can store it as a repo **variable** instead
of a secret if you prefer.

## One-time AWS setup

### 1. Register GitHub as an OIDC provider (once per AWS account)

- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

### 2. Create the IAM role with a repo-scoped trust policy

Lock the trust policy to this repository so no other repo can assume the role:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike":   { "token.actions.githubusercontent.com:sub": "repo:tradeparadex/paradex-skills:*" }
    }
  }]
}
```

Tighten the `sub` further if desired, e.g. `repo:tradeparadex/paradex-skills:pull_request`
or `repo:tradeparadex/paradex-skills:ref:refs/heads/main`.

### 3. Attach a least-privilege Bedrock policy

The Claude docs suggest `AmazonBedrockFullAccess` — prefer this tighter policy
scoped to model invocation only:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "InvokeClaudeOnBedrock",
    "Effect": "Allow",
    "Action": [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:ListInferenceProfiles",
      "bedrock:GetInferenceProfile"
    ],
    "Resource": [
      "arn:aws:bedrock:*:*:inference-profile/*",
      "arn:aws:bedrock:*:*:foundation-model/*"
    ]
  }]
}
```

Request access to the Claude models in Bedrock (in every region you target —
the workflow defaults to `us-west-2`) before the first run.

### 4. Create the GitHub App

A custom GitHub App gives the bot a branded identity and — unlike the default
`GITHUB_TOKEN` — lets commits Claude pushes trigger other workflows (e.g.
`Skill Evals`). Create it at <https://github.com/settings/apps/new>:

- Permissions: Contents R/W, Issues R/W, Pull requests R/W.
- Webhooks: off.
- Generate a private key, install the app on this repo, then store `APP_ID`
  and `APP_PRIVATE_KEY` as repo secrets.

## GITHUB_TOKEN vs. GitHub App — the tradeoff

| | Default `GITHUB_TOKEN` | Custom GitHub App (this setup) |
|---|---|---|
| Stored secrets | none (most secure) | one (`APP_PRIVATE_KEY`) |
| Claude's commits trigger other CI | no | yes |
| Bot identity | `github-actions[bot]` | your branded app |

For zero stored secrets, drop the `app-token` step in
[`claude.yml`](../.github/workflows/claude.yml) and pass nothing for
`github_token` (the action falls back to the job's `GITHUB_TOKEN`). AWS auth
stays keyless via OIDC either way.

## Notes

- `id-token: write` on the job is mandatory for OIDC — omitting it is the most
  common Bedrock setup failure.
- Bedrock model ids are **region-prefixed inference profiles**, e.g.
  `us.anthropic.claude-sonnet-4-6`.
- The existing `Skill Evals` workflow runs a local model (`--local --simulate`)
  and needs no credentials. To grade evals with real Claude on Bedrock in CI,
  add the same OIDC step there and call `run_evals.py` without `--local`.
- Automatic review on **every** PR (no `@claude` needed) is a separate feature —
  see <https://code.claude.com/docs/en/code-review>. It uses the same auth.
