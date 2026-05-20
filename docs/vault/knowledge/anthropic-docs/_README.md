---
type: meta
status: active
created: 2026-05-20T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [readme, anthropic-docs, vault]
related: [[_INDEX]]
confidence: high
source: "src/cma/vault/scrape_anthropic_docs.py"
---

# Anthropic Claude Code Docs — Local Mirror

> Curated mirror of `https://code.claude.com/docs/en/*.md`. The full upstream
> index is at `../[_llms.txt](../_llms.txt)`.

## v1 curated page list (15)

Why these 15 (vs all 134 in `llms.txt`)? Each one is referenced by either
the vault's hook implementation or its design.

| Page | Why included |
|---|---|
| [`overview`](overview.md) | Operator's stated baseline reference |
| [`hooks`](hooks.md) | Full schemas for all 25+ hook events (drives `.claude/settings.json`) |
| [`hooks-guide`](hooks-guide.md) | Worked examples (drives Hook 1-4 design) |
| [`skills`](skills.md) | Tier 2: `vault-code-bridge`, `audit-automation-health` |
| [`sub-agents`](sub-agents.md) | Tier 2: `vault-librarian` subagent |
| [`commands`](commands.md) | Tier 2: `/adr-show`, `/handoff` (Anthropic's slug for slash-command docs) |
| [`mcp`](mcp.md) | `manage_adr` integration depends on MCP semantics |
| [`memory`](memory.md) | `CLAUDE.md` + auto-memory; vault↔memory boundary |
| [`settings`](settings.md) | `.claude/settings.json` shape; hooks location |
| [`permissions`](permissions.md) | `if:` field uses permission-rule syntax (Hook 2/3) |
| [`best-practices`](best-practices.md) | Anthropic-recommended patterns |
| [`common-workflows`](common-workflows.md) | Auto-formatting + validation idioms |
| [`cli-reference`](cli-reference.md) | `claude -p` headless mode for scripted use |
| [`plugins`](plugins.md) | Future: package cma's vault as a plugin |
| [`output-styles`](output-styles.md) | Operator uses 'learning' style — contextually relevant |

To scrape more, run `cma vault refresh-knowledge --all` (134 pages, ~600 KB).
Or scrape additional individual pages: `cma vault refresh-knowledge --page <slug> --force`.

## Conventions

- Filenames mirror the upstream slug (`hooks-guide.md`, not `hooks_guide.md`).
- Nested upstream paths get flattened with `-` (e.g. `agent-sdk/hooks.md` → `agent-sdk-hooks.md`).
- `_INDEX.md` is auto-generated; do not edit by hand.
- Stale (>60d) entries get a `⚠ stale` marker in `_INDEX.md`.

## Updating

The scraper is `cma.vault.scrape_anthropic_docs` — pure stdlib `urllib.request`,
no `httpx`/`WebFetch` dependency. Safe to invoke from any Python environment
that has cma installed.
