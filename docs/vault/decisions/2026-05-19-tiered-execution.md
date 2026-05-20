---
type: decision
status: active
created: 2026-05-19T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [adr, architecture, ip-boundary, philosophy]
related: [[2026-05-19-operator-authored-mcp-bridge]]
confidence: high
source: "CHANGELOG.md v0.1.0 'Decisions' section"
---

# Tiered execution — cloud orchestrates, local executes proprietary code

## Context

The toolkit wraps Anthropic's Claude Managed Agents beta to drive long-running, agentic workflows across multiple consumer projects. Those consumer projects own proprietary IP — model weights, customer datasets, internal pricing models, scraped corpora, proprietary algorithms. The architectural question at v0.1.0 was: **where does that proprietary code run, and what does the cloud agent see?**

Three constraints applied:

- **IP non-leakage** — operators must be able to use Managed Agents without exposing the IP that makes their consumer project valuable.
- **Single source-of-truth for orchestration** — the cloud agent needs enough context to make decisions; if it can't see the structure of the work, it can't drive it.
- **No bespoke runtime per project** — the toolkit ships once and serves any consumer; baking in consumer-specific execution paths is a non-starter.

## Options considered

1. **All-cloud execution** — bundle the consumer's code into the cloud agent's environment, run it remotely. Cleanest from an orchestration standpoint; **rejected** as it would mean every consumer ships their IP to Anthropic-managed infrastructure.
2. **All-local execution** — Anthropic's Managed Agents drive prompts only; all execution happens in operator-controlled subprocesses with no remote orchestration of structured work. **Rejected** as it gives up the cloud agent's planning + coordination capability.
3. **Tiered: cloud orchestrates, local executes** — the cloud agent calls MCP tools that route execution to a local daemon. The cloud sees tool schemas + inputs + outputs but never sees the proprietary code or raw data. **Chosen.**

## Decision

**Cloud agents (Anthropic Managed Agents) orchestrate; the operator-side `cma-local-executor` MCP daemon executes proprietary code. The cloud agent only sees tool names + descriptions + the declared `output_summary` metrics that handlers explicitly return.**

This was operator-confirmed during the first design round and is codified as CLAUDE.md Architectural Commitment #2.

## Rationale

- The MCP protocol is **purpose-built** for this — tools as the boundary, JSON-RPC as the wire format, structured I/O at the seam.
- The cloud agent retains enough surface (tool inputs, tool outputs, summary metrics) to make routing decisions across multiple jobs.
- Proprietary code, raw data, and parameter values **never enter the cloud agent's context** unless the operator's adapter explicitly puts them in the returned dict.
- The same boundary supports both modes: Max-plan-only operator (Claude Code as MCP client) and future operator-with-API-credit (Anthropic Managed Agents as remote MCP client). One commitment, two execution paths.

## Trade-offs accepted

- **Latency floor**: every cross-boundary call is a JSON-RPC round-trip plus subprocess spawn (subprocess per job, see [[2026-05-19-subprocess-per-job]]). For long-running compute this is rounding noise; for chat-fast iteration it would be painful (not the target workload).
- **Operator authorship burden**: every consumer project must author its own MCP bridge config + handlers. No turnkey "drop in your code" path. Acceptable because the alternative (permissive defaults) leaks IP, see [[2026-05-19-operator-authored-mcp-bridge]].
- **Schema discipline**: handlers must explicitly declare what crosses the boundary via `output_summary` — easy to under-declare ("I forgot to return that metric"); the toolkit treats this as the operator's contract to maintain.

## Revisit trigger

Revisit if:
- Anthropic ships a "trusted execution" tier where the cloud agent can run operator code in a confidentially-attested enclave.
- A consumer project's IP is genuinely public — then the tiered split is unnecessary overhead (just inline).
- MCP loses its position as the canonical seam (unlikely near-term).

## Related

- [[2026-05-19-operator-authored-mcp-bridge]] — the corollary commitment about bridge defaults
- [[2026-05-19-subprocess-per-job]] — the execution-isolation mechanic that operationalizes the IP boundary
- [`src/cma/executor/server.py`](../../../src/cma/executor/server.py) — the executor MCP daemon
- `docs/ONBOARDING.md` §"IP boundary" — operator-facing explanation
- `CLAUDE.md` Architectural Commitment #2
- `CHANGELOG.md:255-282` — v0.1.0 release notes
