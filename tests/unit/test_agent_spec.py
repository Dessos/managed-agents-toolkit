"""Tests for cma.core.agent_spec — AgentSpec pydantic model + loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cma.core.agent_spec import (
    AgentSpec,
    BuiltinToolsetEntry,
    CustomToolEntry,
    load_agent_spec,
)


class TestAgentSpecRequiredFields:
    def test_minimal_valid_spec(self) -> None:
        spec = AgentSpec(name="reviewer", model="claude-opus-4-7")
        assert spec.name == "reviewer"
        assert spec.model == "claude-opus-4-7"
        assert spec.description == ""
        assert spec.system == ""
        assert spec.tools == []

    def test_name_required(self) -> None:
        with pytest.raises(ValidationError):
            AgentSpec(model="claude-opus-4-7")  # type: ignore[call-arg]

    def test_model_required(self) -> None:
        with pytest.raises(ValidationError):
            AgentSpec(name="reviewer")  # type: ignore[call-arg]

    def test_name_rejects_path_separator(self) -> None:
        with pytest.raises(ValidationError, match="path separators"):
            AgentSpec(name="some/agent", model="claude-opus-4-7")

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            AgentSpec.model_validate(
                {"name": "x", "model": "claude-opus-4-7", "extra_field": "nope"}
            )


class TestAgentSpecTools:
    def test_builtin_toolset(self) -> None:
        spec = AgentSpec.model_validate({
            "name": "reviewer",
            "model": "claude-opus-4-7",
            "tools": [{"type": "agent_toolset_20260401"}],
        })
        assert len(spec.tools) == 1
        assert isinstance(spec.tools[0], BuiltinToolsetEntry)

    def test_custom_tool(self) -> None:
        spec = AgentSpec.model_validate({
            "name": "reviewer",
            "model": "claude-opus-4-7",
            "tools": [{
                "type": "custom",
                "name": "yourproj_fetch",
                "description": "Fetches data from the workspace registry.",
            }],
        })
        assert isinstance(spec.tools[0], CustomToolEntry)
        assert spec.tools[0].name == "yourproj_fetch"

    def test_custom_tool_requires_description(self) -> None:
        with pytest.raises(ValidationError):
            AgentSpec.model_validate({
                "name": "reviewer",
                "model": "claude-opus-4-7",
                "tools": [{"type": "custom", "name": "x"}],
            })


class TestLoadAgentSpec:
    def test_loads_yaml(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "reviewer.yaml"
        yaml_path.write_text(
            "name: reviewer\n"
            "model: claude-opus-4-7\n"
            "description: Reviews code changes for quality.\n",
            encoding="utf-8",
        )
        spec = load_agent_spec(yaml_path)
        assert spec.name == "reviewer"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_agent_spec(tmp_path / "nonexistent.yaml")

    def test_empty_yaml_raises(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "empty.yaml"
        yaml_path.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="Empty"):
            load_agent_spec(yaml_path)
