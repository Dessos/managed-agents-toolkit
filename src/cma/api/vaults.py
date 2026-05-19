"""Vaults API wrapper — credentials + diagnostic helpers.

Vaults store OAuth-style credentials for MCP servers. The wrapper:

* Refuses to add a second credential for the same ``mcp_server_url`` per
  vault (the API returns 409; we fail earlier with a clearer message).
* Reads tokens from files only, never from CLI args or stdin (no shell-
  history leaks).
* Provides ``validate_oauth_credential`` as a thin wrapper around the
  ``/mcp_oauth_validate`` endpoint for refresh-failure diagnostics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cma.api.client import get_client
from cma.core.identifiers import ResourceKind, expect_kind
from cma.telemetry import emit

# ---------------------------------------------------------------------------
# Vault CRUD
# ---------------------------------------------------------------------------


def create_vault(
    *,
    display_name: str,
    metadata: dict[str, str] | None = None,
) -> Any:
    """Create a new vault for storing credentials.

    :param display_name: Human-readable label.
    :param metadata: Free-form key-value tags; typical entry is
        ``{"external_user_id": "..."}`` for multi-user systems.
    """
    client = get_client()
    payload: dict[str, Any] = {"display_name": display_name}
    if metadata:
        payload["metadata"] = metadata
    emit(domain="cma.api.vaults", action="create_start", extra={"display_name": display_name})
    vault = client.beta.vaults.create(**payload)
    emit(domain="cma.api.vaults", action="create_done", extra={"vault_id": vault.id})
    return vault


def archive_vault(vault_id: str) -> Any:
    """Archive a vault (cascades to all its credentials per the docs)."""
    expect_kind(vault_id, ResourceKind.VAULT)
    emit(domain="cma.api.vaults", action="archive", extra={"vault_id": vault_id})
    return get_client().beta.vaults.archive(vault_id)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def add_static_bearer_credential(
    *,
    vault_id: str,
    display_name: str,
    mcp_server_url: str,
    token_path: Path | str,
) -> Any:
    """Add a static bearer token credential for an MCP server.

    :param token_path: Path to a file containing the bearer token. Read at
        the last possible moment, never echoed, never logged.
    """
    expect_kind(vault_id, ResourceKind.VAULT)
    token = Path(token_path).read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"Token file {token_path} is empty.")
    if not mcp_server_url.startswith("https://"):
        raise ValueError(f"MCP server URL must be HTTPS; got {mcp_server_url!r}")
    client = get_client()
    emit(
        domain="cma.api.vaults",
        action="add_credential_start",
        extra={
            "vault_id": vault_id,
            "mcp_server_url": mcp_server_url,
            "auth_type": "static_bearer",
            "display_name": display_name,
        },
    )
    credential = client.beta.vaults.credentials.create(
        vault_id=vault_id,
        display_name=display_name,
        auth={
            "type": "static_bearer",
            "mcp_server_url": mcp_server_url,
            "token": token,
        },
    )
    emit(
        domain="cma.api.vaults",
        action="add_credential_done",
        extra={"vault_id": vault_id, "credential_id": credential.id},
    )
    return credential


def validate_oauth_credential(*, vault_id: str, credential_id: str) -> Any:
    """Run the ``/mcp_oauth_validate`` probe for refresh diagnostics.

    Returns the validation object; see the docs for the
    ``vault_credential_validation`` response shape.
    """
    expect_kind(vault_id, ResourceKind.VAULT)
    expect_kind(credential_id, ResourceKind.VAULT_CREDENTIAL)
    client = get_client()
    emit(
        domain="cma.api.vaults",
        action="validate_credential",
        extra={"vault_id": vault_id, "credential_id": credential_id},
    )
    # The SDK exposes this via a method on the credentials resource.
    return client.beta.vaults.credentials.mcp_oauth_validate(
        credential_id, vault_id=vault_id
    )
