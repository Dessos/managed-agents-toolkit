---
type: context
status: active
created: 2026-05-20T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [priorities, pending, todo]
related: [[ai-session-brief]]
confidence: high
source: "docs/ONBOARDING.md What's pending section + plan v4.1 slices"
---

# Current priorities

> Operator-curated. Live list of unblocked work + known blockers. Update
> when state changes.

## In progress

### Foundational vault build (plan v4.1)

| Slice | Subject | Status |
|---|---|---|
| D | Anthropic docs scraper + initial scrape | ✅ Done (533 tests passing) |
| A | Vault scaffolding + 7 backfilled ADRs | 🔄 In progress |
| B | 4 hooks + enforcement scripts + tests | ⏳ Pending |
| C | `manage_adr` initial seed | ⏳ Pending |
| E | `cma vault` CLI surface | ⏳ Pending |
| F | `cma project init --with-vault` templating | ⏳ Pending |

## Unblocked (next up after vault build)

1. **`cma env`, `cma session` CLI groups** — wrappers exist, CLI groups don't. ~1 hour.
2. **`SdkActions`** — production wiring of the webhook action executor (cancel/archive via Anthropic SDK). Blocked on first session that uses API credit. ~1 hour.
3. **Confirm `test_end_to_end_notification_via_real_run` flake fix** — has run 5× clean since the session-notifier wiring landed. Drop the "keep watching" caveat after 20-30 clean CI runs.

## Blocked

- **Pilots P1, P2, P3, P4** — blocked on operator decision around adding API credit. Toolkit is ready; runtime inaccessible without it.
- **CI parity for Hook 2** — needs Slice B landed first.
- **`vault-librarian` subagent** (Tier 2) — needs ADR corpus to be useful; needs Slice A + B complete.

## Recently completed

- v0.5.1 release tagged (CI fix + project-agnostic repositioning + `cma project init` evolution).
- `cma webhook` + `cma audit` + `cma agent` CLI surfaces shipped.
- `FastMCPSessionNotifier` wired into `cma executor stdio` / `serve` by default.
- Vault build Slice D — Anthropic docs scraper.

## Known gaps to watch

- Hook 3's `"type": "mcp_tool"` variant not personally smoke-tested (fallback: command-type with CLI shim, proven via hf-2026).
- `manage_adr` 8000-char cap may bite as the ADR corpus grows (hf-2026 has `check_merged_size` budget guard — port if needed).
- `.obsidian/` directory is gitignored — if operator uses Obsidian features (Dataview, Templater), per-machine config doesn't travel.

## Update discipline

When a slice completes, mark it done in this file ⬆ AND in `ai-session-brief.md` sprint table. The Hook 4 (`check_session_writes`) Stop-rule treats unwritten architectural conversations as a problem — this file is one place those writes land.
