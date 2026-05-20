---
type: context
status: active
created: 2026-05-20T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [brief, session-start, orientation]
related: [[current-priorities]]
confidence: high
source: "operator-authored; scaffolded by `cma project init --with-vault`"
---

# Session brief — {{PROJECT_NAME}}

> **Cold-start orientation. The `SessionStart` hook injects this content. If
> you're a fresh agent picking up the project, read this first, then
> `current-priorities.md`, then `decisions/_INDEX.md`.**

## What this project is, in 60 seconds

_Replace this with a 2-3 sentence description of what {{PROJECT_NAME}} does, who uses it, and what the current strategic focus is. Keep it operator-authored: this is what you tell a colleague joining the team mid-sprint._

## Current sprint focus

_The single most important thing being worked on right now._

## Open work + blockers

See [`current-priorities.md`](current-priorities.md) for the live list.

## Constraints + things to know about

_Anything that surprises a new contributor in their first week — e.g., "we run Python 3.11 only", "the X API is rate-limited at 100rps", "we use OAuth via Y, not API keys"._

## How to act on operator requests

| Operator says | You should |
|---|---|
| "Log this decision" / "write an ADR for ..." | `cma vault new-decision <kebab-slug>`. Fill the frontmatter + the six sections. |
| "What did we decide about X?" | First grep `docs/vault/decisions/*.md` for X. Fall back to CHANGELOG. Use `manage_adr` `mode='get'` for the current architectural summary. |
| "Refresh the Anthropic docs" | `cma vault refresh-knowledge` (curated 15 pages) or `cma vault refresh-knowledge --all`. |

## Reading order for deeper context

1. `README.md` — project overview.
2. `CHANGELOG.md` — version-by-version decisions.
3. `docs/vault/decisions/_INDEX.md` — current ADRs.
4. `CLAUDE.md` — non-negotiable rules.

## When this brief is stale

If `updated:` is >14d old at session start, the `check_brief_drift` hook warns. Operator: refresh this file when sprint focus or blockers change.

## Bypass quick reference

```bash
CMA_VAULT_BYPASS=1 git commit ...           # master bypass (both hooks)
CMA_VAULT_COMMIT_BYPASS=1 git commit ...    # Hook 2 only
CMA_VAULT_STOP_BYPASS=1 <action>             # Hook 4 only
```

Every bypass is logged.
