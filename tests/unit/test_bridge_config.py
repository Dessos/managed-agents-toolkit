"""Tests for cma.executor.bridge_config — bridge YAML schema validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cma.executor.bridge_config import (
    BridgeEntry,
    BridgeFilters,
    HttpTransport,
    StdioTransport,
    load_bridge_config,
)

# ---------------------------------------------------------------------------
# Transport models
# ---------------------------------------------------------------------------


class TestStdioTransport:
    def test_basic(self) -> None:
        t = StdioTransport(type="stdio", command="cma", args=["executor", "stdio"])
        assert t.command == "cma"
        assert t.args == ["executor", "stdio"]
        assert t.env == {}
        assert t.cwd is None

    def test_with_env_and_cwd(self) -> None:
        t = StdioTransport(
            type="stdio",
            command="some-mcp",
            env={"DEBUG": "1"},
            cwd="/tmp/work",
        )
        assert t.env == {"DEBUG": "1"}
        assert t.cwd == "/tmp/work"

    def test_rejects_empty_command(self) -> None:
        with pytest.raises(ValueError):
            StdioTransport(type="stdio", command="")

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValueError):
            StdioTransport.model_validate(
                {"type": "stdio", "command": "x", "rogue_field": True}
            )


class TestHttpTransport:
    def test_basic_https(self) -> None:
        t = HttpTransport(type="http", url="https://mcp.example.com/")
        assert t.url.startswith("https://")
        assert t.auth_token_env is None

    def test_with_auth_token_env(self) -> None:
        t = HttpTransport(
            type="http",
            url="https://mcp.example.com/",
            auth_token_env="MY_TOKEN",
        )
        assert t.auth_token_env == "MY_TOKEN"

    def test_rejects_http(self) -> None:
        with pytest.raises(ValueError, match="HTTPS"):
            HttpTransport(type="http", url="http://insecure.example.com/")

    def test_accepts_template_placeholder(self) -> None:
        # ${VAR} is allowed pre-expansion (env-var interpolation happens elsewhere)
        t = HttpTransport(type="http", url="${MY_MCP_URL}")
        assert t.url.startswith("${")


# ---------------------------------------------------------------------------
# BridgeEntry
# ---------------------------------------------------------------------------


class TestBridgeEntry:
    def test_expose_all(self) -> None:
        e = BridgeEntry(mcp="my-mcp", expose="all")
        assert e.expose == "all"
        assert e.deny == []

    def test_expose_list(self) -> None:
        e = BridgeEntry(mcp="my-mcp", expose=["tool_a", "tool_b"])
        assert e.expose == ["tool_a", "tool_b"]

    def test_rejects_empty_string_in_expose_list(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            BridgeEntry(mcp="x", expose=["valid", ""])

    def test_rejects_invalid_expose(self) -> None:
        # Pydantic raises a ValidationError that wraps the field; we just
        # verify it rejects (not the exact message — Pydantic's wording shifts).
        with pytest.raises(ValueError):
            BridgeEntry(mcp="x", expose="some_string_that_isnt_all")

    def test_with_filters(self) -> None:
        e = BridgeEntry(
            mcp="codebase-memory-mcp",
            expose=["search_code"],
            filters=BridgeFilters(paths_denylist=["src/secret/"]),
        )
        assert e.filters.paths_denylist == ["src/secret/"]

    def test_with_stdio_transport(self) -> None:
        e = BridgeEntry(
            mcp="my-mcp",
            expose="all",
            transport=StdioTransport(type="stdio", command="my-mcp"),
        )
        assert e.transport is not None
        assert e.transport.type == "stdio"

    def test_with_http_transport(self) -> None:
        e = BridgeEntry(
            mcp="my-mcp",
            expose="all",
            transport=HttpTransport(type="http", url="https://example.com/mcp"),
        )
        assert e.transport is not None
        assert e.transport.type == "http"

    def test_transport_discriminated_by_type(self) -> None:
        # From dict — discriminator picks the right model.
        e = BridgeEntry.model_validate(
            {
                "mcp": "x",
                "expose": "all",
                "transport": {"type": "stdio", "command": "x"},
            }
        )
        assert isinstance(e.transport, StdioTransport)

    def test_rejects_unknown_transport_type(self) -> None:
        with pytest.raises(ValueError):
            BridgeEntry.model_validate(
                {
                    "mcp": "x",
                    "expose": "all",
                    "transport": {"type": "websocket", "url": "wss://x"},
                }
            )

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValueError):
            BridgeEntry.model_validate(
                {"mcp": "x", "expose": "all", "rogue_field": True}
            )


# ---------------------------------------------------------------------------
# load_bridge_config
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, data: object) -> Path:
    p = tmp_path / "local_mcp_bridge.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


class TestLoadBridgeConfig:
    def test_loads_minimal(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            {"bridge": [{"mcp": "x", "expose": "all"}]},
        )
        config = load_bridge_config(path)
        assert len(config.bridge) == 1
        assert config.bridge[0].mcp == "x"

    def test_loads_with_full_entry(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            {
                "bridge": [
                    {
                        "mcp": "codebase-memory",
                        "expose": ["search_code", "query_graph"],
                        "deny": ["get_code_snippet"],
                        "filters": {"paths_denylist": ["src/secret/"]},
                        "transport": {
                            "type": "stdio",
                            "command": "codebase-memory-mcp.exe",
                            "args": ["--port", "8870"],
                            "env": {"LOG_LEVEL": "info"},
                        },
                    },
                ],
            },
        )
        config = load_bridge_config(path)
        entry = config.bridge[0]
        assert entry.mcp == "codebase-memory"
        assert entry.deny == ["get_code_snippet"]
        assert entry.filters.paths_denylist == ["src/secret/"]
        assert isinstance(entry.transport, StdioTransport)
        assert entry.transport.command == "codebase-memory-mcp.exe"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_bridge_config(tmp_path / "nope.yaml")

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="Empty"):
            load_bridge_config(p)

    def test_invalid_schema_raises(self, tmp_path: Path) -> None:
        path = _write(tmp_path, {"bridge": [{"mcp": "x", "expose": "bogus"}]})
        with pytest.raises(ValueError):
            load_bridge_config(path)

    def test_empty_bridge_list_ok(self, tmp_path: Path) -> None:
        # An empty bridge: [] is valid (means: nothing bridged).
        path = _write(tmp_path, {"bridge": []})
        config = load_bridge_config(path)
        assert config.bridge == []
