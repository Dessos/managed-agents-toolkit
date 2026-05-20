---
type: meta
status: active
created: 2026-05-20T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [agent-instructions, vault]
related: [[README]]
confidence: high
source: "plan v4.1 + hf-2026 vault CLAUDE.md pattern"
---

# Vault-agent instructions

> Read this at session start (the `SessionStart` hook injects an orientation
> based on this file + `context/ai-session-brief.md`).

You are working in a project that uses `docs/vault/` as the persistent
knowledge surface. Both the operator and other Claude sessions rely on this
vault being **truthful, current, and frontmatter-conformant**.

## Your responsibilities in this vault

1. **Frontmatter discipline.** Every `.md` you write under `docs/vault/` MUST start with the full frontmatter block (see `docs/vault/README.md` for the schema). The linter (`cma vault lint`) will catch drift. Don't bypass.

2. **One file = one decision / learning / incident.** Don't pile multiple ADRs into one file. Use `cma vault new-decision <slug>` to create the next one with correct defaults.

3. **Cite sources in `source:`.** ADRs cite the originating CHANGELOG line range (e.g., `"CHANGELOG.md:234-247"`). Learnings cite the failed test or commit hash. Knowledge cites the URL. No `source: "ai memory"` — if it's just from your context, name the conversation date.

4. **Update, don't append.** When an ADR is superseded, set `status: superseded`, fill `superseded_by:` and `superseded_reason:`, and create the new ADR — don't edit the old decision in place. History matters.

5. **Run `cma vault index` after batches.** When you add or modify multiple ADRs in one session, regenerate the `_INDEX.md` files at the end (Tier 2: this becomes automatic via a PostToolUse hook).

## When to write what (cheat sheet)

| Situation | Note type | Trigger |
|---|---|---|
| Operator and I chose X over Y after deliberation | `decision` | `cma vault new-decision <slug>` |
| A test failure revealed an invariant we hadn't named | `learning` | `cma vault new-learning <slug>` |
| Backtest / benchmark gave a clear result | `evaluation` | `cma vault new-evaluation <slug>` |
| Production hiccup — even local — that we should not repeat | `incident` | `cma vault new-incident <slug>` |
| Operator's current sprint focus changed | (edit) `context/ai-session-brief.md` |
| Found a reference URL we'll cite again | `cma vault refresh-knowledge --page <slug> --force` |

## Don't write ADRs for

- Bug fixes that restore documented behavior (it's in CHANGELOG, not an ADR).
- Lint / formatting / dependency bumps.
- Tests added for existing behavior.
- "Code cleanup" with no behavioral change.

**Bar**: would a future operator (or future-you) regret not knowing this was deliberated? Yes → ADR. No → CHANGELOG bullet is enough.

## The Hook 2 gate (anti-decay)

When you stage a `CHANGELOG.md` diff that adds a new `### Decisions baked in` bullet, the PreToolUse hook on `git commit` **will block** unless a `docs/vault/decisions/*.md` is also staged. The error message names the missing bullet. Don't bypass unless the operator explicitly approves.

## The Stop hook (vault-write-reminder)

When you finish responding, a Stop hook scans the transcript for architectural keywords (`architecture`, `design decision`, `invariant`, `contract`, `trade-off`, `commitment`, `rationale`, `deliberated`). If ≥2 distinct matches AND no vault file was written this session, it blocks Stop with a reminder. **This is intentional — propose an ADR when in doubt.** The operator can `CMA_VAULT_STOP_BYPASS=1` to bypass.

## Bypass discipline

If you find yourself wanting to bypass:

1. Pause. Ask the operator if a real ADR / learning is warranted.
2. If genuinely not (e.g., fixing a lint warning), use the most specific bypass: `CMA_VAULT_COMMIT_BYPASS=1` or `CMA_VAULT_STOP_BYPASS=1`. Never the master `CMA_VAULT_BYPASS=1` unless the operator asks.
3. Bypasses are telemetry-logged. Frequent bypass = mis-calibrated rule = file a learning about it.

## How this vault relates to `CLAUDE.md` at the repo root

- Repo `CLAUDE.md`: project-level rules (Architectural commitments, non-negotiable rules, coding conventions, quick commands).
- This file: vault-specific agent instructions, read at session start.

They overlap intentionally — both reach you. Don't be confused by duplication.

## Templating

When this vault is scaffolded into a consumer project via `cma project init --with-vault`, this CLAUDE.md travels along. The substitutions (`{{PROJECT_NAME}}`) apply only to README + session-brief, not to this file — the agent instructions are universal.
