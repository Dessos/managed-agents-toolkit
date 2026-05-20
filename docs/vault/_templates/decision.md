---
type: decision
status: active
created: {{CREATED}}
updated: {{UPDATED}}
tags: [adr]
related: []
confidence: high
source: "{{SOURCE}}"
# Optional:
# reviewed_on: {{REVIEW_DATE}}
# supersedes: [filename.md, ...]
# superseded_by: filename.md
---

# {{TITLE}}

## Context

What problem or question prompted this decision? What constraints applied?
Why was a decision needed at this moment?

## Options considered

1. **Option A** — pros / cons
2. **Option B** — pros / cons
3. **Option C** — pros / cons

## Decision

The chosen path, stated as the operative verb (e.g., "We use subprocess per
job, not in-process tasks"). One sentence.

## Rationale

Why this option won. The evidence, the principle, or the constraint that
tipped the balance. Be honest about what was conjecture.

## Trade-offs accepted

What we gave up by choosing this. Future-us will be tempted to revisit if
these costs grow — name them so the revisit has a clear trigger.

## Revisit trigger

Concrete signal that would warrant re-opening this decision. E.g.,
"if the executor pool size > 50", "if the operator adds API credit",
"if Claude Code drops `mcp_tool` hook support".

## Related

- [[other-adr-name]]
- `path/to/code/that/embodies/this.py`
- CHANGELOG line range that documented this at release time
