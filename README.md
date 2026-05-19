# claude-managed-agents

Multi-project toolkit wrapping Anthropic's [Claude Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview) beta. Project-agnostic core; per-project config in `.managed-agents/`.

> **Status: pre-alpha.** Built autonomously from a planning session on 2026-05-18 → 19. See [`docs/plan.md`](docs/plan.md) for the full architecture, pilot ordering, and contingency model.

## Why this exists

The Anthropic Managed Agents docs make a powerful runtime available, but using it well across multiple projects requires:

- **Tiered execution** — cloud agent orchestrates, local infra executes anything touching proprietary code or data. The cloud agent never sees the consumer's proprietary source.
- **Operator-authored MCP bridge** — explicit per-project YAML declares which local MCP servers are bridged to cloud agents, with audit log.
- **Outcome-driven workflows** — rubrics as versioned markdown; harness iterates until `satisfied` or `max_iterations`.
- **Budget controller** — daily/per-session caps with auto-interrupt.
- **Cache-aware** — every session uses `cache-diagnosis-2026-04-07` beta header; `cma cache report` surfaces hit rates and divergence reasons.
- **Pipeline-shaped** — onboarding a new workflow is "write a YAML + rubric," not "write a new Python module."

## Repo layout (target)

```
src/cma/
├── api/             # Thin SDK wrappers (agents, environments, sessions, events, vaults, files, webhooks)
├── cli/             # Typer entry points
├── core/            # Pure-logic primitives (config, budget, pricing, identifiers, rubric)
├── workflows/       # Outcome runner, coordinator runner, permission handler, custom tool router
├── telemetry/       # JSONL + optional Prometheus + console renderer
├── webhook_receiver/  # FastAPI app (optional dep)
├── executor/        # cma-local-executor MCP server (optional dep)
└── bridge/          # Local MCP bridge proxy + audit log
```

## Quick start

The install path is **operational today** on a Max-only subscription (no API
credit needed) via the local-executor + Claude Code integration. The
broader workflow runtime (`cma session start`, `cma outcome run`) is still
pre-alpha — those commands exist as scaffolding for when API credit lands.

```bash
# Install
pip install claude-managed-agents[webhook,executor]

# Scaffold against your project (interactive prompts when stdin is a TTY;
# flag-driven for agents and CI). One command, both modes.
cd /path/to/your/project
cma project init --project-name my-project --with-example --with-mcp-json

# What that wrote:
#   .managed-agents/project.yaml            (name=my-project, workspace_root=$PWD)
#   .managed-agents/local_mcp_bridge.yaml   (operator-authored — edit to declare local MCP servers)
#   .managed-agents/job_specs/*.yaml        (starter specs; edit/replace as needed)
#   .managed-agents/adapters/local_executor.py  (handler registry; edit for real handlers)
#   .mcp.json                               (wires cma-local-executor as a Claude Code MCP server)

# Verify the config
cma project verify

# Restart Claude Code to pick up .mcp.json — then the executor's 5 tools
# (submit_job, get_job_status, get_job_result, list_jobs, cancel_job)
# are callable from any Claude Code session in this project.
```

For the pre-alpha workflow surface (`cma session start`, `cma outcome run`,
`cma doctor --probe-beta`), see `docs/ONBOARDING.md` for current status.

## Status

| Phase | State |
|---|---|
| Phase 0 — doctor probe of alpha access | In progress |
| Phase 1 — foundation (auth, config, models, telemetry, budget, resource wrappers) | In progress |
| Phase 2 — primitives layer (agent/env/session/event CRUD) | Pending |
| Phase 2.15 — deep alpha exploitation (caching, escalation, self-spawn, etc.) | Planned |
| Phase 3 — pilots (P4 → P2 → [alpha wait] → P1 → P3) | Pending |
| Phase 4 — first consumer adoption | Pending |

## License

MIT — see [LICENSE](LICENSE).
