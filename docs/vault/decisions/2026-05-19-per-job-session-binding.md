---
type: decision
status: active
created: 2026-05-19T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [adr, architecture, notifications, executor, security]
related: [[2026-05-19-subprocess-per-job]]
confidence: high
source: "CHANGELOG.md v0.5.1 notifier section 'Decisions baked in'"
---

# Per-job session binding — notifications to the originating client, not broadcast

## Context

`cma executor` dispatches `notifications/cma/job_status_changed` to MCP clients when a job reaches a terminal state. The architectural question at v0.5.1: **which client(s) get the notification?**

Two natural choices:

1. **Broadcast** to every connected MCP session — simple, always notifies whoever cares.
2. **Per-job session binding** — record `(job_id → originating session)` at `submit_job` time; dispatch only to that session on terminal state.

The current operator path has only one connected client at a time (Claude Code over stdio), so the choices are observably equivalent today. But the future HTTP path has Anthropic Managed Agents cloud agents AND Claude Code potentially attached to the same `cma executor serve` simultaneously, each running their own jobs.

## Options considered

1. **Broadcast to all sessions** — every connected MCP client sees every job's terminal notification. **Rejected**: leaks job IDs and status transitions across clients that didn't submit them; future cross-client information leak.
2. **Per-job session binding** — record the session at submit time; deliver only to the originating one. **Chosen.**
3. **Per-project scoping** — group sessions by project; broadcast within the group. **Rejected**: project boundaries are themselves a permission model we don't have yet; over-engineering for the current need.

## Decision

**`FastMCPSessionNotifier` records `(job_id → ServerSession)` at `submit_job` time and dispatches `notifications/cma/job_status_changed` only to that originating session on terminal state. The binding is consumed on first notification attempt — even if the send drops (no event loop, transport down, session torn down).**

This mirrors how `get_job_result` already requires the caller to know the `job_id` — both maintain the invariant that knowledge-of-a-job-id is the access primitive.

Operator-confirmed in the v0.5.1 notifier design round.

## Rationale

- **Symmetry with `get_job_result`**: that tool already implicitly scopes job results to whoever knows the `job_id`. Notifications follow the same scope.
- **Cross-client leak prevention**: when Claude Code + Managed Agents are concurrently attached (future HTTP path), neither sees the other's job IDs.
- **Simpler than retry**: consuming the binding on first attempt removes the "retry on transport failure" branch that would otherwise complicate testing and reasoning about delivery guarantees.
- **Notifications are a hint, polling is the contract**: `get_job_status` / `get_job_result` remain the authoritative path. The notifier is a courtesy; losing one doesn't break the client (they poll).

## Trade-offs accepted

- **No notification on broken transport** — if the originating session drops between `submit_job` and the terminal-state event, no client learns about completion until they poll. Acceptable: polling is the contract.
- **No "subscribe to all jobs" surface** — a future "operator dashboard" that wants to see every job's status would have to poll the local SQLite store or subscribe via a separate channel (not this notifier). Acceptable: the broadcast variant can be added later as a separate explicit subscription.
- **Custom-method JSON-RPC notification** — `notifications/cma/job_status_changed` lives outside MCP's `ServerNotificationType` union; we instantiate `Notification[dict, str]` directly. Means we don't get a Pydantic subclass for it; means we depend on FastMCP's `model_dump`-based wire encoding. Tested.

## Revisit trigger

Revisit if:
- We add a dashboard / operator UI that needs cross-job visibility — that's a separate subscription surface, not a change to this notifier.
- Anthropic adds a managed cross-client notification channel that supersedes our custom-method approach.
- We observe operators submitting jobs from one client and expecting another to be notified (signal that the chosen scope is wrong for some workflow).

## Related

- [[2026-05-19-subprocess-per-job]] — the execution model whose terminal states trigger these notifications
- [[2026-05-19-observe-first-budget-policy]] — sibling v0.5.1 decision, also operator-confirmed
- [`src/cma/executor/notifications.py`](../../../src/cma/executor/notifications.py) — `FastMCPSessionNotifier` + `NullNotifier` + `CapturingNotifier`
- [`src/cma/executor/server.py`](../../../src/cma/executor/server.py) — `ExecutorServer._submit_job` + ctx-binding
- `CHANGELOG.md:47-58` — v0.5.1 notifier section release notes
- 11 unit tests in `tests/unit/test_executor_session_notifier.py`
