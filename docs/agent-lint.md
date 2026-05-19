# `cma agent lint`

Static validator for agent YAML definitions. Runs locally, makes no API calls, finds the kinds of bugs that quietly cost money or block caching.

## Quickstart

```bash
# Lint a single file
cma agent lint .managed-agents/agents/reviewer.yaml

# Lint a whole directory (recursive)
cma agent lint .managed-agents/agents/

# Lint default targets:
#   1. .managed-agents/agents/ if it exists
#   2. otherwise bundled toolkit templates
cma agent lint
```

Exit code equals the count of ERROR findings (capped at 125). Use it in CI.

## What gets checked

| Rule | Default level | Concern | Example trigger |
|---|---|---|---|
| **R001** | ERROR | description is empty | `description:` blank |
| **R002** | WARNING | description < 30 chars | `description: "reviewer"` |
| **R003** | ERROR | description == name | `name: reviewer / description: reviewer` |
| **R004** | INFO | description doesn't start with an action verb | `description: "Tool for reviewing..."` |
| **R005** | WARNING | system prompt below the model's cache-engagement threshold | <4096 tokens on Opus, <1024 on Sonnet |
| **R006** | ERROR | model not in `cma.core.pricing.PRICING_TABLE` | `model: claude-opus-9-9` |
| **R007** | ERROR | custom tool description < 50 chars | tool description = "Fetches stuff" |
| **R008** | WARNING | custom tool name lacks namespace separator | `name: fetch` (use `yourproj_fetch`) |
| **R009** | WARNING | custom tool description / schema contains a timestamp | `... as of 2026-05-19 ...` |
| **R010** | WARNING | `metadata.cma_template` set but `cma_template_version` missing | template metadata block missing version |

## Why R005 matters most

Anthropic's prompt cache silently no-ops when the input is below the per-model minimum (Opus 4096, Sonnet 1024, Haiku 2048 — verify against current docs when prices change). A 3,500-token Opus system prompt looks like a saving, but every session pays the full input rate because the cache never engages. R005 catches this at lint time. The plan estimates a ~10× cost differential between cached and uncached sessions for stable system prompts.

## Why R009 matters more than it looks

Tool definitions cache stricter than system prompts: a single timestamp anywhere in a tool description or input schema invalidates the *entire* tool block across sessions. Static detection (`\d{4}-\d{2}-\d{2}`, `datetime.now(`, `new Date(`, `time.time(`) catches the common slips. Move dynamic values into arguments at call time, not the definition.

## Tuning severity

Severity per rule is configured in `src/cma/core/lint.py` via `RULE_SEVERITY`. Three reasonable presets — pick one and adjust:

| Preset | Rules at ERROR |
|---|---|
| STRICT | every rule except R004 (INFO) and R010 (WARNING) |
| MIDDLE (current default) | R001, R003, R006, R007 |
| LENIENT | R001, R003, R006 |

Reasoning trade-off: STRICT enforces the toolkit's "cache-aware, IP-defensive" tagline at the cost of friction. LENIENT lets through anything you wouldn't catch in code review. MIDDLE bans the lazy mistakes (missing/duplicate descriptions, unknown models, undocumented tools) but leaves the cache-hygiene rules as warnings the operator can override per-agent.

## Programmatic use

```python
from cma.core.agent_spec import load_agent_spec
from cma.core.lint import lint_agent_spec

spec = load_agent_spec("path/to/agent.yaml")
result = lint_agent_spec(spec)
for finding in result.findings:
    print(f"{finding.rule_id} [{finding.level.value}] {finding.location}: {finding.message}")
if not result.ok:
    raise SystemExit("lint failed")
```

`LintResult.errors`, `.warnings`, `.infos` slice findings by severity; `.ok` is `True` iff there are no errors.

## Adding a new rule

1. Add `_rule_RXYZ_short_name(spec: AgentSpec) -> list[Finding]` to `cma.core.lint`.
2. Register it in `ALL_RULES`.
3. Add its default severity to `RULE_SEVERITY`.
4. Write a test class `TestRXYZShortName` in `tests/unit/test_agent_lint.py` with at least one fail-case and one clean-case.
5. Update the rule table above.

A rule must be independent of severity — return `Finding` with any sensible level; the orchestrator rewrites it from `RULE_SEVERITY`.
