"""Tests for cma.core.identifiers."""

from __future__ import annotations

import pytest

from cma.core.identifiers import ResourceKind, expect_kind, kind_of


class TestKindOf:
    def test_agent_prefix(self) -> None:
        assert kind_of("agent_01HqR2k") is ResourceKind.AGENT

    def test_session_prefix(self) -> None:
        assert kind_of("sesn_01ABC") is ResourceKind.SESSION

    def test_environment_prefix(self) -> None:
        assert kind_of("envrn_01XYZ") is ResourceKind.ENVIRONMENT

    def test_vault_prefix(self) -> None:
        assert kind_of("vlt_01abc") is ResourceKind.VAULT

    def test_vault_credential_prefix(self) -> None:
        assert kind_of("vcrd_01abc") is ResourceKind.VAULT_CREDENTIAL

    def test_outcome_prefix(self) -> None:
        assert kind_of("outc_01abc") is ResourceKind.OUTCOME

    def test_session_thread_prefix(self) -> None:
        assert kind_of("sth_01abc") is ResourceKind.SESSION_THREAD

    def test_session_event_prefix(self) -> None:
        assert kind_of("sevt_01abc") is ResourceKind.SESSION_EVENT

    def test_unknown_prefix_returns_none(self) -> None:
        assert kind_of("xyzzy_01abc") is None

    def test_no_underscore_returns_none(self) -> None:
        assert kind_of("noprefix") is None

    def test_empty_string_returns_none(self) -> None:
        assert kind_of("") is None


class TestExpectKind:
    def test_returns_id_on_match(self) -> None:
        assert expect_kind("agent_01ABC", ResourceKind.AGENT) == "agent_01ABC"

    def test_raises_on_wrong_prefix(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            expect_kind("sesn_01ABC", ResourceKind.AGENT)

    def test_raises_on_no_prefix(self) -> None:
        with pytest.raises(ValueError, match="no recognisable prefix"):
            expect_kind("nope", ResourceKind.AGENT)
