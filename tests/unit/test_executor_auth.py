"""Tests for cma.executor.auth — bearer token storage + verification."""

from __future__ import annotations

from pathlib import Path

from cma.executor.auth import (
    extract_bearer_from_header,
    load_token,
    rotate_token,
    token_path,
    verify_bearer,
)


class TestTokenPath:
    def test_canonical_location(self, tmp_path: Path) -> None:
        p = token_path(tmp_path)
        assert p == tmp_path / ".managed-agents" / ".state" / "executor_token"


class TestLoadToken:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_token(tmp_path) is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        path = token_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("   \n", encoding="utf-8")
        assert load_token(tmp_path) is None

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        path = token_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("  abc123  \n\n", encoding="utf-8")
        assert load_token(tmp_path) == "abc123"


class TestRotateToken:
    def test_creates_token_file(self, tmp_path: Path) -> None:
        token = rotate_token(tmp_path)
        assert len(token) > 0
        assert load_token(tmp_path) == token

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        first = rotate_token(tmp_path)
        second = rotate_token(tmp_path)
        assert first != second
        assert load_token(tmp_path) == second

    def test_token_is_url_safe(self, tmp_path: Path) -> None:
        token = rotate_token(tmp_path)
        # url-safe means a-zA-Z0-9_- only
        import re

        assert re.match(r"^[A-Za-z0-9_-]+$", token)


class TestVerifyBearer:
    def test_returns_false_for_missing_token_file(self, tmp_path: Path) -> None:
        assert not verify_bearer("any-presented-token", workspace_root=tmp_path)

    def test_returns_false_for_none_presented(self, tmp_path: Path) -> None:
        rotate_token(tmp_path)
        assert not verify_bearer(None, workspace_root=tmp_path)

    def test_returns_false_for_empty_presented(self, tmp_path: Path) -> None:
        rotate_token(tmp_path)
        assert not verify_bearer("", workspace_root=tmp_path)

    def test_returns_true_for_matching_token(self, tmp_path: Path) -> None:
        token = rotate_token(tmp_path)
        assert verify_bearer(token, workspace_root=tmp_path)

    def test_returns_false_after_rotation(self, tmp_path: Path) -> None:
        old = rotate_token(tmp_path)
        rotate_token(tmp_path)  # rotate again
        assert not verify_bearer(old, workspace_root=tmp_path)

    def test_returns_false_for_wrong_token(self, tmp_path: Path) -> None:
        rotate_token(tmp_path)
        assert not verify_bearer("not-the-real-token", workspace_root=tmp_path)


class TestExtractBearer:
    def test_well_formed(self) -> None:
        assert extract_bearer_from_header("Bearer abc123") == "abc123"

    def test_extra_whitespace_ok(self) -> None:
        assert extract_bearer_from_header("  Bearer   abc123  ") == "abc123"

    def test_case_insensitive_scheme(self) -> None:
        assert extract_bearer_from_header("bearer abc") == "abc"
        assert extract_bearer_from_header("BEARER abc") == "abc"

    def test_returns_none_for_other_scheme(self) -> None:
        assert extract_bearer_from_header("Basic xyz") is None

    def test_returns_none_for_no_scheme(self) -> None:
        assert extract_bearer_from_header("just-a-token") is None

    def test_returns_none_for_empty(self) -> None:
        assert extract_bearer_from_header(None) is None
        assert extract_bearer_from_header("") is None

    def test_returns_none_for_scheme_only(self) -> None:
        assert extract_bearer_from_header("Bearer") is None
        assert extract_bearer_from_header("Bearer ") is None
