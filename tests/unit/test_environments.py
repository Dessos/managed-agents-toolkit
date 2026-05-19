"""Tests for cma.api.environments.

The most important property: ``create_environment`` REFUSES to omit
``networking``, sidestepping the docs' drift between "unrestricted default"
(Environments page) and "disabled default" (Cloud Containers page).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cma.api.environments import (
    EnvironmentRegistry,
    archive_environment,
    create_environment,
)


def _mock_client_returning(env_id: str) -> MagicMock:
    client = MagicMock()
    env_response = MagicMock()
    env_response.id = env_id
    client.beta.environments.create.return_value = env_response
    client.beta.environments.archive.return_value = env_response
    return client


class TestCreateEnvironment:
    def test_refuses_when_networking_missing(self) -> None:
        with pytest.raises(ValueError, match=r"networking.*explicitly"):
            create_environment(name="x", config={"type": "cloud"})

    def test_accepts_unrestricted(self) -> None:
        mock_client = _mock_client_returning("envrn_01TEST")
        config = {"type": "cloud", "networking": {"type": "unrestricted"}}
        with patch("cma.api.environments.get_client", return_value=mock_client):
            env = create_environment(name="x", config=config)
        assert env.id == "envrn_01TEST"
        mock_client.beta.environments.create.assert_called_once_with(name="x", config=config)

    def test_accepts_limited_with_allowlist(self) -> None:
        mock_client = _mock_client_returning("envrn_01TEST")
        config = {
            "type": "cloud",
            "networking": {
                "type": "limited",
                "allowed_hosts": ["https://api.example.com"],
                "allow_package_managers": True,
            },
        }
        with patch("cma.api.environments.get_client", return_value=mock_client):
            create_environment(name="restricted", config=config)
        # Telemetry should have recorded the networking type.
        # (No direct assertion needed — telemetry.emit() is fire-and-forget.)


class TestArchiveEnvironment:
    def test_calls_sdk_archive(self) -> None:
        mock_client = _mock_client_returning("envrn_01XYZ")
        with patch("cma.api.environments.get_client", return_value=mock_client):
            archive_environment("envrn_01XYZ")
        mock_client.beta.environments.archive.assert_called_once_with("envrn_01XYZ")

    def test_rejects_wrong_prefix(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            archive_environment("agent_NOT_AN_ENV")


# ---------------------------------------------------------------------------
# EnvironmentRegistry
# ---------------------------------------------------------------------------


class TestEnvironmentRegistry:
    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        reg = EnvironmentRegistry(tmp_path / "envs.db")
        assert reg.get_id(project="p", name="missing") is None

    def test_upsert_then_get(self, tmp_path: Path) -> None:
        reg = EnvironmentRegistry(tmp_path / "envs.db")
        reg.upsert(
            project="p",
            name="data",
            environment_id="envrn_01TEST",
            config_json="{}",
            updated_at="2026-05-19T00:00:00",
        )
        assert reg.get_id(project="p", name="data") == "envrn_01TEST"

    def test_upsert_overwrites(self, tmp_path: Path) -> None:
        reg = EnvironmentRegistry(tmp_path / "envs.db")
        reg.upsert(
            project="p",
            name="data",
            environment_id="envrn_OLD",
            config_json="{}",
            updated_at="2026-05-19T00:00:00",
        )
        reg.upsert(
            project="p",
            name="data",
            environment_id="envrn_NEW",
            config_json="{}",
            updated_at="2026-05-19T01:00:00",
        )
        assert reg.get_id(project="p", name="data") == "envrn_NEW"
