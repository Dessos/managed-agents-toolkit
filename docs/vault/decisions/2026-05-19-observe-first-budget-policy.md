---
type: decision
status: active
created: 2026-05-19T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [adr, security, budget, webhook, philosophy]
related: [[2026-05-19-per-job-session-binding]]
confidence: high
source: "CHANGELOG.md v0.5.1 webhook section 'Decisions baked in'"
---

# Observe-first budget policy — NOTIFY_ONLY default, kill_on_breach as the master switch

## Context

`cma webhook serve` receives Anthropic budget-event webhooks (e.g., `session.status_idled` with usage). On a breach, the toolkit needs to decide what to do: ignore, notify, cancel the session, or cancel the entire project's sessions.

Two failure modes to avoid:
- **Runaway spend**: doing nothing while breaches accumulate; the bill shows up later.
- **Trigger-happy cancellation**: killing in-flight sessions on first breach without observing whether the breach is real or spurious.

The operator is new to Managed Agents at v0.5.1; observing actual breach patterns before adopting destructive defaults is the prudent first move.

## Options considered

1. **Hard cancel on first breach** — any breach → `CANCEL_SESSION`. Safest from a budget standpoint, most destructive. **Rejected**: prematurely; we don't know what breaches actually look like in practice.
2. **Always-ignore** — log breaches but never act. Cheapest. **Rejected**: this is just "no policy", which is what we're trying to avoid.
3. **Observe-first with toggle** — default to `NOTIFY_ONLY` (log + telemetry), but expose `BudgetConfig.kill_on_breach` so the operator can flip to `CANCEL_SESSION` once they've observed the pattern. **Chosen.**
4. **Cancel project-wide** (`CANCEL_PROJECT` or `EXHAUST_BUDGET`) — kill all sibling sessions of the breached project. **Explicitly rejected** as collateral damage; strategy sessions are independent experiments.

## Decision

**Default webhook policy is observe-first: under-cap → `IGNORE`, over-cap → `NOTIFY_ONLY` (telemetry log + warning). `BudgetConfig.kill_on_breach` is the master switch — set to `True` to upgrade `NOTIFY_ONLY` → `CANCEL_SESSION` on breach. `CANCEL_PROJECT` and `EXHAUST_BUDGET` are NEVER returned — sibling sessions are independent experiments and not collateral.**

Operator-confirmed during v0.5.1 design.

## Rationale

- **Observe-first respects the unknown**: we don't yet know how often breaches are spurious vs real, how the cap interacts with cache refunds, or how Anthropic's webhook timing aligns with cap windows. Telemetry gives data; cancellation removes the experiment.
- **One toggle keeps the policy legible**: operator reads `BudgetConfig.kill_on_breach` and knows the full behavior. No matrix of "what does X+Y mean".
- **Excluding `CANCEL_PROJECT` / `EXHAUST_BUDGET` from the menu** prevents accidental misconfiguration — the policy literally cannot select them. Strategy sessions are designed as independent experiments; cross-cancellation defeats the design.
- **Stub raises rather than no-ops**: `NotImplementedError` surfaces as HTTP 500 + telemetry line `policy_not_implemented`. A silent default (e.g., always-IGNORE) would let runaway spend through without the operator noticing.

## Trade-offs accepted

- **Latency on cancellation** — once `kill_on_breach=True` is flipped, the breach-to-cancel path goes Anthropic-webhook → cma receiver → `Actions.cancel_session` → Anthropic SDK call. Each leg is ~50-500ms; first breach to actual cancellation is on the order of 1-2 seconds. Acceptable.
- **Operator must opt in to destructive behavior** — default is observational only. Mitigation: documented in `docs/api-key-storage.md` and CHANGELOG; operator picks the right moment based on their own observation.
- **Sibling sessions might also be over budget at the same time** — they each get their own breach event, each evaluated independently. Acceptable: each session is its own experiment.

## Revisit trigger

Revisit if:
- Operator observes systemic over-spending where independent per-session cancellation isn't fast enough — then `CANCEL_PROJECT` might earn a place in the menu (with operator opt-in).
- Anthropic changes webhook semantics (e.g., breach events fire on a different cadence).
- We add a "budget rollover" feature where short-window breach is OK if the long-window stays under cap.

## Related

- [[2026-05-19-per-job-session-binding]] — companion notifier decision in the same v0.5.1 release
- [`src/cma/webhook/policy.py`](../../../src/cma/webhook/policy.py) — `kill_switch_policy()` + `KillSwitchAction` enum
- [`src/cma/webhook/receiver.py`](../../../src/cma/webhook/receiver.py) — webhook FastAPI factory + dispatch
- CLAUDE.md Non-negotiable rule #5 — "Budget enforced structurally"
- `CHANGELOG.md:60-78` — v0.5.1 webhook section release notes
