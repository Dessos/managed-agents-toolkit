"""Tests for cma.api.vaults.

Most important properties:
- Token is read from a file (never CLI/stdin) — no shell-history leaks.
- HTTPS-only MCP URLs.
- Empty token file is rejected.
- ID prefix validation at boundaries.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cma.api.vaults import (
    add_static_bearer_credential,
    archive_vault,
    create_vault,
    validate_oauth_credential,
)


def _mock_client_with_vault(vault_id: str, cred_id: str = "vcrd_01TEST") -> MagicMock:
    client = MagicMock()
    vault = MagicMock()
    vault.id = vault_id
    cred = MagicMock()
    cred.id = cred_id
    client.beta.vaults.create.return_value = vault
    client.beta.vaults.archive.return_value = vault
    client.beta.vaults.credentials.create.return_value = cred
    client.beta.vaults.credentials.mcp_oauth_validate.return_value = MagicMock(status="valid")
    return client


class TestCreateVault:
    def test_basic_creation(self) -> None:
        client = _mock_client_with_vault("vlt_01TEST")
        with patch("cma.api.vaults.get_client", return_value=client):
            vault = create_vault(display_name="Alice")
        client.beta.vaults.create.assert_called_once_with(display_name="Alice")
        assert vault.id == "vlt_01TEST"

    def test_with_metadata(self) -> None:
        client = _mock_client_with_vault("vlt_01TEST")
        with patch("cma.api.vaults.get_client", return_value=client):
            create_vault(display_name="Alice", metadata={"external_user_id": "u1"})
        kwargs = client.beta.vaults.create.call_args.kwargs
        assert kwargs["metadata"]["external_user_id"] == "u1"

    def test_metadata_omitted_when_unset(self) -> None:
        client = _mock_client_with_vault("vlt_01TEST")
        with patch("cma.api.vaults.get_client", return_value=client):
            create_vault(display_name="NoMeta")
        kwargs = client.beta.vaults.create.call_args.kwargs
        assert "metadata" not in kwargs


class TestArchiveVault:
    def test_calls_sdk(self) -> None:
        client = _mock_client_with_vault("vlt_01XYZ")
        with patch("cma.api.vaults.get_client", return_value=client):
            archive_vault("vlt_01XYZ")
        client.beta.vaults.archive.assert_called_once_with("vlt_01XYZ")

    def test_rejects_wrong_prefix(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            archive_vault("agent_NOT_A_VAULT")


class TestAddStaticBearer:
    def test_reads_token_from_file(self, tmp_path: Path) -> None:
        client = _mock_client_with_vault("vlt_01TEST")
        token_file = tmp_path / "token.txt"
        token_file.write_text("lin_api_my_real_token\n", encoding="utf-8")

        with patch("cma.api.vaults.get_client", return_value=client):
            cred = add_static_bearer_credential(
                vault_id="vlt_01TEST",
                display_name="Linear",
                mcp_server_url="https://mcp.linear.app/mcp",
                token_path=token_file,
            )

        client.beta.vaults.credentials.create.assert_called_once()
        kwargs = client.beta.vaults.credentials.create.call_args.kwargs
        # Token from file, whitespace stripped.
        assert kwargs["auth"]["token"] == "lin_api_my_real_token"
        assert kwargs["auth"]["mcp_server_url"] == "https://mcp.linear.app/mcp"
        assert kwargs["display_name"] == "Linear"
        assert cred.id == "vcrd_01TEST"

    def test_rejects_empty_token_file(self, tmp_path: Path) -> None:
        token_file = tmp_path / "empty.txt"
        token_file.write_text("   \n", encoding="utf-8")  # whitespace-only counts as empty.
        with pytest.raises(ValueError, match="empty"):
            add_static_bearer_credential(
                vault_id="vlt_01TEST",
                display_name="X",
                mcp_server_url="https://example.com/mcp",
                token_path=token_file,
            )

    def test_rejects_http_url(self, tmp_path: Path) -> None:
        token_file = tmp_path / "tok.txt"
        token_file.write_text("validtoken", encoding="utf-8")
        with pytest.raises(ValueError, match="HTTPS"):
            add_static_bearer_credential(
                vault_id="vlt_01TEST",
                display_name="Insecure",
                mcp_server_url="http://insecure.example.com/mcp",
                token_path=token_file,
            )

    def test_rejects_wrong_vault_prefix(self, tmp_path: Path) -> None:
        token_file = tmp_path / "tok.txt"
        token_file.write_text("validtoken", encoding="utf-8")
        with pytest.raises(ValueError, match="expected"):
            add_static_bearer_credential(
                vault_id="agent_NOT_A_VAULT",
                display_name="X",
                mcp_server_url="https://example.com/mcp",
                token_path=token_file,
            )


class TestValidateOAuthCredential:
    def test_calls_sdk_with_both_ids(self) -> None:
        client = _mock_client_with_vault("vlt_01TEST")
        with patch("cma.api.vaults.get_client", return_value=client):
            result = validate_oauth_credential(
                vault_id="vlt_01TEST", credential_id="vcrd_01TEST"
            )
        client.beta.vaults.credentials.mcp_oauth_validate.assert_called_once_with(
            "vcrd_01TEST", vault_id="vlt_01TEST"
        )
        assert result.status == "valid"

    def test_rejects_wrong_vault_prefix(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            validate_oauth_credential(
                vault_id="agent_WRONG", credential_id="vcrd_01TEST"
            )

    def test_rejects_wrong_credential_prefix(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            validate_oauth_credential(
                vault_id="vlt_01TEST", credential_id="agent_WRONG"
            )
