"""Tests for cma.api.agents.

Covers:
- spec_hash properties (deterministic, 32 chars, order-independent on keys)
- AgentRegistry CRUD (upsert + get)
- create_agent + sync_agent dispatch (mocks the SDK)
- expect_kind boundary check (already in test_identifiers; we verify it fires here)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cma.api.agents import (
    AgentRegistry,
    RegisteredAgent,
    archive_agent,
    create_agent,
    spec_hash,
    sync_agent,
)

# ---------------------------------------------------------------------------
# spec_hash
# ---------------------------------------------------------------------------


class TestSpecHash:
    def test_returns_32_hex_chars(self) -> None:
        h = spec_hash({"name": "x", "model": "claude-opus-4-7"})
        assert len(h) == 32
        # All hex
        int(h, 16)  # raises if not

    def test_deterministic(self) -> None:
        spec = {"name": "x", "model": "claude-opus-4-7", "tools": [{"type": "t1"}]}
        assert spec_hash(spec) == spec_hash(spec)

    def test_independent_of_dict_insertion_order(self) -> None:
        # Sort by key inside the hash, so {a:1, b:2} == {b:2, a:1}
        a = {"name": "x", "model": "claude-opus-4-7"}
        b = {"model": "claude-opus-4-7", "name": "x"}
        assert spec_hash(a) == spec_hash(b)

    def test_sensitive_to_value_change(self) -> None:
        a = {"name": "x", "model": "claude-opus-4-7"}
        b = {"name": "x", "model": "claude-sonnet-4-6"}
        assert spec_hash(a) != spec_hash(b)

    def test_sensitive_to_list_reorder(self) -> None:
        # Tools reorder DOES matter: the docs say reordering tools invalidates
        # the prompt cache. We mirror that semantics.
        a = {"tools": [{"type": "a"}, {"type": "b"}]}
        b = {"tools": [{"type": "b"}, {"type": "a"}]}
        assert spec_hash(a) != spec_hash(b)

    def test_handles_nested_structures(self) -> None:
        spec = {
            "name": "x",
            "metadata": {"k1": "v1", "k2": "v2"},
            "tools": [
                {"type": "agent_toolset_20260401", "configs": [{"name": "bash"}]},
            ],
        }
        # Just verify it doesn't crash and returns valid hex.
        h = spec_hash(spec)
        assert len(h) == 32
        int(h, 16)


# ---------------------------------------------------------------------------
# AgentRegistry — uses real SQLite via tmp_path
# ---------------------------------------------------------------------------


class TestAgentRegistry:
    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        registry = AgentRegistry(tmp_path / "reg.db")
        assert registry.get(project="p", name="missing") is None

    def test_upsert_then_get(self, tmp_path: Path) -> None:
        registry = AgentRegistry(tmp_path / "reg.db")
        entry = RegisteredAgent(
            project="p",
            name="code-reviewer",
            agent_id="agent_01ABC",
            version=1,
            spec_hash="a" * 32,
            updated_at="2026-05-19T00:00:00",
        )
        registry.upsert(entry)
        got = registry.get(project="p", name="code-reviewer")
        assert got is not None
        assert got.agent_id == "agent_01ABC"
        assert got.version == 1

    def test_upsert_updates_existing(self, tmp_path: Path) -> None:
        registry = AgentRegistry(tmp_path / "reg.db")
        entry_v1 = RegisteredAgent(
            project="p",
            name="r",
            agent_id="agent_01",
            version=1,
            spec_hash="a" * 32,
            updated_at="2026-05-19T00:00:00",
        )
        entry_v2 = RegisteredAgent(
            project="p",
            name="r",
            agent_id="agent_01",
            version=2,
            spec_hash="b" * 32,
            updated_at="2026-05-19T01:00:00",
        )
        registry.upsert(entry_v1)
        registry.upsert(entry_v2)
        got = registry.get(project="p", name="r")
        assert got is not None
        assert got.version == 2
        assert got.spec_hash == "b" * 32

    def test_different_projects_isolated(self, tmp_path: Path) -> None:
        registry = AgentRegistry(tmp_path / "reg.db")
        registry.upsert(RegisteredAgent("proj_a", "shared", "agent_a", 1, "a" * 32, "ts"))
        registry.upsert(RegisteredAgent("proj_b", "shared", "agent_b", 1, "b" * 32, "ts"))
        assert registry.get(project="proj_a", name="shared").agent_id == "agent_a"  # type: ignore[union-attr]
        assert registry.get(project="proj_b", name="shared").agent_id == "agent_b"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# create_agent + sync_agent with mocked SDK
# ---------------------------------------------------------------------------


def _mock_client_returning(agent_id: str, version: int) -> MagicMock:
    """Build a mock anthropic client whose agents.create returns the given ID."""
    client = MagicMock()
    response = MagicMock()
    response.id = agent_id
    response.version = version
    client.beta.agents.create.return_value = response
    client.beta.agents.update.return_value = response
    client.beta.agents.archive.return_value = response
    return client


class TestCreateAgent:
    def test_calls_sdk_and_records_in_registry(self, tmp_path: Path) -> None:
        mock_client = _mock_client_returning("agent_01TEST", version=1)
        registry = AgentRegistry(tmp_path / "reg.db")
        spec = {"name": "tester", "model": "claude-sonnet-4-6", "system": "go"}

        with patch("cma.api.agents.get_client", return_value=mock_client):
            result = create_agent(project="myproj", spec=spec, registry=registry)

        assert result.id == "agent_01TEST"
        mock_client.beta.agents.create.assert_called_once_with(**spec)
        entry = registry.get(project="myproj", name="tester")
        assert entry is not None
        assert entry.agent_id == "agent_01TEST"
        assert entry.version == 1
        # Hash is 32 chars (per the 0.1.0 bump)
        assert len(entry.spec_hash) == 32

    def test_rejects_non_agent_id_from_sdk(self, tmp_path: Path) -> None:
        """If the SDK returned a wrong-prefixed ID, we should fail loudly.

        Defends against the SDK's typed responses drifting away from the
        docs' resource prefixes.
        """
        mock_client = _mock_client_returning("sesn_NOT_AN_AGENT", version=1)
        registry = AgentRegistry(tmp_path / "reg.db")
        spec = {"name": "bad", "model": "claude-sonnet-4-6"}

        with (
            patch("cma.api.agents.get_client", return_value=mock_client),
            pytest.raises(ValueError, match="expected"),
        ):
            create_agent(project="p", spec=spec, registry=registry)


class TestSyncAgent:
    def test_no_op_when_hash_matches(self, tmp_path: Path) -> None:
        mock_client = _mock_client_returning("agent_01TEST", version=5)
        registry = AgentRegistry(tmp_path / "reg.db")
        spec = {"name": "stable", "model": "claude-sonnet-4-6"}
        registry.upsert(
            RegisteredAgent(
                project="p",
                name="stable",
                agent_id="agent_01TEST",
                version=5,
                spec_hash=spec_hash(spec),
                updated_at="2026-05-19T00:00:00",
            )
        )

        with patch("cma.api.agents.get_client", return_value=mock_client):
            result = sync_agent(project="p", spec=spec, registry=registry)

        # No-op should return the existing registry entry and NOT call SDK.
        mock_client.beta.agents.create.assert_not_called()
        mock_client.beta.agents.update.assert_not_called()
        # Result is the RegisteredAgent on no-op.
        assert isinstance(result, RegisteredAgent)
        assert result.version == 5

    def test_creates_when_no_registry_entry(self, tmp_path: Path) -> None:
        mock_client = _mock_client_returning("agent_01NEW", version=1)
        registry = AgentRegistry(tmp_path / "reg.db")
        spec = {"name": "new-agent", "model": "claude-sonnet-4-6"}

        with patch("cma.api.agents.get_client", return_value=mock_client):
            sync_agent(project="p", spec=spec, registry=registry)

        mock_client.beta.agents.create.assert_called_once_with(**spec)
        mock_client.beta.agents.update.assert_not_called()

    def test_updates_when_hash_drifts(self, tmp_path: Path) -> None:
        mock_client = _mock_client_returning("agent_01TEST", version=3)
        registry = AgentRegistry(tmp_path / "reg.db")
        old_spec = {"name": "drifted", "model": "claude-sonnet-4-6", "system": "old"}
        registry.upsert(
            RegisteredAgent(
                project="p",
                name="drifted",
                agent_id="agent_01TEST",
                version=2,
                spec_hash=spec_hash(old_spec),
                updated_at="2026-05-19T00:00:00",
            )
        )
        new_spec = {"name": "drifted", "model": "claude-sonnet-4-6", "system": "new"}

        with patch("cma.api.agents.get_client", return_value=mock_client):
            sync_agent(project="p", spec=new_spec, registry=registry)

        # Should call update, not create.
        mock_client.beta.agents.create.assert_not_called()
        mock_client.beta.agents.update.assert_called_once()
        # Verify version was passed for optimistic concurrency.
        call_kwargs = mock_client.beta.agents.update.call_args.kwargs
        assert call_kwargs["version"] == 2  # the old version


# ---------------------------------------------------------------------------
# archive_agent
# ---------------------------------------------------------------------------


class TestArchiveAgent:
    def test_calls_sdk_archive(self) -> None:
        mock_client = _mock_client_returning("agent_01ANY", version=1)
        with patch("cma.api.agents.get_client", return_value=mock_client):
            archive_agent("agent_01ANY")
        mock_client.beta.agents.archive.assert_called_once_with("agent_01ANY")

    def test_rejects_wrong_prefix(self) -> None:
        # Boundary check fires before SDK call.
        with pytest.raises(ValueError, match="expected"):
            archive_agent("sesn_WRONG_PREFIX")
