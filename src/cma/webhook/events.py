"""Pydantic v2 models for the webhook events the toolkit subscribes to.

The Managed Agents event surface is under active development; the toolkit
models the *envelope* strictly (we need ``type`` and ``data`` to dispatch)
but allows unknown fields inside each event payload (``extra="allow"``)
so a backend-side addition doesn't break parsing.

Only events listed in :attr:`cma.core.config.WebhookConfig.subscribe_events`
need explicit parsers below. Anything else is parsed as a generic envelope
and ignored (telemetry-logged for visibility).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Envelope (common shape for every webhook delivery)
# ---------------------------------------------------------------------------


class WebhookEnvelope(BaseModel):
    """Top-level shape: ``{type: "...", data: {...}}``.

    Extra fields preserved so future top-level keys (e.g. delivery_id,
    api_version) don't break parsing.
    """

    type: str = Field(..., description="Event type, e.g. 'session.status_idled'.")
    data: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Per-event payload models
# ---------------------------------------------------------------------------


class SessionUsage(BaseModel):
    """Token usage block, shape borrowed from the SDK's UsageSnapshot."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_5m_tokens: int = 0
    cache_creation_1h_tokens: int = 0

    model_config = ConfigDict(extra="allow")

    @property
    def total_tokens(self) -> int:
        """Sum of every reported token bucket (informational only)."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


class SessionStatusIdledData(BaseModel):
    """Payload of a ``session.status_idled`` event."""

    session_id: str
    project: str | None = None
    model: str | None = None
    usage: SessionUsage | None = None
    status: str | None = None

    model_config = ConfigDict(extra="allow")


class SessionStatusTerminatedData(BaseModel):
    """Payload of a ``session.status_terminated`` event."""

    session_id: str
    project: str | None = None
    model: str | None = None
    usage: SessionUsage | None = None
    reason: str | None = None

    model_config = ConfigDict(extra="allow")


class VaultCredentialRefreshFailedData(BaseModel):
    """Payload of a ``vault_credential.refresh_failed`` event."""

    vault_id: str
    credential_id: str
    error: str | None = None

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------


EVENT_TYPE_SESSION_STATUS_IDLED: Literal["session.status_idled"] = "session.status_idled"
EVENT_TYPE_SESSION_STATUS_TERMINATED: Literal["session.status_terminated"] = (
    "session.status_terminated"
)
EVENT_TYPE_VAULT_REFRESH_FAILED: Literal["vault_credential.refresh_failed"] = (
    "vault_credential.refresh_failed"
)


def parse_session_status_idled(envelope: WebhookEnvelope) -> SessionStatusIdledData:
    """Parse the ``data`` block of a ``session.status_idled`` envelope."""
    return SessionStatusIdledData.model_validate(envelope.data)


def parse_session_status_terminated(
    envelope: WebhookEnvelope,
) -> SessionStatusTerminatedData:
    """Parse the ``data`` block of a ``session.status_terminated`` envelope."""
    return SessionStatusTerminatedData.model_validate(envelope.data)


def parse_vault_refresh_failed(
    envelope: WebhookEnvelope,
) -> VaultCredentialRefreshFailedData:
    """Parse the ``data`` block of a ``vault_credential.refresh_failed`` envelope."""
    return VaultCredentialRefreshFailedData.model_validate(envelope.data)
