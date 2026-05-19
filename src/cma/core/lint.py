"""Agent YAML lint engine.

The engine is split in three layers so each can be tested in isolation:

1. **Rules** — pure functions ``_rule_<id>(spec) -> list[Finding]``. Each rule
   is independent of severity: it reports what's wrong, the policy table
   decides whether that's an error or a warning.
2. **Severity policy** — :data:`RULE_SEVERITY` maps rule_id to
   :class:`LintLevel`. This is the operator-tunable knob.
3. **Orchestrator** — :func:`lint_agent_spec` runs every rule, applies the
   severity mapping, and returns a :class:`LintResult`.

The CLI in :mod:`cma.cli.agent` is a thin wrapper around
:func:`lint_agent_spec` plus rich-formatted output.

Cache thresholds are sourced from the plan's §2.15.1 (Opus minimum 4096
tokens, Sonnet minimum 1024). They're the minimum input-token count for
``cache_control: ephemeral`` to actually engage; below this Anthropic
silently ignores the cache directive and the operator pays full input
rates on every call.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from cma.core.agent_spec import AgentSpec, CustomToolEntry
from cma.core.pricing import PRICING_TABLE

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class LintLevel(StrEnum):
    """Severity of a finding."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(slots=True, frozen=True)
class Finding:
    """One rule violation."""

    rule_id: str
    level: LintLevel
    location: str  # e.g. "description", "tools[2].name", "system"
    message: str
    suggestion: str = ""


@dataclass(slots=True)
class LintResult:
    """All findings for a single agent spec."""

    spec_name: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level is LintLevel.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level is LintLevel.WARNING]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.level is LintLevel.INFO]

    @property
    def ok(self) -> bool:
        """True when no ERROR-level findings are present."""
        return not self.errors


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum input-token count for ``cache_control: ephemeral`` to engage,
# per model family. Source: plan §2.15.1. Verify against the live docs
# when bumping pricing.py.
#
# Keys are matched as ``startswith`` prefixes so future model variants
# inherit their family's threshold without a code change.
CACHE_MIN_TOKENS: dict[str, int] = {
    "claude-opus-": 4096,
    "claude-sonnet-": 1024,
    "claude-haiku-": 2048,
}

# Tokens-per-character heuristic. Anthropic uses a BPE tokenizer; for the
# "are you near the threshold" check this approximation is accurate to
# within ~15% for English text. The lint warning gives a ±25% band so
# the imprecision never produces a false-pass.
_CHARS_PER_TOKEN = 4.0

# Pattern flagged by R009 (timestamps in tool descriptions/schemas).
# An ISO date or "now()"-style call in a tool definition typically means
# the description is being generated at agent-create time, which busts
# the tool cache on every session.
_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T?\d*"      # ISO date / datetime
    r"|datetime\.now\("            # Python
    r"|new Date\("                 # JS
    r"|time\.time\(",              # Python
    re.IGNORECASE,
)

# Verb-detection heuristic for R004. Not exhaustive — we accept any common
# action verb that an operator would plausibly write at the start of a
# description. False negatives are fine (R004 is INFO-only).
_VERB_PREFIXES = (
    "review", "analyze", "analyse", "check", "validate", "verify", "audit",
    "create", "generate", "produce", "write", "draft",
    "fetch", "retrieve", "load", "load", "extract", "ingest",
    "run", "execute", "trigger", "orchestrate", "coordinate", "dispatch",
    "summarize", "summarise", "report", "diagnose", "investigate",
    "evaluate", "grade", "score", "rank",
)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    """Rough token count using the chars/4 heuristic. See ``_CHARS_PER_TOKEN``."""
    return int(len(text) / _CHARS_PER_TOKEN)


def _cache_threshold_for(model: str) -> int | None:
    """Return the cache-engagement minimum token count for *model*, or None.

    Unknown models return ``None`` — R006 will already have errored on the
    model field, so R005 silently skips rather than double-reporting.
    """
    for prefix, threshold in CACHE_MIN_TOKENS.items():
        if model.startswith(prefix):
            return threshold
    return None


def _rule_r001_description_present(spec: AgentSpec) -> list[Finding]:
    if not spec.description.strip():
        return [Finding(
            rule_id="R001",
            level=LintLevel.ERROR,  # overridden by RULE_SEVERITY
            location="description",
            message="Agent description is empty.",
            suggestion="Add a 1-2 sentence summary of what the agent does and when to use it.",
        )]
    return []


def _rule_r002_description_too_short(spec: AgentSpec) -> list[Finding]:
    desc = spec.description.strip()
    if 0 < len(desc) < 30:
        return [Finding(
            rule_id="R002",
            level=LintLevel.WARNING,
            location="description",
            message=f"Description is only {len(desc)} chars; aim for ≥30 so cma agent list output is scannable.",
            suggestion="Expand to a full sentence describing what the agent does.",
        )]
    return []


def _rule_r003_description_equals_name(spec: AgentSpec) -> list[Finding]:
    if spec.description.strip().lower() == spec.name.strip().lower() and spec.description.strip():
        return [Finding(
            rule_id="R003",
            level=LintLevel.ERROR,
            location="description",
            message="Description is identical to the agent name — no information added.",
            suggestion="Replace with a description of behavior, not identity.",
        )]
    return []


def _rule_r004_description_lacks_verb(spec: AgentSpec) -> list[Finding]:
    desc = spec.description.strip()
    if not desc:
        return []  # R001 handled this
    first_word = desc.split()[0].lower().rstrip(".,;:")
    if not first_word.startswith(_VERB_PREFIXES):
        return [Finding(
            rule_id="R004",
            level=LintLevel.INFO,
            location="description",
            message=f"Description starts with {first_word!r}; consider leading with an action verb.",
            suggestion="Rewrite as 'Reviews ...', 'Generates ...', 'Coordinates ...', etc.",
        )]
    return []


def _rule_r005_system_sub_threshold(spec: AgentSpec) -> list[Finding]:
    threshold = _cache_threshold_for(spec.model)
    if threshold is None:
        return []
    tokens = _estimate_tokens(spec.system)
    if tokens == 0:
        return []  # empty system prompt is its own (acceptable) decision
    if tokens >= threshold:
        return []
    # Two bands: "close enough to be worth padding" vs "way under".
    pct = (tokens / threshold) * 100
    if pct >= 75:
        msg = (
            f"System prompt is ~{tokens} tokens ({pct:.0f}% of {threshold} cache threshold for {spec.model}). "
            f"Pad past {threshold} to unlock cache reads on subsequent sessions."
        )
        suggestion = "Add concrete examples, edge cases, or guidance — keep the additions stable across sessions."
    else:
        msg = (
            f"System prompt is ~{tokens} tokens, well below the {threshold}-token cache threshold for {spec.model}. "
            f"Caching will NOT engage; every session pays full input rates."
        )
        suggestion = (
            "If this is intentional (cheap one-shot agent), document it in metadata. "
            "Otherwise expand the prompt past the threshold."
        )
    return [Finding(
        rule_id="R005",
        level=LintLevel.WARNING,
        location="system",
        message=msg,
        suggestion=suggestion,
    )]


def _rule_r006_model_unknown(spec: AgentSpec) -> list[Finding]:
    if spec.model not in PRICING_TABLE:
        return [Finding(
            rule_id="R006",
            level=LintLevel.ERROR,
            location="model",
            message=f"Model {spec.model!r} has no entry in PRICING_TABLE — spend cannot be metered.",
            suggestion="Use a known model ID (e.g. claude-opus-4-7) or add the new model to cma/core/pricing.py.",
        )]
    return []


def _rule_r007_custom_tool_description_short(spec: AgentSpec) -> list[Finding]:
    findings: list[Finding] = []
    for i, tool in enumerate(spec.tools):
        if not isinstance(tool, CustomToolEntry):
            continue
        if len(tool.description) < 50:
            findings.append(Finding(
                rule_id="R007",
                level=LintLevel.ERROR,
                location=f"tools[{i}].description",
                message=(
                    f"Custom tool {tool.name!r} has a {len(tool.description)}-char description; "
                    f"Anthropic's guidance is ≥3-4 sentences (≥50 chars hard minimum here)."
                ),
                suggestion="Describe purpose, when to call, what it returns, and any constraints.",
            ))
    return findings


def _rule_r008_custom_tool_name_no_namespace(spec: AgentSpec) -> list[Finding]:
    findings: list[Finding] = []
    for i, tool in enumerate(spec.tools):
        if not isinstance(tool, CustomToolEntry):
            continue
        # Accept ``namespace:name`` or ``namespace_name``. Reject bare names
        # that would collide with built-in toolset entries.
        if ":" not in tool.name and "_" not in tool.name:
            findings.append(Finding(
                rule_id="R008",
                level=LintLevel.WARNING,
                location=f"tools[{i}].name",
                message=f"Custom tool name {tool.name!r} lacks a namespace separator.",
                suggestion="Rename to e.g. 'yourproject_<verb>' or 'yourproject:<verb>' to avoid collisions.",
            ))
    return findings


def _rule_r009_custom_tool_has_timestamp(spec: AgentSpec) -> list[Finding]:
    findings: list[Finding] = []
    for i, tool in enumerate(spec.tools):
        if not isinstance(tool, CustomToolEntry):
            continue
        haystack = tool.description + " " + repr(tool.input_schema)
        if _TIMESTAMP_PATTERN.search(haystack):
            findings.append(Finding(
                rule_id="R009",
                level=LintLevel.WARNING,
                location=f"tools[{i}]",
                message=(
                    f"Custom tool {tool.name!r} description or input_schema contains a timestamp-like value. "
                    "Tools cache stricter than system prompts — a timestamp here invalidates the tool cache on every session."
                ),
                suggestion="Move dynamic values into tool arguments at call time, not the definition.",
            ))
    return findings


def _rule_r010_template_version_missing(spec: AgentSpec) -> list[Finding]:
    if "cma_template" in spec.metadata and "cma_template_version" not in spec.metadata:
        return [Finding(
            rule_id="R010",
            level=LintLevel.WARNING,
            location="metadata",
            message="metadata.cma_template is set but metadata.cma_template_version is missing.",
            suggestion="Add 'cma_template_version: 1' (bump on schema changes) so consumers can detect drift.",
        )]
    return []


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------


ALL_RULES: dict[str, Callable[[AgentSpec], list[Finding]]] = {
    "R001": _rule_r001_description_present,
    "R002": _rule_r002_description_too_short,
    "R003": _rule_r003_description_equals_name,
    "R004": _rule_r004_description_lacks_verb,
    "R005": _rule_r005_system_sub_threshold,
    "R006": _rule_r006_model_unknown,
    "R007": _rule_r007_custom_tool_description_short,
    "R008": _rule_r008_custom_tool_name_no_namespace,
    "R009": _rule_r009_custom_tool_has_timestamp,
    "R010": _rule_r010_template_version_missing,
}


# ---------------------------------------------------------------------------
# Severity policy — operator-tunable
# ---------------------------------------------------------------------------
# TODO(operator): decide the right severity per rule for this toolkit's posture.
#
# The toolkit's tagline is "cache-aware, IP-defensive." That argues for treating
# cache hygiene (R005) and tool stability (R007, R009) as errors. But a strict
# stance also makes the linter annoying for cheap throwaway agents that
# intentionally bypass caching.
#
# Three reasonable presets — pick one or roll your own:
#
#   STRICT  = everything ERROR except R004 (INFO) and R010 (WARNING)
#   MIDDLE  = R001/R003/R006/R007 ERROR; R002/R005/R008/R009/R010 WARNING; R004 INFO
#   LENIENT = R001/R003/R006 ERROR; everything else WARNING/INFO
#
# Current defaults are MIDDLE. Edit below to your preference, then update the
# doc table in docs/agent-lint.md to match.
RULE_SEVERITY: dict[str, LintLevel] = {
    "R001": LintLevel.ERROR,    # missing description -> always block
    "R002": LintLevel.WARNING,  # short description -> warn, don't block
    "R003": LintLevel.ERROR,    # description == name -> lazy authoring, block
    "R004": LintLevel.INFO,     # missing leading verb -> stylistic hint
    "R005": LintLevel.WARNING,  # sub-threshold system prompt -> warn (toggle to ERROR for strict cache discipline)
    "R006": LintLevel.ERROR,    # unknown model -> can't meter spend, block
    "R007": LintLevel.ERROR,    # short tool description -> Anthropic guidance, block
    "R008": LintLevel.WARNING,  # un-namespaced tool -> collision risk, warn
    "R009": LintLevel.WARNING,  # timestamp in tool def -> cache-busting, warn
    "R010": LintLevel.WARNING,  # missing template version -> drift risk, warn
}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def lint_agent_spec(spec: AgentSpec) -> LintResult:
    """Run every registered rule against *spec*, applying :data:`RULE_SEVERITY`.

    Each rule returns findings with its own "natural" severity; this function
    rewrites the level using :data:`RULE_SEVERITY`. A rule not listed in the
    map keeps its natural severity (so adding a new rule never silently
    changes existing severities).
    """
    result = LintResult(spec_name=spec.name)
    for rule_id, rule_fn in ALL_RULES.items():
        for finding in rule_fn(spec):
            effective_level = RULE_SEVERITY.get(rule_id, finding.level)
            result.findings.append(
                Finding(
                    rule_id=finding.rule_id,
                    level=effective_level,
                    location=finding.location,
                    message=finding.message,
                    suggestion=finding.suggestion,
                )
            )
    return result
