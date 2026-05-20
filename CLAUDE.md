# claude-managed-agents — Project Memory

> Toolkit-internal instructions. Companion to the consumer-project `.managed-agents/` configs.
>
> **Picking this up cold?** Read [`docs/ONBOARDING.md`](docs/ONBOARDING.md) first — it has the current state, constraints, what's built vs pending, and reading order.

## What this repo is

Project-agnostic backbone for using Anthropic's [Claude Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview) beta against any project. Designed so an agent picking it up cold can install + customize it for a new consumer project in one command (`cma project init --with-example --with-mcp-json`). Per-project config in consumer-project `.managed-agents/`; nothing in `src/cma/` imports anything consumer-specific. Multiple consumer projects intentionally supported — one pilot consumer is being onboarded first, others to follow.

## Architectural commitments

1. **Project-agnostic core.** Nothing in `src/cma/` may import a consumer-specific module. Project specifics live in templates + adapter contracts.
2. **Tiered execution.** Cloud agents NEVER touch proprietary code. Local infrastructure (this toolkit's `cma-local-executor` MCP daemon) handles any execution that needs proprietary code or data.
3. **Operator-authored bridge.** No default permissive MCP bridge preset. The toolkit ships only the schema validator + audit log; operators write their own `.managed-agents/local_mcp_bridge.yaml`.
4. **Cache-aware by default.** Every session adds `cache-diagnosis-2026-04-07` beta header alongside `managed-agents-2026-04-01`. Cache diagnostics are captured to telemetry on every response.
5. **Budget enforced structurally.** Pre-flight check on `cma session start`. Webhook-driven kill switch on `session.status_idled` budget breach.
6. **Strict tool linting.** Custom tool descriptions <50 chars hard-fail. Missing namespace hard-fail. Examples + field docs required.

## Non-negotiable rules

1. **Never commit secrets.** Only `.env.example` is tracked. `.env` is gitignored.
2. **Never log credentials.** Telemetry layer redacts `*token*`, `*secret*`, `*password*`, `*api_key*`, and values matching `whsec_`, `xoxp-`, `xoxe-`, `Bearer ` prefixes before writing JSONL.
3. **Beta headers on every API call.** `get_client()` is the only path to anthropic.Anthropic; do not instantiate directly elsewhere.
4. **Pinned model + tool versions per session.** Coordinator agents use `{type: "agent", id, version: N}` form in multiagent rosters.
5. **One MCP credential per `mcp_server_url` per vault.** Toolkit refuses to create duplicates.
6. **Pre-flight check before any session.** Budget + bridge config + alpha access state all verified.

## Coding conventions

- **Lint**: ruff (`ruff check src tests`)
- **Type check**: mypy (`mypy src/cma`)
- **Test**: pytest (`pytest tests/`)
- **Python**: 3.11+ (matches consumer-project floor)
- **Line length**: 100
- **Pydantic v2** for all data models. `model_validate` over `**dict` unpacking.
- **Typer** for CLI. Subcommands grouped by domain (`cma agent ...`, `cma session ...`).
- **Telemetry first** — every API call and workflow stage emits a JSONL line BEFORE returning.

## Plan reference

The authoritative plan lives operator-local under `~/.claude/plans/` and is not checked in. Mirror critical sections to `docs/plan.md` in this repo as they stabilize.

## Knowledge vault — `docs/vault/`

Decision logs, learnings, session context, and cached reference material live in [`docs/vault/`](docs/vault/) — see [`docs/vault/README.md`](docs/vault/README.md) for the 3-layer model (CHANGELOG snapshot ↔ markdown ADRs ↔ `manage_adr` summary).

**Distinct from `cma.api.vaults`**: this knowledge vault is *operator + agent shared notes*. `cma.api.vaults` is the Anthropic Managed Agents *credential vault* (MCP server bearer tokens, governed by Non-negotiable rule #5). Same English word, different concept. Verbal convention: "knowledge vault" vs "credential vault".

**Update discipline is hook-enforced** (`.claude/settings.json`, Slice B pending):
- `PreToolUse[git commit]` blocks commits that add `### Decisions baked in` bullets to CHANGELOG without a parallel ADR file staged in `docs/vault/decisions/`.
- `Stop` blocks session-end if the transcript contains architectural keywords but no vault file was written/edited this session.
- Bypass: `CMA_VAULT_BYPASS=1` (both gates), `CMA_VAULT_COMMIT_BYPASS=1`, `CMA_VAULT_STOP_BYPASS=1`. Every bypass is telemetry-logged.

## Quick commands

- **Doctor**: `cma doctor --probe-beta` — verifies alpha access state
- **Lint bridge**: `cma bridge lint` — validates `.managed-agents/local_mcp_bridge.yaml`
- **Session start**: `cma session start --workflow <name>` — main entry point
- **Audit**: `cma audit --since=24h --project=<name>` — sanitized MCP call log
- **Vault refresh**: `cma vault refresh-knowledge` — scrape Anthropic Claude Code docs into `docs/vault/knowledge/anthropic-docs/` (default: curated 15 pages, 30d freshness window)
- **Vault bypass** (use sparingly): `CMA_VAULT_BYPASS=1` (master), `CMA_VAULT_COMMIT_BYPASS=1` (Hook 2 only), `CMA_VAULT_STOP_BYPASS=1` (Hook 4 only). Bypass events log to `O:/Temp/cma-vault-hook.jsonl`.

## Workflow

- Prefer editing existing files over creating new ones.
- Templates go under `src/cma/templates/`, never under `src/cma/` core modules.
- For governance-relevant changes (security model, budget defaults, bridge schema), cross-reference the plan file.
- Risk vetoes anything that could leak the consumer's proprietary code or data — when in doubt, fail closed.
