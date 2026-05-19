"""Tests for cma.api.sessions.

Focus: ID validation at boundaries (catches operator typos before they hit
the API) and the agent-string vs agent-dict (pinned-version) dispatch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cma.api.sessions import (
    archive_session,
    create_session,
    delete_session,
    get_session,
)


def _mock_client() -> MagicMock:
    client = MagicMock()
    sess = MagicMock()
    sess.id = "sesn_01TEST"
    sess.status = "idle"
    client.beta.sessions.create.return_value = sess
    client.beta.sessions.retrieve.return_value = sess
    client.beta.sessions.archive.return_value = sess
    client.beta.sessions.delete.return_value = sess
    return client


class TestCreateSession:
    def test_with_agent_id_string_passes_through(self) -> None:
        client = _mock_client()
        with patch("cma.api.sessions.get_client", return_value=client):
            sess = create_session(agent="agent_01ABC", environment_id="envrn_01XYZ")
        client.beta.sessions.create.assert_called_once()
        call_kwargs = client.beta.sessions.create.call_args.kwargs
        assert call_kwargs["agent"] == "agent_01ABC"
        assert call_kwargs["environment_id"] == "envrn_01XYZ"
        assert sess.id == "sesn_01TEST"

    def test_with_pinned_version_dict_passes_through(self) -> None:
        client = _mock_client()
        with patch("cma.api.sessions.get_client", return_value=client):
            create_session(
                agent={"type": "agent", "id": "agent_01ABC", "version": 3},
                environment_id="envrn_01XYZ",
            )
        call_kwargs = client.beta.sessions.create.call_args.kwargs
        assert call_kwargs["agent"]["version"] == 3

    def test_optional_fields_only_included_when_set(self) -> None:
        client = _mock_client()
        with patch("cma.api.sessions.get_client", return_value=client):
            create_session(agent="agent_01ABC", environment_id="envrn_01XYZ")
        call_kwargs = client.beta.sessions.create.call_args.kwargs
        # Should NOT include title / vault_ids / metadata if not passed.
        assert "title" not in call_kwargs
        assert "vault_ids" not in call_kwargs
        assert "metadata" not in call_kwargs

    def test_includes_title_when_set(self) -> None:
        client = _mock_client()
        with patch("cma.api.sessions.get_client", return_value=client):
            create_session(
                agent="agent_01ABC",
                environment_id="envrn_01XYZ",
                title="A long session",
            )
        assert client.beta.sessions.create.call_args.kwargs["title"] == "A long session"

    def test_includes_vault_ids_when_set(self) -> None:
        client = _mock_client()
        with patch("cma.api.sessions.get_client", return_value=client):
            create_session(
                agent="agent_01ABC",
                environment_id="envrn_01XYZ",
                vault_ids=["vlt_01A", "vlt_01B"],
            )
        assert client.beta.sessions.create.call_args.kwargs["vault_ids"] == ["vlt_01A", "vlt_01B"]

    def test_rejects_wrong_environment_prefix(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            create_session(agent="agent_01ABC", environment_id="agent_NOT_AN_ENV")

    def test_rejects_wrong_agent_prefix_string(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            create_session(agent="vlt_NOT_AN_AGENT", environment_id="envrn_01XYZ")


class TestSessionOperations:
    def test_get_validates_prefix(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            get_session("envrn_NOT_A_SESSION")

    def test_archive_validates_prefix(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            archive_session("envrn_NOT_A_SESSION")

    def test_delete_validates_prefix(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            delete_session("envrn_NOT_A_SESSION")

    def test_get_calls_sdk(self) -> None:
        client = _mock_client()
        with patch("cma.api.sessions.get_client", return_value=client):
            sess = get_session("sesn_01TEST")
        client.beta.sessions.retrieve.assert_called_once_with("sesn_01TEST")
        assert sess.status == "idle"
