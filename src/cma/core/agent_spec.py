"""``AgentSpec`` — the schema for a standalone agent YAML.

An agent YAML lives at ``src/cma/templates/agents/<name>.yaml`` (toolkit
templates) or ``.managed-agents/agents/<name>.yaml`` (project overrides) and
is referenced from ``project.yaml`` via :class:`cma.core.config.TemplateRef`
or :class:`cma.core.config.LocalRef`.

The Pydantic model here is the *shape* check; the lint engine in
:mod:`cma.core.lint` layers semantic rules on top (caching thresholds,
description discipline, tool hygiene). Keep this module narrow — it should
fail on outright invalid YAML, not on style.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, field_validator

# ---------------------------------------------------------------------------
# Tool sub-models
# ---------------------------------------------------------------------------


class BuiltinToolsetEntry(BaseModel):
    """The ``agent_toolset_20260401`` block (or future toolset versions).

    Anthropic publishes a versioned bundle of read/glob/grep/edit/bash/etc.
    Per-tool enable/disable lives under ``configs``.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., description="Toolset identifier, e.g. agent_toolset_20260401.")
    configs: list[dict[str, Any]] = Field(default_factory=list)


class CustomToolEntry(BaseModel):
    """A workspace-defined custom tool.

    Custom tools live or die by their descriptions and input schemas; the
    lint engine is the place those are checked.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["custom"] = "custom"
    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)


def _tool_discriminator(v: Any) -> str:
    """Discriminate tool entries by their ``type`` field.

    Anything with ``type == "custom"`` is a custom tool; everything else is
    treated as a built-in toolset bundle.
    """
    if isinstance(v, dict):
        return "custom" if v.get("type") == "custom" else "builtin"
    return "custom" if isinstance(v, CustomToolEntry) else "builtin"


ToolEntry = Annotated[
    Annotated[CustomToolEntry, Tag("custom")] | Annotated[BuiltinToolsetEntry, Tag("builtin")],
    Discriminator(_tool_discriminator),
]


# ---------------------------------------------------------------------------
# Skill + MCP sub-models (kept thin; the lint engine doesn't yet rule on these)
# ---------------------------------------------------------------------------


class AnthropicSkillEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["anthropic"] = "anthropic"
    skill_id: str = Field(..., min_length=1)


class CustomSkillEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["custom"] = "custom"
    local: str = Field(..., min_length=1)
    version: str = "latest"


SkillEntry = AnthropicSkillEntry | CustomSkillEntry


class AgentMcpServer(BaseModel):
    """Per-agent MCP server override. Project-level entries override these."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    url: str | None = Field(default=None, description="HTTPS endpoint; may be omitted for project-resolved entries.")


# ---------------------------------------------------------------------------
# Multi-agent block (lint engine doesn't rule on this yet; carried verbatim)
# ---------------------------------------------------------------------------


class MultiagentBlock(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str  # "coordinator" or other future types
    agents: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


class AgentSpec(BaseModel):
    """The full agent YAML document."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1, description="Anthropic model identifier (e.g. claude-opus-4-7).")
    description: str = Field(default="", description="Operator-facing summary; the lint engine enforces discipline.")
    system: str = Field(default="", description="System prompt body. Lint warns when below the model's cache threshold.")
    tools: list[ToolEntry] = Field(default_factory=list)
    mcp_servers: list[AgentMcpServer] = Field(default_factory=list)
    skills: list[SkillEntry] = Field(default_factory=list)
    multiagent: MultiagentBlock | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _no_path_separator(cls, v: str) -> str:
        if "/" in v or "\\" in v:
            raise ValueError(f"agent name must not contain path separators; got {v!r}")
        return v


def load_agent_spec(path: Path | str) -> AgentSpec:
    """Parse a single agent YAML file into an :class:`AgentSpec`.

    Raises :class:`FileNotFoundError` if the path doesn't exist,
    :class:`ValueError` if the YAML is empty, and
    :class:`pydantic.ValidationError` for shape violations.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"No agent spec at {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"Empty agent spec at {p}")
    return AgentSpec.model_validate(raw)
