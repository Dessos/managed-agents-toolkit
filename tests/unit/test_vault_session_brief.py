"""Unit tests for ``cma.vault.session_brief`` — Hook 1."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from cma.vault import session_brief as h1


@pytest.fixture(autouse=True)
def silence_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMA_VAULT_HOOK_LOG", str(tmp_path / "telemetry.jsonl"))


def _make_brief(vault: Path, updated: datetime) -> None:
    (vault / "context").mkdir(parents=True, exist_ok=True)
    iso = updated.strftime("%Y-%m-%dT%H:%M:%SZ")
    (vault / "context" / "ai-session-brief.md").write_text(
        f"---\n"
        f"type: context\n"
        f"status: active\n"
        f"updated: {iso}\n"
        f"---\n\n"
        f"# Session brief content\n\nProject sprint: vault build.\n",
        encoding="utf-8",
    )


def _make_adr(vault: Path, name: str, created: str, title: str) -> None:
    (vault / "decisions").mkdir(parents=True, exist_ok=True)
    (vault / "decisions" / name).write_text(
        f"---\n"
        f"type: decision\n"
        f"status: active\n"
        f"created: {created}\n"
        f"updated: {created}\n"
        f"tags: [adr]\n"
        f"---\n\n"
        f"# {title}\n",
        encoding="utf-8",
    )


class TestBuildContext:
    def test_no_brief_returns_empty(self, tmp_path: Path) -> None:
        ctx, warn = h1.build_context(tmp_path)
        assert ctx == ""
        assert warn is None

    def test_returns_brief_content(self, tmp_path: Path) -> None:
        vault = tmp_path / "docs" / "vault"
        _make_brief(vault, datetime.now(UTC))
        ctx, warn = h1.build_context(tmp_path)
        assert "VAULT SESSION BRIEF" in ctx
        assert "vault build" in ctx
        assert warn is None  # not stale

    def test_emits_staleness_warning(self, tmp_path: Path) -> None:
        vault = tmp_path / "docs" / "vault"
        old = datetime.now(UTC) - timedelta(days=30)
        _make_brief(vault, old)
        ctx, warn = h1.build_context(tmp_path)
        assert ctx  # brief still loaded
        assert warn is not None
        assert "30 days old" in warn or "29 days old" in warn

    def test_includes_recent_adrs(self, tmp_path: Path) -> None:
        vault = tmp_path / "docs" / "vault"
        _make_brief(vault, datetime.now(UTC))
        # Create 3 ADRs with different created dates
        _make_adr(vault, "2026-05-18-old.md", "2026-05-18T00:00:00Z", "Old ADR")
        _make_adr(vault, "2026-05-19-mid.md", "2026-05-19T00:00:00Z", "Mid ADR")
        _make_adr(vault, "2026-05-20-new.md", "2026-05-20T00:00:00Z", "New ADR")
        ctx, _ = h1.build_context(tmp_path)
        # Most recent should appear first, all present.
        assert "New ADR" in ctx
        assert "Mid ADR" in ctx
        assert "Old ADR" in ctx
        # Recency ordering: New should appear before Old in the string.
        assert ctx.index("New ADR") < ctx.index("Old ADR")

    def test_skips_index_and_readme(self, tmp_path: Path) -> None:
        vault = tmp_path / "docs" / "vault"
        _make_brief(vault, datetime.now(UTC))
        (vault / "decisions" / "_INDEX.md").parent.mkdir(parents=True, exist_ok=True)
        (vault / "decisions" / "_INDEX.md").write_text(
            "---\ntype: meta\ncreated: 2026-05-20T00:00:00Z\n---\n# Index\n",
            encoding="utf-8",
        )
        (vault / "decisions" / "_README.md").write_text(
            "---\ntype: meta\ncreated: 2026-05-20T00:00:00Z\n---\n# Readme\n",
            encoding="utf-8",
        )
        ctx, _ = h1.build_context(tmp_path)
        assert "Index" not in ctx
        assert "Readme" not in ctx

    def test_no_adrs_yet_message(self, tmp_path: Path) -> None:
        vault = tmp_path / "docs" / "vault"
        _make_brief(vault, datetime.now(UTC))
        ctx, _ = h1.build_context(tmp_path)
        assert "(no ADRs yet)" in ctx


class TestMain:
    def test_main_emits_stdout_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = tmp_path / "docs" / "vault"
        _make_brief(vault, datetime.now(UTC))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        with patch.object(h1.sys, "stdin"):
            h1.sys.stdin.read = lambda: ""  # type: ignore[method-assign]
            rc = h1.main()
        assert rc == 0
        captured = capsys.readouterr()
        assert "VAULT SESSION BRIEF" in captured.out

    def test_main_silent_when_no_vault(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        with patch.object(h1.sys, "stdin"):
            h1.sys.stdin.read = lambda: ""  # type: ignore[method-assign]
            rc = h1.main()
        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out == ""
