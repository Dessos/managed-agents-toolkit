---
type: meta
status: active
created: 2026-05-20T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [readme, vault, entry-point]
related: [[CLAUDE]]
confidence: high
source: "scaffolded by `cma project init --with-vault`"
---

# `docs/vault/` — Knowledge vault for {{PROJECT_NAME}}

> Operator's bench, tools, and light. Persistent surface where decisions,
> learnings, session context, and cached reference material live so they
> survive across sessions, agents, and humans.

## The three-layer architecture

This vault works **alongside** `CHANGELOG.md` and `manage_adr` (codebase-memory-mcp). Each layer answers a different question — they do not duplicate.

| Layer | Path / Tool | Question it answers | Update cadence |
|---|---|---|---|
| Release snapshot | `../../CHANGELOG.md` | "What was decided at v0.5.0?" | Append-only at release time; immutable |
| Decision history | [`decisions/`](decisions/) | "Why did we decide X, and is it still valid?" | Per decision; revisitable via `reviewed_on` |
| Architectural summary | `mcp__codebase-memory-mcp__manage_adr` | "What's our current PURPOSE / STACK / ARCHITECTURE / PATTERNS / TRADEOFFS / PHILOSOPHY?" | When an ADR materially shifts state |

CHANGELOG is a snapshot in time; ADRs are the living explanation; `manage_adr` is the queryable summary.

## Folder map

```
docs/vault/
├── README.md           ← you are here
├── CLAUDE.md           ← agent instructions (read at session start)
├── _templates/         ← frontmatter-conformant templates
│   ├── decision.md     — Architectural Decision Record
│   ├── learning.md     — Empirical finding from a failure or experiment
│   ├── evaluation.md   — Experiment / backtest result
│   └── incident.md     — Postmortem
├── context/            ← operator-authored briefs (cold-start orientation)
│   ├── ai-session-brief.md
│   └── current-priorities.md
├── decisions/          ← ADRs (one file per decision)
├── learnings/          ← empirical findings
├── governance/         ← non-negotiable rules
└── knowledge/          ← scraped reference (Anthropic docs, etc.)
    └── anthropic-docs/
```

## CLI surface

```bash
cma vault new-decision <slug>      # copy template + open in $EDITOR
cma vault new-learning <slug>
cma vault new-evaluation <slug>
cma vault new-incident <slug>
cma vault lint [PATH]              # frontmatter validation
cma vault index                    # regenerate _INDEX.md files
cma vault refresh-knowledge        # scrape Anthropic docs (curated 15)
```

## Anti-decay enforcement (the hooks)

Four Claude Code hooks wired in `.claude/settings.json` keep this vault honest:

| Hook | When | What |
|---|---|---|
| `SessionStart` | Every session start | Reads `context/ai-session-brief.md` + last 5 ADRs into context |
| `PreToolUse[git commit]` | Before any `git commit` | **Blocks** if CHANGELOG adds a "Decisions baked in" bullet without a parallel ADR file staged |
| `PostToolUse[git commit]` | After successful commit | Nudges to update `manage_adr` if the committed ADR touches one of the 6 sections |
| `Stop` | When Claude finishes responding | **Blocks** if transcript contains architectural keywords but no vault file was written |

### Bypass (use sparingly — every bypass is logged)

```bash
CMA_VAULT_BYPASS=1 git commit ...           # both gates skip
CMA_VAULT_COMMIT_BYPASS=1 git commit ...    # only Hook 2 skips
CMA_VAULT_STOP_BYPASS=1 <action>             # only Hook 4 skips
```

Bypass events are appended to `O:/Temp/cma-vault-hook.jsonl` (Windows) or `/tmp/cma-vault-hook.jsonl` — frequent use is a signal the enforcement is mis-calibrated.

## Next steps after scaffolding

1. Edit [`context/ai-session-brief.md`](context/ai-session-brief.md) — describe your project's current sprint focus and constraints.
2. Edit [`context/current-priorities.md`](context/current-priorities.md) — list unblocked work + known blockers.
3. Run `cma vault refresh-knowledge` to populate `knowledge/anthropic-docs/` with the curated 15 Anthropic Claude Code docs.
4. Run `cma vault new-decision <first-decision>` when you record your first architectural choice.
5. Run `cma vault index` after batches of new notes to regenerate `_INDEX.md`.
