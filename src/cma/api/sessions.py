"""Sessions API wrapper.

Sessions are the core lifecycle object. The wrapper:

* Validates the agent + environment IDs at boundaries.
* Threads ``diagnostics.previous_message_id`` automatically for cache
  diagnostics (when a session "follows on" from a prior session).
* Emits a JSONL line at every status transition.
"""

from __future__ import annotations

from typing import Any

from cma.api.client import get_client
from cma.core.identifiers import ResourceKind, expect_kind
from cma.telemetry import emit


def create_session(
    *,
    agent: str | dict[str, Any],
    environment_id: str,
    title: str | None = None,
    vault_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Create a new session.

    :param agent: Either an agent_id (latest version) or a dict
        ``{"type": "agent", "id": ..., "version": N}`` to pin a specific
        version.
    :param environment_id: Must be a ``envrn_...`` ID.
    :param vault_ids: Optional list of vault references for MCP auth.
    """
    expect_kind(environment_id, ResourceKind.ENVIRONMENT)
    if isinstance(agent, str):
        expect_kind(agent, ResourceKind.AGENT)

    client = get_client()
    payload: dict[str, Any] = {
        "agent": agent,
        "environment_id": environment_id,
    }
    if title is not None:
        payload["title"] = title
    if vault_ids:
        payload["vault_ids"] = vault_ids
    if metadata:
        payload["metadata"] = metadata

    emit(
        domain="cma.api.sessions",
        action="create_start",
        extra={
            "environment_id": environment_id,
            "agent_form": "pinned" if isinstance(agent, dict) else "latest",
            "vault_count": len(vault_ids or []),
        },
    )
    session = client.beta.sessions.create(**payload)
    emit(
        domain="cma.api.sessions",
        action="create_done",
        extra={"session_id": session.id, "status": getattr(session, "status", "?")},
    )
    return session


def get_session(session_id: str) -> Any:
    """Retrieve a session's current state."""
    expect_kind(session_id, ResourceKind.SESSION)
    return get_client().beta.sessions.retrieve(session_id)


def archive_session(session_id: str) -> Any:
    """Archive a session — no new events accepted; history preserved."""
    expect_kind(session_id, ResourceKind.SESSION)
    emit(domain="cma.api.sessions", action="archive", extra={"session_id": session_id})
    return get_client().beta.sessions.archive(session_id)


def delete_session(session_id: str) -> Any:
    """Permanently delete a session.

    Per the docs: deletion fails on ``running`` sessions; send an interrupt
    first. The toolkit's auto-delete-after-idle workflow (§2.15.8) honors
    this — it always calls archive or interrupt before delete.
    """
    expect_kind(session_id, ResourceKind.SESSION)
    emit(domain="cma.api.sessions", action="delete", extra={"session_id": session_id})
    return get_client().beta.sessions.delete(session_id)
