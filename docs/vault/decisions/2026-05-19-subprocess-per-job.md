---
type: decision
status: active
created: 2026-05-19T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [adr, architecture, patterns, executor]
related: [[2026-05-19-tiered-execution]]
confidence: high
source: "CHANGELOG.md v0.2.0 'Decisions baked in' section"
---

# Subprocess per job — strong isolation, accepted overhead

## Context

The `cma-local-executor` MCP daemon dispatches operator-authored handler functions in response to `submit_job` calls. The architectural question at v0.2.0 was **how those handlers should actually execute**: in-process tasks in the daemon's event loop, or in fresh subprocesses spawned per job?

The choice has consequences for:

- **Isolation**: a handler that imports a module with side effects (logging config, signal handlers, monkey-patches) can leak that state to subsequent jobs if they share a process.
- **Resource cleanup**: handlers may leak file descriptors, threads, GPU memory, etc. Subprocess termination cleans those reliably; in-process cleanup is best-effort.
- **Failure containment**: a handler that segfaults or hits `os._exit` brings down the daemon if in-process; a subprocess crash only fails one job.
- **Overhead**: each subprocess spawn is on the order of 50-300ms depending on platform and import cost.

## Options considered

1. **In-process `asyncio.Task`** — handlers awaited in the daemon's event loop. Lowest overhead, simplest model, **rejected** because isolation guarantees are weak and one bad import poisons subsequent jobs.
2. **In-process thread pool** — handlers run in `concurrent.futures.ThreadPoolExecutor`. Better than `asyncio.Task` for blocking handlers but still shares the daemon's Python process (GIL, imports, signals). **Rejected** for the same isolation reason.
3. **Subprocess per job** via `asyncio.create_subprocess_exec` — fresh `python -m cma.executor.worker` per `submit_job`. JSON over stdin/stdout for I/O. **Chosen.**
4. **Process pool reuse** — pool of warm worker processes that survive multiple jobs. **Rejected** because warmth means shared state, defeating the isolation rationale.

## Decision

**Each `submit_job` invocation spawns a fresh subprocess via `asyncio.create_subprocess_exec`. The child runs `python -m cma.executor.worker`, imports the operator's adapter (with `workspace_root` on `sys.path` for transitive imports), runs the handler via `asyncio.run`, and writes a JSON envelope to stdout.**

Operator-confirmed during the v0.2.0 design round.

## Rationale

- **Strong isolation is a CLAUDE.md non-negotiable**: PIT (point-in-time) enforcement and other module-load side effects must not leak between jobs.
- **Failure containment matters more than throughput**: this isn't a high-QPS service — it's an executor for long-running analytical / compute work. A 100ms spawn cost on a 30-minute job is rounding noise.
- **SIGTERM → 5s grace → SIGKILL** is a clean termination story; in-process equivalents require cooperative cancellation.
- **The wire format is testable**: stdin/stdout JSON is the same protocol the integration tests already exercise via subprocess fixtures.

## Trade-offs accepted

- **Spawn overhead** — ~50-300ms per job, plus adapter import cost. Acceptable for analytical workloads; would be wrong for chat-fast iteration (not the target).
- **Result serialization constraint** — handlers must return JSON-serializable dicts. Numpy arrays etc. are rejected at the worker boundary with a clear error. This is enforced and tested.
- **No streaming results** — current contract is "run to completion, then return the dict". Streaming would require a different transport. Acceptable until we have a workload that actually needs streaming.

## Revisit trigger

Revisit if:
- A consumer project regularly runs >100 sub-second jobs/min where spawn overhead becomes dominant (the answer might be a CLI sub-mode that opts into in-process for that workload).
- We need streaming results (e.g., progressive backtest output) — different transport, different decision.
- We adopt a runtime where subprocess spawn is dramatically cheaper (e.g., a forking model) AND isolation guarantees can be preserved.

## Related

- [[2026-05-19-tiered-execution]] — the boundary commitment this enforces
- [[2026-05-19-hybrid-yaml-python-adapter]] — the contract the subprocess executes
- [`src/cma/executor/runner.py`](../../../src/cma/executor/runner.py) — the `asyncio.create_subprocess_exec` driver
- [`src/cma/executor/worker.py`](../../../src/cma/executor/worker.py) — the child entry point
- `CHANGELOG.md:216-247` — v0.2.0 release notes
