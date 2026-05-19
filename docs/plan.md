# Architecture + build plan

> Public mirror of the parts of the operator's local planning notes
> that have stabilized. The authoritative plan stays local; this file
> exists so the repo answers "what is this and where is it going" in
> one read.

## Premise

Anthropic's [Claude Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview)
runtime is powerful but adopting it well across multiple projects needs
more than the raw SDK provides:

- A tiered execution model that lets cloud agents orchestrate without
  ever touching proprietary code or data.
- A per-project config schema with validation, drift detection, and
  audit logging.
- A budget controller that catches runaway spend before the bill
  arrives.
- A scaffold an agent can drive cold to install the toolkit into a new
  project.

The toolkit (`claude-managed-agents`, CLI verb `cma`) ships these
opinionated defaults so each consumer project only writes the parts
that are genuinely consumer-specific.

## Tiered execution (the load-bearing design)

| Layer | What it sees | What it never sees |
|---|---|---|
| **Cloud agent** (Managed Agents runtime, or Claude Code via MCP) | Tool names + descriptions, JSON-RPC input/output of each call, opaque `spec_ref` slugs, the curated `output_summary` of each job. | Adapter Python, raw inputs, proprietary parameters, per-step state. |
| **`cma executor`** (MCP server in operator's subprocess) | JSON-RPC requests, the spec YAML, the adapter module's symbol table. | Nothing extra — runs the adapter in a child process. |
| **`python -m cma.executor.worker`** (per-job subprocess) | The adapter Python, full local data, the consumer project's proprietary source. | — (it's the innermost layer). |

The cloud learns only what the operator declared in `output_summary`. If
the handler doesn't put a value in the returned dict, the cloud never
sees it. Every MCP call is JSONL-audit-logged at the executor before
dispatch.

## Component map

```
src/cma/
├── api/        # Thin SDK wrappers (agents, environments, sessions, events, vaults)
├── cli/        # Typer entry points (doctor, project, executor, bridge, agent, audit, webhook)
├── core/       # Pure-logic primitives (config, budget, pricing, identifiers, lint, agent_spec)
├── executor/   # Local MCP daemon: server, runner, worker, spec, adapter, audit, bridge probe/config
├── telemetry/  # JSONL emitter with mandatory credential redaction
├── templates/  # project.yaml, local_mcp_bridge.yaml, .mcp.json.example, starter/
│   └── starter/   # Bundled example adapter + job specs — copied by `cma project init --with-example`
└── webhook/    # FastAPI receiver with Svix signature verification + budget kill-switch policy
```

## Build phasing

| Phase | Subject | State |
|---|---|---|
| 0 | Doctor probe of Anthropic alpha access | scaffolded; requires API credit to actually run |
| 1 | Foundation: SDK client, config, telemetry, budget, identifiers, CLI scaffolding | shipped |
| 2 | `cma-local-executor` MCP daemon (5 tools, subprocess runner, audit log) | shipped |
| 3 | apiKeyHelper resolution chain + sentinel detection | shipped |
| 4 | stdio transport for Claude Code integration + starter example adapter | shipped |
| 5 | Public release + identifier scrub of operator-specific surface | shipped |
| 6 | Bridge schema deep validation + injectable MCP notifications | shipped |
| 7 | `cma agent lint` — 10-rule local YAML validator | shipped |
| 8 | `cma audit` — human-readable executor MCP log reader | shipped |
| 9 | `cma webhook serve/verify` — FastAPI receiver + Svix verification + budget kill-switch policy | shipped |
| 10 | `FastMCPSessionNotifier` — production wiring for `notifications/cma/job_status_changed` | shipped |
| 11 | `cma project init` — both-modes scaffold (interactive + flag-driven) | shipped |
| 12 | `cma env`, `cma session` CLI surfaces | pending |
| 13 | `SdkActions` — production wiring of webhook action executor | blocked on API credit |
| 14 | Pilots P1-P4 against the full Managed Agents runtime | blocked on API credit |

## What runs today vs. what's pre-alpha

**Operational today on a Claude Max subscription (no separate API credit):**
- `cma project init` — scaffold a consumer-project config in one command.
- `cma executor stdio` — local MCP server Claude Code connects to via `.mcp.json`.
- `cma agent lint` — local YAML validator with 10 rules.
- `cma audit` — read the executor MCP audit log.
- `cma bridge lint` / `cma bridge probe` — validate the local MCP bridge YAML.

**Pre-alpha (scaffolded, blocked on Anthropic API credit):**
- `cma doctor --probe-beta` — probes alpha-access state via live API call.
- `cma session start` / `cma outcome run` — Managed Agents runtime drivers.
- `cma webhook serve` — FastAPI receiver works, but `SdkActions` (cancel/archive via Anthropic SDK) needs credit to actually act on policy decisions.

## Contingency model

- **No API credit, ever**: the `cma executor stdio` + Claude Code MCP path
  delivers value standalone. Treat the Managed Agents-runtime surface as
  optional rather than required.
- **Operator-specific identifier leaks into public surface**: the
  `cma agent lint` rule set catches description drift; broader scrubs
  happen at release time via multi-pass agent scans. Repository history
  rewrites are the nuclear option used sparingly.
- **Cloud-side runtime drift** (Anthropic changes wire format): every
  request goes through `cma.api.client.get_client()` which is the single
  patch point for beta headers. The Pydantic v2 envelope on webhook
  events uses `extra="allow"` so backend additions don't break parsing.

## Reference

- `docs/ONBOARDING.md` — current state, what's built, what's pending,
  reading order.
- `CHANGELOG.md` — version-by-version narrative of every shipped slice.
- `CLAUDE.md` — toolkit-internal project memory and coding conventions.
- `docs/claude-code-integration.md` — using `cma-local-executor` as a
  Claude Code MCP server.
- `docs/api-key-storage.md` — apiKeyHelper + Get-Secret setup.
- `docs/agent-lint.md` — agent-YAML linting rule reference.
- `docs/beta-access-state.md` — `cma doctor --probe-beta` output.
