---
type: learning
status: active
created: {{CREATED}}
updated: {{UPDATED}}
tags: [learning]
related: []
confidence: medium
source: "{{SOURCE}}"
---

# {{TITLE}}

## What happened

The concrete event — failure, surprise, unexpected behavior — that prompted
this note. Cite the commit / test / log line where applicable.

## What was learned

The generalized insight. Phrased so future-us can apply it without re-reading
the entire context.

## Why it surprised us

What model of the world did we hold that turned out to be wrong? Naming the
broken assumption is the most valuable part of a learning.

## Applies to

Where the insight is load-bearing. Could be a module, a class of bug, an
operator workflow. The narrower the scope, the more reliable the lesson.

## Mitigation

Concrete steps now in place (or that should be) so we don't pay this lesson
again. Link to code, tests, or ADRs that operationalize the lesson.

## Confidence

- **high** — confirmed by ≥2 independent observations / reproduced
- **medium** — single observation, plausible mechanism
- **low** — hunch with weak evidence; revisit before relying on it

## Related

- [[adr-that-this-informs]]
- `path/to/failing/test.py`
