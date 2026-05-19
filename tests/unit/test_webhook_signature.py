"""Tests for cma.webhook.signature.

The Svix signature scheme is HMAC-SHA256 over ``{id}.{timestamp}.{body}``
with the base64-decoded secret. We test against a known-good roundtrip
(produce signature, verify it) plus every failure path.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest

from cma.webhook.signature import (
    DEFAULT_REPLAY_WINDOW_SECONDS,
    WebhookHeaders,
    WebhookSignatureError,
    verify_signature,
)


# Helper: produce a known-good signature so tests don't depend on the
# implementation under test for their fixtures.
def _make_sig(*, secret_raw: bytes, msg_id: str, ts: str, body: bytes) -> str:
    """Compute the v1 signature the same way Svix does."""
    to_sign = f"{msg_id}.{ts}.".encode() + body
    mac = hmac.new(secret_raw, to_sign, hashlib.sha256).digest()
    return base64.b64encode(mac).decode("ascii")


SECRET_RAW = b"super-secret-32-bytes-of-entropy"
SECRET = "whsec_" + base64.b64encode(SECRET_RAW).decode("ascii")


class TestHeaderParsing:
    def test_lowercase_headers_pass(self) -> None:
        headers = WebhookHeaders.from_mapping(
            {
                "webhook-id": "msg_123",
                "webhook-timestamp": "1700000000",
                "webhook-signature": "v1,abc",
            }
        )
        assert headers.webhook_id == "msg_123"

    def test_mixed_case_headers_pass(self) -> None:
        headers = WebhookHeaders.from_mapping(
            {
                "Webhook-Id": "msg_123",
                "Webhook-Timestamp": "1700000000",
                "Webhook-Signature": "v1,abc",
            }
        )
        assert headers.webhook_id == "msg_123"

    def test_missing_header_raises(self) -> None:
        with pytest.raises(WebhookSignatureError, match="missing required"):
            WebhookHeaders.from_mapping(
                {"webhook-id": "msg_1", "webhook-timestamp": "1700000000"}
            )


class TestSignatureRoundtrip:
    def test_good_signature_verifies(self) -> None:
        ts = "1700000000"
        body = b'{"type":"test","data":{}}'
        sig = _make_sig(
            secret_raw=SECRET_RAW, msg_id="msg_1", ts=ts, body=body
        )
        verify_signature(
            body=body,
            headers=WebhookHeaders(
                webhook_id="msg_1",
                webhook_timestamp=ts,
                webhook_signature=f"v1,{sig}",
            ),
            secret=SECRET,
            now=float(ts),
        )

    def test_secret_without_prefix_also_works(self) -> None:
        ts = "1700000000"
        body = b"{}"
        sig = _make_sig(
            secret_raw=SECRET_RAW, msg_id="m", ts=ts, body=body
        )
        bare_secret = base64.b64encode(SECRET_RAW).decode("ascii")
        verify_signature(
            body=body,
            headers=WebhookHeaders(
                webhook_id="m",
                webhook_timestamp=ts,
                webhook_signature=f"v1,{sig}",
            ),
            secret=bare_secret,
            now=float(ts),
        )

    def test_multiple_versions_first_matches(self) -> None:
        ts = "1700000000"
        body = b"{}"
        sig = _make_sig(secret_raw=SECRET_RAW, msg_id="m", ts=ts, body=body)
        verify_signature(
            body=body,
            headers=WebhookHeaders(
                webhook_id="m",
                webhook_timestamp=ts,
                webhook_signature=f"v1,{sig} v1,otherbase64",
            ),
            secret=SECRET,
            now=float(ts),
        )

    def test_multiple_versions_second_matches(self) -> None:
        ts = "1700000000"
        body = b"{}"
        sig = _make_sig(secret_raw=SECRET_RAW, msg_id="m", ts=ts, body=body)
        verify_signature(
            body=body,
            headers=WebhookHeaders(
                webhook_id="m",
                webhook_timestamp=ts,
                # Real-world rotation case: old key first, new key second.
                webhook_signature=f"v1,oldwrongsig v1,{sig}",
            ),
            secret=SECRET,
            now=float(ts),
        )

    def test_v0_signature_ignored(self) -> None:
        ts = "1700000000"
        body = b"{}"
        sig = _make_sig(secret_raw=SECRET_RAW, msg_id="m", ts=ts, body=body)
        # Only v0 supplied — we don't support v0.
        with pytest.raises(WebhookSignatureError, match="no v1 signature"):
            verify_signature(
                body=body,
                headers=WebhookHeaders(
                    webhook_id="m",
                    webhook_timestamp=ts,
                    webhook_signature=f"v0,{sig}",
                ),
                secret=SECRET,
                now=float(ts),
            )


class TestSignatureFailures:
    def test_bad_body_rejected(self) -> None:
        ts = "1700000000"
        sig = _make_sig(
            secret_raw=SECRET_RAW, msg_id="m", ts=ts, body=b"original"
        )
        with pytest.raises(WebhookSignatureError):
            verify_signature(
                body=b"tampered",
                headers=WebhookHeaders(
                    webhook_id="m",
                    webhook_timestamp=ts,
                    webhook_signature=f"v1,{sig}",
                ),
                secret=SECRET,
                now=float(ts),
            )

    def test_bad_secret_rejected(self) -> None:
        ts = "1700000000"
        body = b"{}"
        sig = _make_sig(
            secret_raw=SECRET_RAW, msg_id="m", ts=ts, body=body
        )
        wrong = "whsec_" + base64.b64encode(b"different-key").decode("ascii")
        with pytest.raises(WebhookSignatureError):
            verify_signature(
                body=body,
                headers=WebhookHeaders(
                    webhook_id="m",
                    webhook_timestamp=ts,
                    webhook_signature=f"v1,{sig}",
                ),
                secret=wrong,
                now=float(ts),
            )

    def test_bad_msg_id_rejected(self) -> None:
        ts = "1700000000"
        body = b"{}"
        sig = _make_sig(
            secret_raw=SECRET_RAW, msg_id="original", ts=ts, body=body
        )
        with pytest.raises(WebhookSignatureError):
            verify_signature(
                body=body,
                headers=WebhookHeaders(
                    webhook_id="different",
                    webhook_timestamp=ts,
                    webhook_signature=f"v1,{sig}",
                ),
                secret=SECRET,
                now=float(ts),
            )

    def test_stale_timestamp_rejected(self) -> None:
        ts = "1700000000"
        body = b"{}"
        sig = _make_sig(
            secret_raw=SECRET_RAW, msg_id="m", ts=ts, body=body
        )
        far_future_now = float(ts) + DEFAULT_REPLAY_WINDOW_SECONDS + 60
        with pytest.raises(WebhookSignatureError, match="window"):
            verify_signature(
                body=body,
                headers=WebhookHeaders(
                    webhook_id="m",
                    webhook_timestamp=ts,
                    webhook_signature=f"v1,{sig}",
                ),
                secret=SECRET,
                now=far_future_now,
            )

    def test_future_timestamp_rejected(self) -> None:
        # Symmetric window: timestamps in the future are also rejected.
        now = 1700000000.0
        future_ts = str(int(now + DEFAULT_REPLAY_WINDOW_SECONDS + 60))
        body = b"{}"
        sig = _make_sig(
            secret_raw=SECRET_RAW, msg_id="m", ts=future_ts, body=body
        )
        with pytest.raises(WebhookSignatureError, match="window"):
            verify_signature(
                body=body,
                headers=WebhookHeaders(
                    webhook_id="m",
                    webhook_timestamp=future_ts,
                    webhook_signature=f"v1,{sig}",
                ),
                secret=SECRET,
                now=now,
            )

    def test_non_integer_timestamp_rejected(self) -> None:
        with pytest.raises(WebhookSignatureError, match="not an integer"):
            verify_signature(
                body=b"{}",
                headers=WebhookHeaders(
                    webhook_id="m",
                    webhook_timestamp="not-a-number",
                    webhook_signature="v1,xxx",
                ),
                secret=SECRET,
                now=time.time(),
            )

    def test_empty_secret_after_decode_rejected(self) -> None:
        with pytest.raises(WebhookSignatureError, match="empty"):
            verify_signature(
                body=b"{}",
                headers=WebhookHeaders(
                    webhook_id="m",
                    webhook_timestamp="1700000000",
                    webhook_signature="v1,xxx",
                ),
                secret="whsec_",  # decodes to empty bytes
                now=1700000000.0,
            )

    def test_non_base64_secret_rejected(self) -> None:
        with pytest.raises(WebhookSignatureError, match="base64"):
            verify_signature(
                body=b"{}",
                headers=WebhookHeaders(
                    webhook_id="m",
                    webhook_timestamp="1700000000",
                    webhook_signature="v1,xxx",
                ),
                secret="whsec_!!!not-base64!!!",
                now=1700000000.0,
            )

    def test_empty_signature_header_rejected(self) -> None:
        with pytest.raises(WebhookSignatureError, match="no v1 signature"):
            verify_signature(
                body=b"{}",
                headers=WebhookHeaders(
                    webhook_id="m",
                    webhook_timestamp="1700000000",
                    webhook_signature="",
                ),
                secret=SECRET,
                now=1700000000.0,
            )
