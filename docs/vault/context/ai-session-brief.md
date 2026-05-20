---
type: context
status: active
created: 2026-05-20T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [brief, session-start, orientation]
related: [[current-priorities]]
confidence: high
source: "docs/ONBOARDING.md current-state section"
---

# Session brief — claude-managed-agents

> **Cold-start orientation. The `SessionStart` hook injects this content. If
> you're a fresh agent picking up the project, read this first, then
> `current-priorities.md`, then `decisions/_INDEX.md`.**

## What this repo is, in 60 seconds

`claude-managed-agents` (CLI verb: `cma`) is a project-agnostic backbone for using Anthropic's [Claude Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview) beta. Two execution paths:

1. **Today** (operator's path): `cma executor stdio` runs as a Claude Code MCP server. Claude Code orchestrates; the executor runs proprietary code locally. No API credit required.
2. **Tomorrow** (planned): same toolkit exposed via Cloudflare Tunnel; Anthropic Managed Agents cloud agents connect as remote MCP clients. Proprietary code never leaves the operator's machine — only declared `output_summary` metrics cross the IP boundary.

The vault you're reading right now (`docs/vault/`) is the meta-infrastructure: the bench where decisions get recorded, the tools that prevent decay, the light that orients the next agent.

## Current sprint focus

**Foundational vault build (v4.1 plan).** All 6 slices in flight or complete:

- [x] **Slice D** — Anthropic docs scraper + initial scrape of 15 curated pages.
- [ ] **Slice A** — Vault scaffolding + 7 backfilled ADRs (in progress).
- [ ] **Slice B** — 4 Claude Code hooks + enforcement scripts + tests.
- [ ] **Slice C** — `manage_adr` initial seed (operator-review gated).
- [ ] **Slice E** — `cma vault` CLI surface (new-*, lint, index, refresh-knowledge).
- [ ] **Slice F** — Meta-system templating (`cma project init --with-vault`).

## Open work + blockers

See [`current-priorities.md`](current-priorities.md) for the live list. Highlights:

- **Unblocked**: CLI surface (`cma env`, `cma session`), `SdkActions` production wiring, vault build itself.
- **Blocked on API credit**: Pilots P1–P4. `cma doctor --probe-beta` cannot be exercised against real API.

## Constraints you should know about

1. **Operator runs Claude Max plan with OAuth, no API credit.** Live API probes are non-runnable until that changes.
2. **`cma.api.vaults` ≠ knowledge vault.** The former is MCP credentials; this `docs/vault/` is decisions/learnings.
3. **Vault enforcement is live.** Hooks 2 + 4 will block commits / stops if you bypass discipline. Use `CMA_VAULT_*_BYPASS=1` only when warranted.
4. **MCP notifications are speculative.** Server-side dispatch is tested; client consumption is per-client.

## How to act on operator requests

| Operator says | You should |
|---|---|
| "Run job X" | If a matching `spec_ref` exists, use `submit_job` via cma-local-executor MCP. Otherwise ask. |
| "Add a new job type" | New `.managed-agents/job_specs/<name>.yaml` + handler in `local_executor.py`. |
| "Why isn't this working?" | `cma audit --since=24h` first. |
| "Make a release" | Bump `pyproject.toml` + `src/cma/__init__.py`, CHANGELOG entry, commit, push. |
| "Log this decision" | `cma vault new-decision <slug>`. |
| "What did we decide about X?" | Search `docs/vault/decisions/` + grep CHANGELOG "Decisions baked in" sections. |

## Reading order for deeper context

1. `docs/ONBOARDING.md` — full toolkit walkthrough.
2. `CHANGELOG.md` — version-by-version decisions.
3. `docs/vault/decisions/_INDEX.md` — current ADRs.
4. `CLAUDE.md` — non-negotiable rules.
5. `docs/api-key-storage.md`, `docs/claude-code-integration.md` — operator path setup.

## When this brief is stale

If `updated:` is >14d old at session start, the `check_brief_drift` hook warns. Operator: edit this file when sprint focus or blockers change. Agent: don't edit without operator approval — this is operator-authored.

## Bypass quick reference (for emergencies)

```bash
CMA_VAULT_BYPASS=1 git commit ...           # master bypass (both hooks)
CMA_VAULT_COMMIT_BYPASS=1 git commit ...    # Hook 2 only
CMA_VAULT_STOP_BYPASS=1 <action>             # Hook 4 only
```

Every bypass is logged to `O:/Temp/cma-vault-hook.jsonl`.
