"""Tests for cma.webhook.events.

Schema is intentionally permissive (extra="allow") on the event payload
side because the API surface evolves. Tests verify the bits we DO assume
about — envelope shape, the four parsers, and the ``total_tokens`` helper.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cma.webhook.events import (
    EVENT_TYPE_SESSION_STATUS_IDLED,
    EVENT_TYPE_SESSION_STATUS_TERMINATED,
    EVENT_TYPE_VAULT_REFRESH_FAILED,
    SessionUsage,
    WebhookEnvelope,
    parse_session_status_idled,
    parse_session_status_terminated,
    parse_vault_refresh_failed,
)


class TestEnvelope:
    def test_minimal_envelope_parses(self) -> None:
        env = WebhookEnvelope.model_validate({"type": "x.y", "data": {}})
        assert env.type == "x.y"
        assert env.data == {}

    def test_envelope_preserves_extra_fields(self) -> None:
        env = WebhookEnvelope.model_validate(
            {"type": "x.y", "data": {}, "delivery_id": "del_1", "api_version": "v1"}
        )
        # Extra fields are accepted (extra="allow"); we don't assert their
        # placement, just that parsing succeeded.
        assert env.type == "x.y"

    def test_missing_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            WebhookEnvelope.model_validate({"data": {}})


class TestSessionUsage:
    def test_total_tokens(self) -> None:
        usage = SessionUsage(
            input_tokens=10,
            output_tokens=20,
            cache_creation_input_tokens=5,
            cache_read_input_tokens=2,
        )
        assert usage.total_tokens == 37

    def test_defaults_zero(self) -> None:
        usage = SessionUsage()
        assert usage.total_tokens == 0

    def test_extra_fields_tolerated(self) -> None:
        # Backend adds a new bucket — we shouldn't crash.
        usage = SessionUsage.model_validate(
            {
                "input_tokens": 1,
                "output_tokens": 2,
                "new_thinking_tokens": 99,
            }
        )
        assert usage.input_tokens == 1


class TestParsers:
    def test_session_status_idled_full(self) -> None:
        env = WebhookEnvelope.model_validate(
            {
                "type": EVENT_TYPE_SESSION_STATUS_IDLED,
                "data": {
                    "session_id": "sess_abc",
                    "project": "example-project",
                    "model": "claude-opus-4-7",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                    "status": "idled",
                },
            }
        )
        parsed = parse_session_status_idled(env)
        assert parsed.session_id == "sess_abc"
        assert parsed.project == "example-project"
        assert parsed.usage is not None
        assert parsed.usage.input_tokens == 100

    def test_session_status_idled_minimal(self) -> None:
        # The receiver tolerates missing project/model/usage and treats
        # them as "incomplete" — the parser itself only requires session_id.
        env = WebhookEnvelope.model_validate(
            {
                "type": EVENT_TYPE_SESSION_STATUS_IDLED,
                "data": {"session_id": "sess_x"},
            }
        )
        parsed = parse_session_status_idled(env)
        assert parsed.session_id == "sess_x"
        assert parsed.project is None
        assert parsed.usage is None

    def test_session_status_idled_missing_session_id_raises(self) -> None:
        env = WebhookEnvelope.model_validate(
            {"type": EVENT_TYPE_SESSION_STATUS_IDLED, "data": {}}
        )
        with pytest.raises(ValidationError):
            parse_session_status_idled(env)

    def test_session_status_terminated(self) -> None:
        env = WebhookEnvelope.model_validate(
            {
                "type": EVENT_TYPE_SESSION_STATUS_TERMINATED,
                "data": {
                    "session_id": "sess_y",
                    "reason": "budget_exceeded",
                },
            }
        )
        parsed = parse_session_status_terminated(env)
        assert parsed.reason == "budget_exceeded"

    def test_vault_refresh_failed(self) -> None:
        env = WebhookEnvelope.model_validate(
            {
                "type": EVENT_TYPE_VAULT_REFRESH_FAILED,
                "data": {
                    "vault_id": "vault_1",
                    "credential_id": "cred_2",
                    "error": "401 from upstream",
                },
            }
        )
        parsed = parse_vault_refresh_failed(env)
        assert parsed.vault_id == "vault_1"
        assert parsed.error == "401 from upstream"
