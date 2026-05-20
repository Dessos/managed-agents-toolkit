---
type: incident
status: active
created: {{CREATED}}
updated: {{UPDATED}}
tags: [incident, postmortem]
related: []
confidence: high
source: "{{SOURCE}}"
---

# {{TITLE}}

## Summary

One paragraph: what broke, what was the impact, how long it lasted, who
noticed.

## Timeline

UTC timestamps. Detection → diagnosis → fix → confirmation. Be specific
about what was tried and what was tried after each step failed.

| Time (UTC) | Event |
|---|---|
| HH:MM | Symptom first observed |
| HH:MM | First hypothesis tested |
| HH:MM | Root cause confirmed |
| HH:MM | Fix deployed |
| HH:MM | Recovery confirmed |

## Root cause

The actual underlying defect, not the proximate symptom. Phrased so the fix
is obviously responsive.

## Resolution

Exactly what was changed. Link to commits or PRs.

## What worked

Detection mechanisms, runbooks, tools that helped. Reinforce these.

## What didn't work

Detection gaps, dead ends, missing signals. File learnings for each.

## Prevention

Concrete changes — tests, alerts, lint rules, ADR updates — that should
prevent or detect this class of issue going forward. Track each as a TODO
in `context/current-priorities.md` until landed.

## Related

- [[learning-from-incident]]
- commit hash of fix
- alerting / monitoring change
