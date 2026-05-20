---
type: decision
status: active
created: 2026-05-19T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [adr, architecture, security, ip-boundary, philosophy]
related: [[2026-05-19-tiered-execution]]
confidence: high
source: "CHANGELOG.md v0.1.0 'Decisions' section"
---

# Operator-authored MCP bridge — no permissive defaults

## Context

[[2026-05-19-tiered-execution]] commits that proprietary code runs locally and the cloud agent never sees it. That commitment depends on a question one layer down: **what MCP tools does the cloud agent get to call, and who decides?**

The toolkit could either:
- ship a generous default set of tools (filesystem, shell, network, etc.) the cloud agent could use out of the box, OR
- ship nothing by default and require the operator to write the bridge config that names which tools to expose.

The choice has direct security implications. A permissive default would let any cloud-agent prompt issue filesystem reads or shell commands, exfiltrating data the IP-boundary commitment was meant to protect.

## Options considered

1. **Permissive defaults** — ship a default `local_mcp_bridge.yaml` with common tools (filesystem read/write, shell, env access). Operators get a working setup immediately; can lock down later. **Rejected** — the default IS the configuration; "lock down later" never happens in practice.
2. **No defaults; ship schema + audit log only** — operator writes their own `.managed-agents/local_mcp_bridge.yaml` from scratch, naming exactly which tools to expose and to which servers. The toolkit provides `cma bridge lint` to validate shape. **Chosen.**
3. **Fail-closed gated default** — ship a default but require explicit `--accept-defaults` flag. **Rejected** — same as #1 once operators get tired of the flag.

## Decision

**The toolkit ships the bridge schema validator and the audit log only. Operators author `.managed-agents/local_mcp_bridge.yaml` themselves; nothing is exposed to the cloud agent by default.**

Codified as CLAUDE.md Architectural Commitment #3.

## Rationale

- **Defaults are documentation**: whatever ships as default IS what most operators run. "Configurable" is theory; default is practice.
- **The threat model is real**: the cloud agent runs untrusted prompts; any tool it can invoke is a potential exfiltration path.
- **Operator authorship is a forcing function**: writing the bridge config makes the operator think about *what each tool is for* before exposing it.
- **`cma bridge lint` + `cma bridge probe` give back ergonomics**: schema validation + live MCP-server probing close the "I didn't know I broke it" gap that explicit configs normally suffer.

## Trade-offs accepted

- **Higher first-time-setup cost** — operator must author their bridge config before any cloud session works. The README's `cma project init --with-example` mitigates this with a starter; the operator still has to declare which servers to bridge.
- **Documentation burden** — `docs/claude-code-integration.md` exists because of this commitment; without it operators flail. Mitigation: the doc is maintained as part of the toolkit's commitment.
- **No "just try it" demo** — there is no zero-config demo. Acceptable: the trust model deserves the friction.

## Revisit trigger

Revisit if:
- A future MCP / Managed Agents version introduces verifiable execution isolation that makes permissive defaults safe.
- Anthropic publishes a managed allow-list of "safe" MCP tools with strong guarantees.
- We learn that operators are reaching for community-published bridge configs without auditing them — that would mean the friction we wanted is being routed around, and we'd need a different mechanism (e.g., signed bridge configs).

## Related

- [[2026-05-19-tiered-execution]] — the parent commitment this corollary serves
- [`src/cma/executor/bridge_config.py`](../../../src/cma/executor/bridge_config.py) — the Pydantic schema for `local_mcp_bridge.yaml`
- [`src/cma/executor/bridge_probe.py`](../../../src/cma/executor/bridge_probe.py) — `cma bridge probe` deep validator
- `docs/claude-code-integration.md` — operator-facing setup walkthrough
- `CLAUDE.md` Architectural Commitment #3
- `CHANGELOG.md:255-282` — v0.1.0 release notes
