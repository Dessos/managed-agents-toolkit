"""Unit tests for ``cma.vault.check_adr_section_drift`` — Hook 3 (nudge)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from cma.vault import check_adr_section_drift as h3


@pytest.fixture(autouse=True)
def silence_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMA_VAULT_HOOK_LOG", str(tmp_path / "telemetry.jsonl"))


def _mock_git_diff(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=0, stdout=stdout.encode("utf-8"), stderr=b""
    )


class TestCommittedAdrs:
    def test_filters_decisions_dir(self) -> None:
        out = "src/cma/x.py\ndocs/vault/decisions/2026-05-20-a.md\nREADME.md\n"
        with patch("cma.vault.check_adr_section_drift.subprocess.run", return_value=_mock_git_diff(out)):
            adrs = h3.committed_adrs(Path("/x"))
        assert adrs == ["docs/vault/decisions/2026-05-20-a.md"]

    def test_skips_underscore_files(self) -> None:
        out = "docs/vault/decisions/_INDEX.md\ndocs/vault/decisions/_README.md\n"
        with patch("cma.vault.check_adr_section_drift.subprocess.run", return_value=_mock_git_diff(out)):
            adrs = h3.committed_adrs(Path("/x"))
        assert adrs == []

    def test_empty_on_git_failure(self) -> None:
        with patch(
            "cma.vault.check_adr_section_drift.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert h3.committed_adrs(Path("/x")) == []


class TestAdrSectionsTouched:
    def test_extracts_architecture_tag(self, tmp_path: Path) -> None:
        adr = tmp_path / "adr.md"
        adr.write_text(
            "---\ntype: decision\ntags: [adr, architecture, ip-boundary]\n---\n# X\n",
            encoding="utf-8",
        )
        assert h3.adr_sections_touched(adr) == {"ARCHITECTURE"}

    def test_extracts_multiple_sections(self, tmp_path: Path) -> None:
        adr = tmp_path / "adr.md"
        adr.write_text(
            "---\ntype: decision\ntags: [adr, patterns, philosophy, stack]\n---\n",
            encoding="utf-8",
        )
        assert h3.adr_sections_touched(adr) == {"PATTERNS", "PHILOSOPHY", "STACK"}

    def test_no_relevant_tags_returns_empty(self, tmp_path: Path) -> None:
        adr = tmp_path / "adr.md"
        adr.write_text(
            "---\ntype: decision\ntags: [adr, security]\n---\n",
            encoding="utf-8",
        )
        assert h3.adr_sections_touched(adr) == set()

    def test_handles_dashed_tags(self, tmp_path: Path) -> None:
        # 'trade-off' / 'trade-offs' both map to TRADEOFFS
        adr = tmp_path / "adr.md"
        adr.write_text(
            "---\ntype: decision\ntags: [adr, trade-off]\n---\n",
            encoding="utf-8",
        )
        assert h3.adr_sections_touched(adr) == {"TRADEOFFS"}

    def test_no_frontmatter(self, tmp_path: Path) -> None:
        adr = tmp_path / "adr.md"
        adr.write_text("# Just a heading\n", encoding="utf-8")
        assert h3.adr_sections_touched(adr) == set()


class TestMain:
    def test_no_adrs_silent(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch(
                "cma.vault.check_adr_section_drift.subprocess.run",
                return_value=_mock_git_diff(""),
            ),
            patch.object(h3.sys, "stdin"),
        ):
            h3.sys.stdin.read = lambda: ""  # type: ignore[method-assign]
            rc = h3.main()
        assert rc == 0
        assert capsys.readouterr().err == ""

    def test_adr_with_section_tag_emits_nudge(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        adr_dir = tmp_path / "docs" / "vault" / "decisions"
        adr_dir.mkdir(parents=True)
        adr = adr_dir / "2026-05-20-test.md"
        adr.write_text(
            "---\ntype: decision\ntags: [adr, patterns]\n---\n# Test\n",
            encoding="utf-8",
        )

        diff_out = "docs/vault/decisions/2026-05-20-test.md\n"
        with (
            patch(
                "cma.vault.check_adr_section_drift.subprocess.run",
                return_value=_mock_git_diff(diff_out),
            ),
            patch.object(h3.sys, "stdin"),
        ):
            h3.sys.stdin.read = lambda: ""  # type: ignore[method-assign]
            rc = h3.main()
        assert rc == 0
        err = capsys.readouterr().err
        assert "may shift manage_adr" in err
        assert "PATTERNS" in err
        assert "2026-05-20-test.md" in err
