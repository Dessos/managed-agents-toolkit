"""Pydantic models for the ``.managed-agents/local_mcp_bridge.yaml`` schema.

The bridge YAML is the operator's declaration of which local MCP servers
are exposed to cloud agents (or to Claude Code in stdio mode), with
optional per-entry filters.

Each bridge entry has:

* ``mcp`` — name of the local MCP server (just a label; resolution to a
  real server happens via ``transport``).
* ``expose`` — either ``"all"`` (every tool the server advertises) or a
  list of tool names to allow-list.
* ``filters`` — optional per-MCP-server filters (e.g. denied paths).
* ``transport`` — OPTIONAL connection info. Without it, the entry stays a
  declarative reference and only schema-shape validation is possible.
  With it, ``cma bridge probe`` can deep-validate by actually connecting
  to the server and listing its tools.

Two transports:

* ``stdio`` — spawn a local command, speak JSON-RPC over its stdin/stdout
* ``http``  — connect to a streamable-HTTP MCP endpoint
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Transport configs
# ---------------------------------------------------------------------------


class StdioTransport(BaseModel):
    """Stdio transport: spawn a command, talk over its stdin/stdout."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["stdio"]
    command: str = Field(..., min_length=1, description="Executable to spawn (e.g. 'cma', 'codebase-memory-mcp.exe').")
    args: list[str] = Field(default_factory=list, description="Argv after the command.")
    env: dict[str, str] = Field(default_factory=dict, description="Extra env vars to set for the subprocess.")
    cwd: str | None = Field(default=None, description="Working directory; defaults to the project root.")


class HttpTransport(BaseModel):
    """HTTP transport: streamable-HTTP MCP endpoint."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["http"]
    url: str = Field(..., description="HTTPS endpoint of the MCP server.")
    auth_token_env: str | None = Field(
        default=None,
        description="Name of an env var holding the bearer token. Token never goes in the YAML.",
    )

    @field_validator("url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.startswith(("https://", "${")):
            raise ValueError(f"HTTP transport URL must be HTTPS; got {v!r}")
        return v


BridgeTransport = Annotated[
    StdioTransport | HttpTransport,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class BridgeFilters(BaseModel):
    """Per-entry filters constraining what the bridged tools can touch."""

    model_config = ConfigDict(extra="forbid")

    paths_denylist: list[str] = Field(
        default_factory=list,
        description=(
            "Paths the bridged MCP server's tools must NOT touch. Enforced by "
            "the bridge proxy at call time. Useful for codebase-memory-mcp "
            "where you want code search but NOT exposure of specific dirs."
        ),
    )


# ---------------------------------------------------------------------------
# Entry + top-level config
# ---------------------------------------------------------------------------


class BridgeEntry(BaseModel):
    """One bridged local MCP server."""

    model_config = ConfigDict(extra="forbid")

    mcp: str = Field(..., min_length=1, description="Name of the local MCP server (a label).")
    expose: Literal["all"] | list[str] = Field(
        ...,
        description="Either 'all' (every advertised tool) or a list of tool names to allow.",
    )
    deny: list[str] = Field(
        default_factory=list,
        description="Tool names to deny even if 'expose: all' would include them.",
    )
    filters: BridgeFilters = Field(default_factory=BridgeFilters)
    transport: BridgeTransport | None = Field(
        default=None,
        description=(
            "Optional connection info. Without it, schema-shape validation "
            "only. With it, `cma bridge probe` deep-validates by listing tools."
        ),
    )

    @field_validator("expose")
    @classmethod
    def _expose_shape(cls, v: object) -> object:
        if v == "all":
            return v
        if isinstance(v, list):
            if not all(isinstance(x, str) and x for x in v):
                raise ValueError("'expose' list entries must be non-empty strings")
            return v
        raise ValueError("'expose' must be 'all' or a list of tool name strings")


class BridgeConfig(BaseModel):
    """Top-level shape of ``.managed-agents/local_mcp_bridge.yaml``."""

    model_config = ConfigDict(extra="forbid")

    bridge: list[BridgeEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_bridge_config(path: Path | str) -> BridgeConfig:
    """Load + validate ``local_mcp_bridge.yaml``.

    :param path: Path to the YAML file.
    :raises FileNotFoundError: if the file is missing.
    :raises ValueError: if the file is empty or fails Pydantic validation.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"No bridge config at {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"Empty bridge config at {p}")
    return BridgeConfig.model_validate(raw)
