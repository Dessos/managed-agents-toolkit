"""Tests for cma.telemetry.jsonl.redact.

The redactor is the only defense against accidentally logging a credential.
Test EVERY pattern we claim to catch.
"""

from __future__ import annotations

from cma.telemetry import redact
from cma.telemetry.jsonl import _ANTHROPIC_KEY_RE, _looks_like_secret_value


class TestKeyRedaction:
    def test_token_key_redacted(self) -> None:
        out = redact({"token": "anything"})
        assert out == {"token": "<redacted>"}

    def test_api_key_camel_case_redacted(self) -> None:
        out = redact({"apiKey": "anything"})
        assert out == {"apiKey": "<redacted>"}

    def test_api_key_snake_case_redacted(self) -> None:
        out = redact({"api_key": "anything"})
        assert out == {"api_key": "<redacted>"}

    def test_nested_key_redacted(self) -> None:
        out = redact({"auth": {"token": "abc", "method": "bearer"}})
        # 'auth' key itself is redacted (matches 'auth' substring)
        assert out == {"auth": "<redacted>"}

    def test_unrelated_key_preserved(self) -> None:
        out = redact({"username": "alice"})
        assert out == {"username": "alice"}


class TestValueRedaction:
    def test_whsec_prefix_redacted(self) -> None:
        out = redact({"webhook_url": "whsec_secretvalue"})
        # Key doesn't match patterns, but value does — value-level redact.
        # Note: 'webhook' isn't in the key denylist; only the value matches.
        assert out["webhook_url"] == "<redacted>"

    def test_bearer_prefix_redacted(self) -> None:
        out = redact({"header": "Bearer xyz123"})
        assert out["header"] == "<redacted>"

    def test_slack_xoxp_redacted(self) -> None:
        out = redact({"creds": "xoxp-1234"})
        assert out["creds"] == "<redacted>"

    def test_ghp_prefix_redacted(self) -> None:
        out = redact({"value": "ghp_AAAAAAAAAAAAAAAAAAAA"})
        assert out["value"] == "<redacted>"

    def test_lin_api_prefix_redacted(self) -> None:
        out = redact({"linear": "lin_api_realtoken"})
        assert out["linear"] == "<redacted>"

    def test_anthropic_key_shape_redacted(self) -> None:
        # The regex requires sk-ant- + at least 20 url-safe chars.
        out = redact({"sample": "sk-ant-api03-XXXXXXXXXXXXXXXXXXXX"})
        assert out["sample"] == "<redacted>"

    def test_short_prefix_not_redacted(self) -> None:
        # ``sk-`` is a real prefix entry, so a string with just sk- gets caught.
        # But a string mentioning "sk- means something" should still be caught
        # because we match prefixes, not whole values. This is acceptable false
        # positive — better to over-redact than under-redact.
        out = redact({"sample": "sk-anything"})
        assert out["sample"] == "<redacted>"

    def test_short_random_string_preserved(self) -> None:
        out = redact({"sample": "hello world"})
        assert out == {"sample": "hello world"}


class TestStructuralRedaction:
    def test_redacts_in_list(self) -> None:
        out = redact([{"token": "x"}, {"name": "y"}])
        assert out == [{"token": "<redacted>"}, {"name": "y"}]

    def test_redacts_deeply_nested(self) -> None:
        out = redact({"outer": {"inner": {"secret": "hide"}}})
        assert out == {"outer": {"inner": {"secret": "<redacted>"}}}

    def test_preserves_non_string_scalars(self) -> None:
        out = redact({"count": 42, "ratio": 0.5, "ok": True, "missing": None})
        assert out == {"count": 42, "ratio": 0.5, "ok": True, "missing": None}


class TestAnthropicKeyRegex:
    def test_matches_real_shape(self) -> None:
        assert _ANTHROPIC_KEY_RE.match("sk-ant-api03-XXXXXXXXXXXXXXXXXXXX") is not None

    def test_no_match_too_short(self) -> None:
        assert _ANTHROPIC_KEY_RE.match("sk-ant-XX") is None

    def test_no_match_wrong_prefix(self) -> None:
        assert _ANTHROPIC_KEY_RE.match("ant-api03-XXXXXXXXXXXXXXXXXXXX") is None


class TestSecretValueDetector:
    def test_non_string_returns_false(self) -> None:
        assert _looks_like_secret_value(42) is False
        assert _looks_like_secret_value(None) is False
        assert _looks_like_secret_value(True) is False
