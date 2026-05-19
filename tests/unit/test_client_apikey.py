"""Tests for cma.api.client API-key resolution.

Covers sentinel detection, the three-source resolution chain, and the
helper-command runner.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cma.api import client as client_mod
from cma.api.client import (
    _looks_like_real_key,
    _run_helper,
    _why_not_real_key,
)

# ---------------------------------------------------------------------------
# Sentinel detection
# ---------------------------------------------------------------------------


class TestLooksLikeRealKey:
    def test_accepts_realistic_key(self) -> None:
        # Length 80+, sk-ant- prefix.
        assert _looks_like_real_key("sk-ant-api03-" + "A" * 70)

    def test_rejects_dot_sentinel(self) -> None:
        assert not _looks_like_real_key("sk-ant-..")
        assert not _looks_like_real_key("sk-ant-...")

    def test_rejects_placeholder_sentinel(self) -> None:
        assert not _looks_like_real_key("sk-ant-placeholder")
        assert not _looks_like_real_key("sk-ant-PLACEHOLDER")
        assert not _looks_like_real_key("sk-ant-stub")
        assert not _looks_like_real_key("sk-ant-todo")
        assert not _looks_like_real_key("sk-ant-disabled")

    def test_rejects_x_padded_sentinel(self) -> None:
        assert not _looks_like_real_key("sk-ant-xxxxxxxxx")

    def test_rejects_too_short(self) -> None:
        assert not _looks_like_real_key("sk-ant-shortkey")  # under 30 chars

    def test_rejects_wrong_prefix(self) -> None:
        # OpenAI-style key shouldn't fool us.
        assert not _looks_like_real_key("sk-" + "A" * 100)
        assert not _looks_like_real_key("ant-api03-" + "A" * 70)

    def test_rejects_empty(self) -> None:
        assert not _looks_like_real_key("")


class TestWhyNotReal:
    def test_empty(self) -> None:
        assert "empty" in _why_not_real_key("")

    def test_wrong_prefix(self) -> None:
        msg = _why_not_real_key("not-a-key")
        assert "sk-ant-" in msg  # rejection mentions the expected prefix

    def test_too_short(self) -> None:
        msg = _why_not_real_key("sk-ant-tooshort")
        assert "short" in msg

    def test_sentinel(self) -> None:
        msg = _why_not_real_key("sk-ant-..")
        # Could be "too short" OR "matches sentinel" — both checks fire. We
        # just verify it doesn't claim "unknown rejection".
        assert "unknown" not in msg


# ---------------------------------------------------------------------------
# _run_helper — subprocess execution
# ---------------------------------------------------------------------------


class TestRunHelper:
    def test_returns_stdout_stripped(self) -> None:
        # 'echo hello' works on both cmd.exe and bash/sh.
        out = _run_helper("echo hello")
        assert out == "hello"

    def test_raises_on_nonzero_exit(self) -> None:
        # 'exit 1' is portable.
        with pytest.raises(RuntimeError, match="exited 1"):
            _run_helper("exit 1")

    def test_raises_on_empty_stdout(self) -> None:
        # Run something that exits 0 with no output.
        # On Windows cmd: `echo.` prints just newline → stripped to empty.
        # Use a more portable pattern: `python -c "pass"` exits 0, no stdout.
        with pytest.raises(RuntimeError, match="empty stdout"):
            _run_helper('python -c "pass"')


# ---------------------------------------------------------------------------
# _api_key — full resolution chain
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Clear the API key cache before every test in this module."""
    client_mod._api_key.cache_clear()
    yield
    client_mod._api_key.cache_clear()


class TestApiKeyResolution:
    def test_returns_real_env_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real = "sk-ant-api03-" + "A" * 70
        monkeypatch.setenv("ANTHROPIC_API_KEY", real)
        monkeypatch.delenv("CMA_API_KEY_HELPER", raising=False)
        assert client_mod._api_key() == real

    def test_skips_sentinel_env_falls_through(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Sentinel env var → falls through to CMA_API_KEY_HELPER."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-..")
        real = "sk-ant-api03-" + "B" * 70
        # Use a shell echo that emits the real key.
        monkeypatch.setenv("CMA_API_KEY_HELPER", f"echo {real}")
        # Force HOME to a temp dir so settings.json isn't found.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        assert client_mod._api_key() == real

    def test_helper_command_executed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        real = "sk-ant-api03-" + "C" * 70
        monkeypatch.setenv("CMA_API_KEY_HELPER", f"echo {real}")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        assert client_mod._api_key() == real

    def test_settings_json_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CMA_API_KEY_HELPER", raising=False)
        real = "sk-ant-api03-" + "D" * 70
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(
            f'{{"apiKeyHelper": "echo {real}"}}', encoding="utf-8"
        )
        with patch("cma.api.client.Path.home", return_value=tmp_path):
            assert client_mod._api_key() == real

    def test_helper_returning_sentinel_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """If the helper command itself returns a sentinel, we don't accept it."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("CMA_API_KEY_HELPER", "echo sk-ant-..")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        with (
            patch("cma.api.client.Path.home", return_value=tmp_path),
            pytest.raises(RuntimeError, match="Could not resolve"),
        ):
            client_mod._api_key()

    def test_all_sources_empty_raises_helpful_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CMA_API_KEY_HELPER", raising=False)
        with (
            patch("cma.api.client.Path.home", return_value=tmp_path),
            pytest.raises(RuntimeError) as exc_info,
        ):
            client_mod._api_key()
        msg = str(exc_info.value)
        assert "Could not resolve" in msg
        # Error message should mention each source we tried.
        assert "ANTHROPIC_API_KEY" in msg
        assert "CMA_API_KEY_HELPER" in msg
        assert "settings.json" in msg

    def test_env_takes_precedence_over_helper(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A real env-var key should win even when a helper is also configured."""
        env_key = "sk-ant-api03-" + "E" * 70
        helper_key = "sk-ant-api03-" + "F" * 70
        monkeypatch.setenv("ANTHROPIC_API_KEY", env_key)
        monkeypatch.setenv("CMA_API_KEY_HELPER", f"echo {helper_key}")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert client_mod._api_key() == env_key

    def test_cma_helper_takes_precedence_over_settings(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cma_key = "sk-ant-api03-" + "G" * 70
        settings_key = "sk-ant-api03-" + "H" * 70
        monkeypatch.setenv("CMA_API_KEY_HELPER", f"echo {cma_key}")
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(
            f'{{"apiKeyHelper": "echo {settings_key}"}}', encoding="utf-8"
        )
        with patch("cma.api.client.Path.home", return_value=tmp_path):
            assert client_mod._api_key() == cma_key


class TestSettingsJsonHelper:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        with patch("cma.api.client.Path.home", return_value=tmp_path):
            assert client_mod._claude_code_settings_helper() is None

    def test_no_helper_field_returns_none(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")
        with patch("cma.api.client.Path.home", return_value=tmp_path):
            assert client_mod._claude_code_settings_helper() is None

    def test_returns_helper_string(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(
            '{"apiKeyHelper": "echo abc"}', encoding="utf-8"
        )
        with patch("cma.api.client.Path.home", return_value=tmp_path):
            assert client_mod._claude_code_settings_helper() == "echo abc"

    def test_unparseable_json_returns_none(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("not json", encoding="utf-8")
        with patch("cma.api.client.Path.home", return_value=tmp_path):
            assert client_mod._claude_code_settings_helper() is None
