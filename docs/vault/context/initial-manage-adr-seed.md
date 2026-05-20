---
type: context
status: draft
created: 2026-05-20T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [manage-adr, seed, codebase-memory, draft]
related: [[../decisions/_INDEX]]
confidence: high
source: "distilled from README.md, CLAUDE.md, ONBOARDING.md, and the 7 backfilled ADRs"
---

# `manage_adr` initial seed — operator review required

> **DRAFT.** This file is the proposed content for the project's `manage_adr`
> single architectural summary (codebase-memory-mcp). It is **not yet stored**
> in `manage_adr`. The operator must review and approve, then run the storage
> call documented at the bottom.
>
> Per the `manage_adr` tool docstring: *"For creating/replacing: explore the
> codebase first using get_architecture and graph tools, then enter plan mode
> to draft the ADR collaboratively with the user. Store only after user
> approval."* This file is that draft.

## Constraint check

- Each section is sized to leave headroom under the 8000-char total cap.
- All six canonical sections present in canonical order.
- Body cites the specific ADRs (in `docs/vault/decisions/`) that ground each claim.
- No proprietary content; this is project-public.

---

## PURPOSE

`claude-managed-agents` (CLI: `cma`) is a project-agnostic backbone for using Anthropic's Claude Managed Agents beta across multiple consumer projects. It is designed so an agent picking it up cold can install + customize it for a new consumer project via one command (`cma project init --with-example --with-mcp-json --with-vault`). Per-project config lives in the consumer's `.managed-agents/`; nothing under `src/cma/` imports anything consumer-specific.

The toolkit ships two execution paths:

1. **Today**: `cma executor stdio` runs as a Claude Code MCP server. Claude Code orchestrates; the executor runs proprietary code in subprocess-isolated workers. No API credit needed.
2. **Tomorrow**: same surface exposed via Cloudflare Tunnel; Anthropic Managed Agents cloud agents connect as remote MCP clients. Proprietary code stays operator-local across both paths.

The vault under `docs/vault/` (added in this slice) is the operational meta-system — every future consumer project gets its own copy via `cma project init --with-vault`.

## STACK

- **Python 3.11+** (matches consumer floor).
- **Pydantic v2** for all data models. `model_validate` over `**dict` unpacking.
- **Typer + Rich** for CLI surfaces (`cma doctor`, `cma project`, `cma executor`, `cma bridge`, `cma agent`, `cma audit`, `cma webhook`, `cma vault`).
- **FastMCP** for the executor MCP daemon (optional extra: `[executor]`).
- **FastAPI + Uvicorn** for the webhook receiver (optional extra: `[webhook]`).
- **SQLite (WAL)** for job state + budget ledger.
- **Anthropic SDK** (>=0.87) with always-on `managed-agents-2026-04-01` + `cache-diagnosis-2026-04-07` beta headers.
- **codebase-memory-mcp** for this `manage_adr` surface + future ADR querying.
- Tooling: `ruff` (lint, line length 100), `mypy --strict`, `pytest`. 609+ tests covering hooks, executor, webhook, budget, telemetry, identifiers.

## ARCHITECTURE

**Tiered execution** (`2026-05-19-tiered-execution.md`): cloud agents orchestrate; `cma-local-executor` MCP daemon executes proprietary code locally. The cloud agent only sees tool names + inputs + outputs + declared `output_summary` metrics — never the source code, raw data, or parameter values.

**Operator-authored bridge** (`2026-05-19-operator-authored-mcp-bridge.md`): the toolkit ships only the schema validator + audit log. Operators author their own `.managed-agents/local_mcp_bridge.yaml`. No permissive default tool surface.

**Layered package** (`src/cma/`):
- `api/` — Anthropic SDK wrappers (client, agents, environments, sessions, vaults, events).
- `core/` — config (project, budget, webhook, telemetry), pricing, identifiers, agent_spec + lint.
- `executor/` — MCP daemon (server, runner, worker, store, audit, auth, notifications, bridge_config, bridge_probe).
- `webhook/` — FastAPI receiver (signature, events, policy, actions, receiver).
- `telemetry/` — JSONL emitter with mandatory credential redaction.
- `templates/` — `cma project init` payloads (starter, .mcp.json, vault).
- `vault/` — knowledge vault hooks + scraper + CLI (this slice).
- `cli/` — Typer subcommands per domain.

**Three-layer decision architecture** (introduced in this slice): `CHANGELOG.md` (release snapshot, immutable) + `docs/vault/decisions/*.md` (per-decision history) + `manage_adr` (this document — current summary). Each answers a different question; they don't drift.

## PATTERNS

- **Subprocess per job** (`2026-05-19-subprocess-per-job.md`): each `submit_job` spawns a fresh `python -m cma.executor.worker` for isolation. SIGTERM → 5s grace → SIGKILL.
- **Hybrid YAML + Python adapter** (`2026-05-19-hybrid-yaml-python-adapter.md`): `job_specs/*.yaml` declares the contract (input schema, allowed values, `output_summary` keys); `adapters/local_executor.py` provides the callables.
- **API key resolution chain** (`2026-05-19-api-key-resolution-chain.md`): env var → `CMA_API_KEY_HELPER` shell command → Claude Code's `apiKeyHelper` in `~/.claude/settings.json`. Sentinel detection rejects `sk-ant-..` placeholders.
- **Cache-aware sessions**: every API call adds `cache-diagnosis-2026-04-07`. Tool descriptions ≥50 chars to enable caching. Linter (`cma agent lint`) flags sub-threshold prompts + timestamp-bearing tool descriptions (cache-busting).
- **Operator-confirmed at decision points**: tiered execution, operator-authored bridge, subprocess-per-job, hybrid YAML/Python, observe-first kill switch, per-job notifier binding — each surfaced to the operator explicitly before landing.
- **Vault hooks enforce discipline** (this slice): PreToolUse on `git commit` blocks if CHANGELOG adds a "Decisions baked in" bullet without a parallel ADR file; Stop hook reminds when architectural keywords appear with no vault write.

## TRADEOFFS

- **Observe-first budget policy** (`2026-05-19-observe-first-budget-policy.md`): default is `NOTIFY_ONLY` on breach; `BudgetConfig.kill_on_breach=True` upgrades to `CANCEL_SESSION`. `CANCEL_PROJECT` / `EXHAUST_BUDGET` never selected — sibling sessions are independent experiments. Trade-off accepted: operator must opt into destructive behavior; risk: a runaway session under default config logs but doesn't get killed (mitigated by the breach-event telemetry being unmissable).
- **Per-job session binding** (`2026-05-19-per-job-session-binding.md`): notifications dispatched only to the originating MCP client, not broadcast. Prevents cross-client info leak in the future HTTP path. Trade-off accepted: no "watch all jobs from one dashboard" channel (would require a separate subscription).
- **Polling-primary, notifications-as-hint**: `get_job_status` / `get_job_result` are the contract; notifications are a courtesy. Trade-off: client implementations vary on notification consumption (mitigation: polling always works).
- **Operator runs Claude Max with OAuth, no API credit**: live `cma doctor --probe-beta` cannot be exercised against the real API. Mitigation: extensive unit tests + stdio integration tests via subprocess; pivot to Claude Code MCP path for daily use.
- **Spawn overhead per job** (~50-300ms): accepted for analytical/compute workloads; would be wrong for chat-fast iteration (not the target).

## PHILOSOPHY

The IP boundary is non-negotiable (`CLAUDE.md` Architectural Commitment #2). When in doubt, fail closed — never leak the consumer's proprietary code or data. The toolkit's whole structure is calibrated around this commitment: tiered execution exists to maintain it; subprocess-per-job exists to enforce it cleanly; the operator-authored bridge exists to prevent it being undermined by permissive defaults.

Other non-negotiables (`CLAUDE.md` "Non-negotiable rules"):

1. Never commit secrets. Only `.env.example` is tracked.
2. Never log credentials. Telemetry redacts `*token*`, `*secret*`, `*password*`, `*api_key*`, `whsec_`, `xoxp-`, `xoxe-`, `Bearer ` prefixes.
3. Beta headers on every API call (via `get_client()` only).
4. Pinned model + tool versions per session.
5. One MCP credential per `mcp_server_url` per vault.
6. Pre-flight check before any session.

**Telemetry first**: every API call and workflow stage emits a JSONL line before returning. Diagnostics beat heroics.

**Operator approval at choice points**: significant decisions surface explicitly. Five "operator-confirmed" markers in `CHANGELOG.md` reflect this — the human stays in the loop on shape-of-the-system choices even when the agent could decide alone.

---

## Storage instructions (operator)

When approved, run via the MCP tool:

```
mcp__codebase-memory-mcp__manage_adr
  mode: store
  content: <contents of this file, with the YAML frontmatter and this Storage section REMOVED>
```

Or via the CLI binary directly:

```
codebase-memory-mcp.exe cli manage_adr '{"mode":"store","content":"<...>"}'
```

After successful store, set this file's `status:` to `archived` (or delete) — the canonical content is now in `manage_adr`. Future edits use `mode='update'` with section-by-section diffs from new ADRs, prompted by Hook 3 (`check_adr_section_drift`).

**Size check**: rough word count of the content sections (PURPOSE through PHILOSOPHY) is well under the 8000-char hard cap; should land around 4500-5500 chars after the frontmatter and storage note are stripped. The 500-char headroom buffer (`_BUDGET_WARN_CHARS = 7500` in hf-2026's `_ccm_shared.py`) is respected.
