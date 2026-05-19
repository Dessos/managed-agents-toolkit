"""Svix-compatible HMAC-SHA256 webhook signature verification.

Anthropic delivers webhooks via Svix. The signature scheme is:

* Headers: ``webhook-id``, ``webhook-timestamp`` (unix seconds),
  ``webhook-signature`` (space-separated list of ``v1,<base64-sig>`` pairs;
  multiple signatures allowed during key rotation).
* MAC input: ``f"{id}.{timestamp}.{body_str}"`` (body is the raw bytes).
* Key: the bytes after the ``whsec_`` prefix, base64-decoded.
* Replay window: reject timestamps older than 5 minutes from now (or newer
  than 5 minutes in the future, allowing for clock skew).

Constant-time comparison via :func:`hmac.compare_digest`.

This module has zero project dependencies — it can be reused by any
service that consumes Svix-style webhooks.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

DEFAULT_REPLAY_WINDOW_SECONDS: Final[int] = 5 * 60
_WHSEC_PREFIX: Final[str] = "whsec_"
_SUPPORTED_VERSION: Final[str] = "v1"


class WebhookSignatureError(ValueError):
    """Raised when a webhook fails verification.

    Generic on purpose: signature mismatch, stale/future timestamp,
    malformed header, and unknown signature version all surface as this
    one error so the receiver can return a single HTTP 400 / 401 without
    leaking which check failed (defense in depth against probing).
    """


@dataclass(frozen=True, slots=True)
class WebhookHeaders:
    """The three headers Svix attaches to every delivery."""

    webhook_id: str
    webhook_timestamp: str
    webhook_signature: str

    @classmethod
    def from_mapping(cls, headers: Mapping[str, str]) -> WebhookHeaders:
        """Pull the three header values from a case-insensitive mapping.

        FastAPI's ``request.headers`` already lowercases keys. We accept
        any mapping but normalize keys to lowercase ourselves so callers
        don't have to think about it.
        """
        lower = {k.lower(): v for k, v in headers.items()}
        try:
            return cls(
                webhook_id=lower["webhook-id"],
                webhook_timestamp=lower["webhook-timestamp"],
                webhook_signature=lower["webhook-signature"],
            )
        except KeyError as exc:
            raise WebhookSignatureError(
                f"missing required webhook header: {exc.args[0]}"
            ) from exc


def _decode_secret(secret: str) -> bytes:
    """Strip the ``whsec_`` prefix and base64-decode the rest.

    Tolerates the prefix being absent (some operators paste only the
    base64 portion). Validates the result is non-empty.
    """
    encoded = (
        secret[len(_WHSEC_PREFIX):] if secret.startswith(_WHSEC_PREFIX) else secret
    )
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise WebhookSignatureError("signing secret is not valid base64") from exc
    if not decoded:
        raise WebhookSignatureError("signing secret decodes to empty bytes")
    return decoded


def _check_timestamp(
    timestamp_str: str, *, replay_window_seconds: int, now: float
) -> None:
    """Raise if the timestamp is outside the replay window."""
    try:
        ts = int(timestamp_str)
    except ValueError as exc:
        raise WebhookSignatureError(
            f"webhook-timestamp is not an integer: {timestamp_str!r}"
        ) from exc
    delta = abs(now - ts)
    if delta > replay_window_seconds:
        raise WebhookSignatureError(
            f"webhook-timestamp {ts} is {delta:.0f}s from now (window "
            f"{replay_window_seconds}s)"
        )


def _expected_signature(
    *, secret_bytes: bytes, msg_id: str, timestamp: str, body: bytes
) -> str:
    """Compute the base64-encoded HMAC-SHA256 for a single (id, ts, body)."""
    to_sign = f"{msg_id}.{timestamp}.".encode() + body
    mac = hmac.new(secret_bytes, to_sign, hashlib.sha256).digest()
    return base64.b64encode(mac).decode("ascii")


def verify_signature(
    *,
    body: bytes,
    headers: WebhookHeaders,
    secret: str,
    replay_window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS,
    now: float | None = None,
) -> None:
    """Verify the Svix signature on a webhook delivery.

    Raises :class:`WebhookSignatureError` on any failure. Returns ``None``
    on success — callers should treat any exception as a 400/401.

    :param body: Raw request body bytes (NOT a parsed/re-serialized JSON
        — re-serializing destroys byte-for-byte equality).
    :param headers: The three Svix headers.
    :param secret: The signing secret. ``whsec_<base64>`` form preferred;
        bare base64 also accepted.
    :param replay_window_seconds: Max ± skew from ``now``. Defaults to 5
        minutes (Svix default).
    :param now: Override current time (for tests). ``None`` uses
        :func:`time.time`.
    """
    if now is None:
        now = time.time()
    _check_timestamp(
        headers.webhook_timestamp,
        replay_window_seconds=replay_window_seconds,
        now=now,
    )
    secret_bytes = _decode_secret(secret)
    expected = _expected_signature(
        secret_bytes=secret_bytes,
        msg_id=headers.webhook_id,
        timestamp=headers.webhook_timestamp,
        body=body,
    )
    # Header may carry multiple "v1,<sig>" pairs separated by spaces — e.g.
    # during key rotation. Accept if any v1 entry matches.
    for entry in headers.webhook_signature.split(" "):
        entry = entry.strip()
        if not entry:
            continue
        version, _, sig = entry.partition(",")
        if version != _SUPPORTED_VERSION or not sig:
            continue
        if hmac.compare_digest(sig, expected):
            return
    raise WebhookSignatureError("no v1 signature matched the expected MAC")
