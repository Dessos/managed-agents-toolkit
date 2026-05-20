---
type: context
status: active
created: 2026-05-20T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [priorities, pending, todo]
related: [[ai-session-brief]]
confidence: high
source: "operator-authored; scaffolded by `cma project init --with-vault`"
---

# Current priorities — {{PROJECT_NAME}}

> Operator-curated. Live list of unblocked work + known blockers. Update
> when state changes.

## In progress

_Replace this section with what's actively being worked on right now._

## Unblocked (next up)

_What's ready to be picked up immediately. Order matters — first item is the next thing._

## Blocked

_What can't progress yet. Each item should say WHY it's blocked + WHAT would unblock it._

## Recently completed

_Last 5-10 things shipped. Helps cold-start agents see the current direction._

## Update discipline

When a slice completes, mark it done in this file ⬆ AND in `ai-session-brief.md`. The Hook 4 (`check_session_writes`) Stop-rule treats unwritten architectural conversations as a problem — this file is one place those writes land.
