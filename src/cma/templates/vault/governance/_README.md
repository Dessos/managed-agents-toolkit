---
type: meta
status: active
created: 2026-05-20T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [readme, governance, vault]
related: [[../../../CLAUDE.md]]
confidence: high
source: "CLAUDE.md (toolkit-internal project memory)"
---

# `governance/` — Non-negotiable rules + escalation procedures

> Rules that bind agents and operators alike. Each rule is its own file so
> we can reference it precisely, evolve it deliberately, and never lose its
> rationale.

## Relationship to `CLAUDE.md`

The repo-root `CLAUDE.md` is the *load-bearing* version — it's what every Claude session reads on startup. This folder is for:

1. **Per-rule files** that explain the *why* of a rule (CLAUDE.md is space-constrained; a governance file can spend 300 lines explaining a one-line rule).
2. **Procedures** that need precise reference (e.g., "Incident response runbook", "Secret rotation").
3. **Mirrors** of CLAUDE.md non-negotiable rules so they can be cited by ADRs (you can wikilink `[[non-negotiable-no-secrets]]` from an ADR; you can't wikilink into CLAUDE.md).

When CLAUDE.md and a governance file disagree, **CLAUDE.md wins** — it's the authority. Update both in the same commit.

## When to add a file here

- A CLAUDE.md rule gets cited often and needs its own discussion space.
- A new operational procedure needs to be written down (incident response, secret rotation, budget breach drill).
- A boundary commitment (like the IP boundary) deserves a long-form explanation.

## What this folder is NOT for

- Aspirational rules ("we should do X") — these go in learnings until proven.
- Sprint or task lists — these go in `context/current-priorities.md`.
- Architectural choices — these go in `decisions/`.

## Filename convention

Descriptive kebab-case, no dates (governance rules are timeless; their evolution shows in git history). E.g., `ip-boundary.md`, `secret-handling.md`, `incident-response.md`.

## Status lifecycle

Mostly `active`. `superseded` if a rule is replaced by a stronger or weaker version (set `superseded_by:` and link the replacement).

## Initial contents

Empty for now — populate as the operator finds CLAUDE.md rules they want to expand. Candidates from CLAUDE.md "Non-negotiable rules":
- `no-secrets-committed.md`
- `telemetry-redaction.md`
- `beta-headers-discipline.md`
- `ip-boundary-commitment.md`
