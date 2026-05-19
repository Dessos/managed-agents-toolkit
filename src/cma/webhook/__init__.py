"""Webhook receiver — FastAPI app + budget kill switch.

Anthropic's Managed Agents posts events (``session.status_idled``,
``session.outcome_evaluation_ended``, ``vault_credential.refresh_failed``,
etc.) to a public HTTPS endpoint declared in :class:`cma.core.config.WebhookConfig`.
This package owns:

* :mod:`cma.webhook.signature` — Svix-style HMAC-SHA256 verification.
* :mod:`cma.webhook.events`    — Pydantic v2 envelope + per-event models.
* :mod:`cma.webhook.policy`    — operator-authored kill-switch decision.
* :mod:`cma.webhook.actions`   — pluggable action executor (SDK / null).
* :mod:`cma.webhook.receiver`  — :func:`build_app` FastAPI factory.

The transport (Cloudflare Tunnel) is out of scope; the toolkit only ships
the receiver. CLI: ``cma webhook serve``.
"""

from cma.webhook.signature import (
    DEFAULT_REPLAY_WINDOW_SECONDS,
    WebhookHeaders,
    WebhookSignatureError,
    verify_signature,
)

# build_app is intentionally NOT re-exported — it pulls in fastapi at import
# time. Import via ``from cma.webhook.receiver import build_app`` from
# code paths that have the ``[webhook]`` extra installed.

__all__ = [
    "DEFAULT_REPLAY_WINDOW_SECONDS",
    "WebhookHeaders",
    "WebhookSignatureError",
    "verify_signature",
]
