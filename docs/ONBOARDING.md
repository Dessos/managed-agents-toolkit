# Onboarding — new agent / contributor pickup

> **For an agent or human picking up the cma toolkit cold.** Skip to "Current state" if you just want the punch list. Skip to "Architecture in 60 seconds" if you want to know what this is.

## Architecture in 60 seconds

`claude-managed-agents` (CLI verb: `cma`) is a multi-project toolkit wrapping Anthropic's [Claude Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview) beta. Two modes:

1. **Today, Max-plan-only path** (operator's current setup):
   - Run `cma executor stdio` (or `serve`) as an MCP server.
   - Claude Code (the operator's client) connects, sees 5 tools: `submit_job`, `get_job_status`, `get_job_result`, `list_jobs`, `cancel_job`.
   - Cloud-side never touches Anthropic's Managed Agents API. The executor runs locally; Claude Code orchestrates.

2. **Tomorrow, with API credit path** (planned):
   - Same toolkit, same tools, exposed via Cloudflare Tunnel.
   - Anthropic Managed Agents cloud agents connect as remote MCP clients.
   - Strategy source / raw data / parameters never leave the operator's machine — only summary metrics cross the boundary (the IP boundary, see §IP Boundary below).

The architecture was decided through 3 grilling rounds + 6 build chunks with the operator. The plan file is operator-local (under their `~/.claude/plans/` directory) and not checked in.

## IP boundary (the load-bearing design)

The toolkit's central commitment is that **proprietary code and data never leave the operator's machine**. Whatever the consumer project's IP is — model weights, customer datasets, internal pricing models, scraped corpora, proprietary algorithms — only the summary metrics declared in each handler's `output_summary` cross the boundary to the cloud agent.

| Layer | Sees |
|---|---|
| Cloud agent (Managed Agents) or Claude Code (MCP client) | Tool names, descriptions, the input/output JSON of each call. Spec_refs as opaque slugs. Summary metrics. |
| `cma executor` (MCP server in subprocess) | JSON-RPC requests. Validates against the operator's `job_specs/*.yaml` schema. Dispatches to the adapter. |
| `python -m cma.executor.worker` (per-job subprocess) | Operator-authored adapter Python, full local data, the consumer project's proprietary source, parameter values. |

The cloud agent learns only what the operator declared in `output_summary`. If the handler doesn't put proprietary parameters in the returned dict, the cloud never sees them.

## Current state (v0.5.1; backbone)

| | |
|---|---|
| Repo | https://github.com/Dessos/managed-agents-toolkit (public) |
| Default branch | `claude` |
| Version | 0.5.1 |
| Tests | 485 passing |
| CI | green on py3.11 + py3.12 |
| Lint/types | ruff clean, mypy strict clean on 47 source files |
| Operator's subscription | Claude Max plan with OAuth (no separate API credit) — see §Constraints below |

### What's built and tested

| Chunk | Subject | Where |
|---|---|---|
| 1 | Foundation: SDK client, project config, pricing, budget controller, telemetry, identifiers, CLI scaffolding | `src/cma/api/client.py`, `src/cma/core/`, `src/cma/cli/__main__.py`, `src/cma/cli/doctor.py`, `src/cma/cli/project.py`, `src/cma/telemetry/` |
| 2 | Local executor MCP daemon (5 tools, subprocess runner, hybrid YAML+Python adapter, bearer auth, SQLite state, audit log) | `src/cma/executor/`, `src/cma/cli/executor.py`, `src/cma/cli/bridge.py` |
| 3 | apiKeyHelper resolution chain + sentinel detection + Get-Secret setup guide | `src/cma/api/client.py`, `docs/api-key-storage.md` |
| 4 | stdio transport for Claude Code integration + `.mcp.json` template + starter example adapter (`compute_stats_handler` works; `example_long_job_handler` is a stub for consumer-side wiring) | `src/cma/cli/executor.py` (stdio cmd), `docs/claude-code-integration.md`, `src/cma/templates/starter/` |
| 5 | GitHub push + visibility flip to public + scrub of operator-specific identifiers | (repo metadata + commits) |
| 6 | Bridge schema deep validation (`cma bridge probe`) + injectable MCP notifications | `src/cma/executor/bridge_config.py`, `src/cma/executor/bridge_probe.py`, `src/cma/executor/notifications.py` |
| 7 | `cma agent lint` — 10-rule local YAML validator (description discipline, sub-threshold system prompts, unknown models, tool-cache hygiene) + `cma agent list` | `src/cma/core/agent_spec.py`, `src/cma/core/lint.py`, `src/cma/cli/agent.py`, `docs/agent-lint.md` |
| 8 | `cma audit` — human-readable view of the executor MCP audit log with `--since` / `--tool` / `--client-id` / `--error-only` / `--json` filters | `src/cma/cli/audit.py`, `src/cma/executor/audit.py` (added `audit_log_path` helper) |
| 9 | `cma webhook serve/verify` — FastAPI receiver for Anthropic webhooks. Svix signature verification, budget kill switch via operator-authored policy (observe-first: `BudgetConfig.kill_on_breach` toggles `CANCEL_SESSION` ↔ `NOTIFY_ONLY`), pluggable `Actions` (NullActions default for Max-only setup). | `src/cma/webhook/`, `src/cma/cli/webhook.py` |
| 10 | `FastMCPSessionNotifier` — production wiring for `notifications/cma/job_status_changed`. Per-job session binding (not broadcast): the active MCP `Context` is captured at `submit_job` time and the JSON-RPC notification dispatches only to the originating session on terminal transition. Wired by default in `cma executor stdio` / `serve`. | `src/cma/executor/notifications.py`, `src/cma/executor/server.py`, `src/cma/cli/executor.py` |
| 11 | **Foundational vault** (`docs/vault/`) — markdown knowledge vault + 4 Claude Code hooks for anti-decay enforcement + `cma vault` CLI surface + `cma project init --with-vault` templating. Three-layer architecture (CHANGELOG snapshot ↔ markdown ADRs ↔ `manage_adr` summary). Backfilled 7 load-bearing ADRs from v0.1.0–v0.5.1 "Decisions baked in" sections. Anthropic docs scraper (`cma vault refresh-knowledge`) caches `code.claude.com/docs/en/*.md` locally to stop re-fetching. Plan v4.1: Slice D shipped, Slice A shipped, Slices B/C/E/F pending. | `docs/vault/`, `src/cma/vault/`, `src/cma/cli/vault.py`, `.claude/settings.json`, `src/cma/templates/vault/` |

### What's pending (in priority order, all unblocked)

1. **CLI surfaces**: `cma env`, `cma session` — wrappers exist; CLI groups don't. ~1 hour. (`cma agent`, `cma audit`, `cma webhook` shipped in chunks 7+8+9.)
2. **`SdkActions`** — production wiring of the webhook action executor (cancel/archive sessions via the Anthropic SDK). Blocked on API credit; lands in the same release the operator first uses it. ~1 hour.
3. **Confirm the `test_end_to_end_notification_via_real_run` flake is fixed** — the session-notifier wiring slice ran the test 5× clean. If it stays clean across the next 20-30 CI runs, drop the "keep watching" caveat from this doc. If it returns, the assertion is at `test_executor_notifications.py:240` (timestamp/ISO substring). ~5 min to remove the caveat once confidence is built.

(Consumer-side integration work — wiring real handlers into the starter adapter for whatever project you're using the backbone with — lives in the consumer project, not in the backbone's pending list.)

### What's blocked

- **Pilots P1, P2, P3, P4** (the original Managed Agents pilot plan) — blocked on operator's decision around adding API credit. The toolkit is ready; the runtime isn't accessible without it.

## Constraints + design decisions you should know

1. **Operator runs Claude Max plan with OAuth, no API credit.** Authenticated via OAuth tokens in `~/.claude/.credentials.json` (auto-refreshed in memory; disk copy is always stale). No raw API access. The `cma doctor --probe-beta` command exists but the operator cannot actually run it.
2. **apiKeyHelper sentinel detection.** The toolkit recognizes 10-char `sk-ant-..` placeholders and falls through to the helper chain. See `cma.api.client._looks_like_real_key`.
3. **MCP notifications are speculative.** Server-side dispatch is bulletproof and tested. Whether Claude Code or Managed Agents act on `notifications/cma/job_status_changed` is client-dependent. The polling path via `get_job_status` is the contract.
4. **Bridge transport is optional per entry.** Without transport config, lint is shape-only. With transport config, `cma bridge probe` deep-validates by connecting and listing tools.
5. **Strict tool-description discipline.** Per the Anthropic docs ≥3-4 sentence rule. Custom tool descriptions <50 chars fail lint.
6. **One adapter per consumer project.** All handlers live in `.managed-agents/adapters/local_executor.py`. Handlers MUST be `async def`.
7. **Telemetry redaction is non-optional.** The `redact()` function in `cma.telemetry.jsonl` masks keys matching `*token*`, `*secret*`, `*password*`, `*api_key*`, and values matching `whsec_`, `xoxp-`, `xoxe-`, `Bearer `, `sk-ant-`, `ghp_`, `lin_api_` prefixes. 20+ tests cover every pattern.

## Quick verification (run these to confirm the toolkit works on your machine)

```bash
# Setup
cd <REPO_ROOT>  # wherever you cloned claude-managed-agents
pip install -e ".[dev,webhook,prometheus,executor]"

# Full sweep
python -m pytest tests/unit/                     # 472 passing
python -m ruff check src tests                   # clean
python -m mypy src/cma                           # clean

# CLI smoke
python -m cma.cli doctor                         # local diagnostic (no API call)
python -m cma.cli --help                         # 7 subcommands: doctor, project, executor, bridge, agent, audit, webhook
python -m cma.cli executor --help                # 3 subcommands: stdio, serve, jobs
python -m cma.cli agent --help                   # 2 subcommands: lint, list
python -m cma.cli audit --help                   # filter executor MCP audit log
python -m cma.cli webhook --help                 # 2 subcommands: serve, verify (requires [webhook] extra)
```

If all of the above work, the toolkit is operational.

## Reading order

If you're new and want to understand what's here:

1. **`README.md`** — the public-facing overview.
2. **This file** (`docs/ONBOARDING.md`) — you are here.
3. **`CHANGELOG.md`** — version-by-version decisions baked in.
4. **`docs/vault/README.md`** — knowledge vault (decisions, learnings, session context, cached Anthropic docs). New as of v0.6 (vault build slices D + A; B/C/E/F pending).
5. **`docs/vault/context/ai-session-brief.md`** — single-screen orientation for a cold-start agent. The `SessionStart` hook (Slice B, pending) injects this automatically.
6. **`docs/vault/decisions/_INDEX.md`** — current Architectural Decision Records.
7. **`docs/api-key-storage.md`** — apiKeyHelper + Get-Secret setup.
8. **`docs/claude-code-integration.md`** — using cma-local-executor as a Claude Code MCP server. **Most useful for the operator's current path.**
9. **`src/cma/templates/starter/`** — drop-in starter that ships with the wheel. Materialize a copy into your project with `cma project init --with-example`.
10. **`CLAUDE.md`** — toolkit-internal project memory.
11. **The plan file** (operator-local under their `~/.claude/plans/` directory; not checked in) — the full design rationale.

## When to do what

| Operator says | You should |
|---|---|
| "Run job X" | If a matching `spec_ref` exists in `.managed-agents/job_specs/`, pre-emptively use `submit_job` via cma-local-executor MCP. Otherwise, ask which spec/inputs the operator wants. |
| "Add a new job type" | New `.managed-agents/job_specs/<name>.yaml` + new handler in `local_executor.py`. The `JOB_HANDLERS` dict + spec validation enforces the contract. |
| "Why isn't this working?" | Check the audit log: `cma audit --since=24h` (if implemented) or read `.managed-agents/.state/executor-audit.jsonl` directly. |
| "Make a new release" | Bump `pyproject.toml` + `src/cma/__init__.py`, add CHANGELOG entry, commit, push. CI auto-runs. |
| "Push to GitHub" | `git push origin claude` from the repo root. |
| "Log this decision" / "write an ADR for ..." | `cma vault new-decision <kebab-slug>` (once Slice E lands), or copy `docs/vault/_templates/decision.md` manually. Fill the frontmatter + the six sections. |
| "What did we decide about X?" | First grep `docs/vault/decisions/*.md` for X. Fall back to CHANGELOG "Decisions baked in" sections if not found. Use `manage_adr` `mode='get'` for the current architectural summary. |
| "Refresh the Anthropic docs" | `cma vault refresh-knowledge` (curated 15 pages) or `cma vault refresh-knowledge --all` (all 134). Skips entries <30d old. |
| Hook 2 blocked my commit | A `### Decisions baked in` bullet was added to CHANGELOG without a parallel ADR. Either write the ADR (`cma vault new-decision`) or, in emergency, `CMA_VAULT_COMMIT_BYPASS=1 git commit ...`. |
| Hook 4 blocked my Stop | The session touched architecture/design/decision words but no vault file was written. Write an ADR or learning, or `CMA_VAULT_STOP_BYPASS=1` to bypass (logged). |

## Things that bit us during the initial build (anti-patterns to avoid)

- **`git filter-branch --all` on a freshly-fetched repo** rewrote thousands of unrelated upstream commits we didn't want to touch. Use `HEAD` not `--all` unless you mean it.
- **GitHub email privacy** blocks pushes with personal emails. Use noreply: `<ID>+<USER>@users.noreply.github.com`. Set in `git config user.email` AND in `pyproject.toml` authors field.
- **GitHub Actions billing** can silently block CI on private repos. Public repos get unlimited free minutes; if the toolkit has no IP, public is the right call.
- **The `[executor]` extra** is required in CI; tests import `mcp` from the optional dep. Don't forget to install all extras in CI.
- **`asyncio.create_task` without holding a reference** can be GC'd mid-execution. `ExecutorServer._background_tasks` is the strong-ref set that prevents this.
- **CLI workflow trigger branch must match the default branch.** Default is `claude`, not `main`.

---

_Last updated 2026-05-19. Tagged at v0.5.1 on the `claude` branch. The v0.5.0 baseline was a single squashed initial commit (granular per-feature history was rewritten away during a public-release clean-up); subsequent releases are individual commits. The per-feature narrative lives in CHANGELOG.md._
