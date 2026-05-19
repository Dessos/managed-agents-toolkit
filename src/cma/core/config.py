"""Project config schema (``.managed-agents/project.yaml``).

Pydantic v2 models. Loader resolves template-or-local imports and expands
``${ENV_VAR}`` references in string values.

Shipping a strict schema serves two ends:

* Operator sees validation errors at lint time, not at session-start time.
* Cloud agents see the same shape across projects, which makes templates
  + adapter contracts portable.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Leaf models — refs to template/local resources
# ---------------------------------------------------------------------------


class TemplateRef(BaseModel):
    """Reference to a template that ships with the toolkit."""

    template: str = Field(..., description="Template name, e.g. 'code-reviewer'.")

    @field_validator("template")
    @classmethod
    def _no_path_separator(cls, v: str) -> str:
        if "/" in v or "\\" in v:
            raise ValueError(
                "template names cannot contain path separators; use 'local:' "
                "for paths relative to .managed-agents/"
            )
        return v


class LocalRef(BaseModel):
    """Reference to a YAML file under the project's ``.managed-agents/``."""

    local: str = Field(
        ..., description="Path relative to .managed-agents/, e.g. 'agents/foo.yaml'."
    )


Ref = TemplateRef | LocalRef


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


class ProjectMeta(BaseModel):
    """Top-level ``project:`` block."""

    name: str = Field(..., min_length=1, description="Unique project slug; used in telemetry filenames.")
    workspace_root: Path = Field(..., description="Absolute path to the consumer project's repo root.")
    description: str | None = None


class Defaults(BaseModel):
    """Project-wide defaults for agent/env selection."""

    model: str = "claude-opus-4-7"
    environment: str | None = None  # Resolved to an entry in :attr:`ProjectConfig.environments`.


class AnthropicSkill(BaseModel):
    """Pre-built Anthropic skill (xlsx / pdf / docx / pptx, etc.)."""

    type: Literal["anthropic"] = "anthropic"
    skill_id: str


class CustomSkillRef(BaseModel):
    """Workspace-authored skill, loaded from a local directory."""

    type: Literal["custom"] = "custom"
    local: str = Field(..., description="Path to skill directory under .managed-agents/.")
    version: str = "latest"


SkillEntry = AnthropicSkill | CustomSkillRef


class McpServerSpec(BaseModel):
    """Remote MCP server entry in ``project.yaml``."""

    name: str = Field(..., min_length=1)
    url: str = Field(..., description="HTTPS endpoint of the MCP server (streamable HTTP transport).")
    # Vault credential reference is resolved at session-creation time via
    # ``vault_ids``; the project-level config only declares the URL + name.

    @field_validator("url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.startswith(("https://", "${")):  # allow ${ENV_VAR} interpolation
            raise ValueError(f"MCP server URL must be HTTPS; got {v!r}")
        return v


class BudgetConfig(BaseModel):
    """Spend ceilings + kill switch behaviour."""

    daily_usd_cap: float = Field(25.0, gt=0)
    per_session_token_cap: int = Field(5_000_000, gt=0)
    warn_at_pct: int = Field(75, ge=0, le=100)
    kill_on_breach: bool = True


class WebhookConfig(BaseModel):
    """Webhook receiver configuration."""

    endpoint: str = Field(
        ..., description="Public HTTPS URL Anthropic posts to. Typically a Cloudflare Tunnel hostname."
    )
    signing_secret_env: str = Field(
        default="ANTHROPIC_WEBHOOK_SIGNING_KEY",
        description="Env var name that holds the whsec_-prefixed secret.",
    )
    subscribe_events: list[str] = Field(
        default_factory=lambda: [
            "session.status_idled",
            "session.status_terminated",
            "session.outcome_evaluation_ended",
            "vault_credential.refresh_failed",
        ]
    )

    @field_validator("endpoint")
    @classmethod
    def _https_or_template(cls, v: str) -> str:
        if not v.startswith(("https://", "${")):
            raise ValueError(f"Webhook endpoint must be HTTPS; got {v!r}")
        return v


class TelemetryConfig(BaseModel):
    """Where telemetry lines go."""

    jsonl_path: str | None = None  # If unset, falls back to platform default.
    prometheus_pushgateway: str | None = None


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


class ProjectConfig(BaseModel):
    """The full ``.managed-agents/project.yaml`` document."""

    project: ProjectMeta
    defaults: Defaults = Field(default_factory=Defaults)
    agents: list[Ref] = Field(default_factory=list)
    environments: list[Ref] = Field(default_factory=list)
    skills: list[SkillEntry] = Field(default_factory=list)
    mcp_servers: list[McpServerSpec] = Field(default_factory=list)
    workflows: list[Ref] = Field(default_factory=list)
    # mypy doesn't see Pydantic's runtime defaults, so we annotate.
    budget: BudgetConfig = Field(default_factory=BudgetConfig)  # type: ignore[arg-type]
    webhook: WebhookConfig | None = None
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)

    @model_validator(mode="after")
    def _check_workspace_root_exists(self) -> ProjectConfig:
        if not self.project.workspace_root.is_dir():
            raise ValueError(
                f"workspace_root {self.project.workspace_root!s} does not exist "
                f"or is not a directory."
            )
        return self


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


_ENV_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ``${VAR}`` references in string values.

    Missing env vars leave the placeholder in place — lint will then flag
    them. We prefer obvious failure ("validator says VAULT_TOKEN is empty")
    to silent defaults ("validator passes because we substituted empty").
    """
    if isinstance(value, str):
        def _sub(match: re.Match[str]) -> str:
            return os.environ.get(match.group(1), match.group(0))
        return _ENV_VAR_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


def load_project(path: Path | str) -> ProjectConfig:
    """Load + validate a project config from disk.

    :param path: Either the project root (will look for
        ``.managed-agents/project.yaml``) or the YAML file directly.
    """
    p = Path(path)
    if p.is_dir():
        p = p / ".managed-agents" / "project.yaml"
    if not p.is_file():
        raise FileNotFoundError(f"No project config found at {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"Empty project config at {p}")
    expanded = _expand_env_vars(raw)
    return ProjectConfig.model_validate(expanded)
