---
type: decision
status: active
created: 2026-05-19T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [adr, architecture, patterns, executor, ip-boundary]
related: [[2026-05-19-tiered-execution]]
confidence: high
source: "CHANGELOG.md v0.2.0 'Decisions baked in' section"
---

# Hybrid YAML + Python adapter — contract in YAML, callables in Python

## Context

The `cma-local-executor` exposes a small set of MCP tools (`submit_job`, `get_job_status`, etc.) to the cloud agent. The cloud agent supplies a `spec_ref` (a slug like `compute-stats`) and an `inputs` dict; the local daemon must dispatch the right Python function with validated inputs and return a structured result.

The architectural question at v0.2.0: **where is the contract between cloud and local defined?**

Two natural options:

1. Embed everything in Python — the spec is a decorator, the handler is a function, the schema is inferred from type hints.
2. Split it — YAML declares the contract (input schema, allowed values, summary keys); Python provides the callable.

The choice affects what the cloud agent gets to see, how easy it is to validate inputs before dispatching, and how the IP boundary holds.

## Options considered

1. **Pure-Python decorator-based registration** — handlers self-register via `@register_handler("compute-stats", input_schema=...)`. Spec lives in code. **Rejected**: the contract is invisible to anyone reading the cloud agent's tool list — they'd need to read consumer Python to know what inputs are allowed. Bad for the IP boundary.
2. **Pure-YAML with reflection** — YAML declares the function path and schema; the daemon imports and dispatches. **Rejected**: too magic; rename-a-function-break-the-system; no IDE support for the contract→implementation link.
3. **Hybrid: YAML for contract, Python for callables** — `job_specs/*.yaml` declares spec_ref, input schema, allowed values, output_summary keys; `adapters/local_executor.py` has a `JOB_HANDLERS = {"compute-stats": compute_stats_handler}` dict mapping spec_ref → callable. **Chosen.**

## Decision

**Job specs are declared in `.managed-agents/job_specs/<spec_ref>.yaml` (input schema + validation rules + summary keys). Handlers are declared in `.managed-agents/adapters/local_executor.py` via the `JOB_HANDLERS: dict[str, Callable]` mapping. The cloud agent sees only the YAML's `spec_ref` + `output_summary` keys via the MCP tool descriptions — it never reads operator Python.**

Operator-confirmed during v0.2.0 design.

## Rationale

- **The YAML is the boundary**: it's what `cma agent lint` validates, what the bridge probe can introspect, what the cloud agent sees in tool descriptions. The Python adapter is the implementation detail.
- **Schema enforcement at the daemon edge**: the spec validates inputs before dispatching; the cloud agent gets a clear error if it passes an invalid input shape. No Python TypeError-at-call-time noise.
- **IP boundary preserved**: the cloud agent never gets the Python code; only the declared contract.
- **Operator authoring ergonomics**: YAML is fast to write/edit/diff; Python is where the actual logic lives. They're optimized for different concerns.

## Trade-offs accepted

- **Two files per handler** — one YAML, one Python entry. Costs 30 seconds when scaffolding; saves an hour of debugging when the cloud agent is mysteriously not seeing the handler.
- **Duplication risk** — input types live in both files (YAML schema + Python type hints). Mitigation: the daemon validates inputs against YAML and only YAML; type hints are documentation.
- **No type-driven schema generation** — we don't infer YAML from Python type hints. Doing so would tightly couple them and require regen tooling. Cost accepted in favor of explicitness.

## Revisit trigger

Revisit if:
- A consumer project grows past ~30 handlers and the YAML/Python duplication becomes painful — then a `cma agent generate-spec <handler>` could derive YAML from type hints, with the Python remaining authoritative.
- Anthropic adds a "structured tools" feature to MCP that makes our YAML schema redundant.

## Related

- [[2026-05-19-tiered-execution]] — the IP boundary this enforces
- [[2026-05-19-subprocess-per-job]] — the execution mechanic
- [`src/cma/executor/spec.py`](../../../src/cma/executor/spec.py) — `JobSpec` Pydantic model + YAML loader
- [`src/cma/executor/adapter.py`](../../../src/cma/executor/adapter.py) — `JOB_HANDLERS` discovery + validation
- [`src/cma/templates/starter/job_specs/compute-stats.yaml`](../../../src/cma/templates/starter/job_specs/compute-stats.yaml) — reference example
- `CHANGELOG.md:216-247` — v0.2.0 release notes
