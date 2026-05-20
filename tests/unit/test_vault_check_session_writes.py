"""Unit tests for ``cma.vault.check_session_writes`` — Hook 4 (Stop rule)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cma.vault import check_session_writes as h4


@pytest.fixture(autouse=True)
def clear_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CMA_VAULT_BYPASS", raising=False)
    monkeypatch.delenv("CMA_VAULT_STOP_BYPASS", raising=False)


@pytest.fixture(autouse=True)
def silence_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMA_VAULT_HOOK_LOG", str(tmp_path / "telemetry.jsonl"))


def _make_transcript(tmp_path: Path, text: str) -> Path:
    """Write a fake transcript JSONL and return its path."""
    p = tmp_path / "transcript.jsonl"
    lines = []
    for chunk in text.split("\n"):
        lines.append(json.dumps({"role": "assistant", "content": chunk}))
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Keyword scanning
# --------------------------------------------------------------------------- #


class TestScanKeywords:
    def test_finds_single_keyword(self) -> None:
        assert "architecture" in h4.scan_keywords("We need a clean architecture.")

    def test_case_insensitive(self) -> None:
        assert "architecture" in h4.scan_keywords("ARCHITECTURE matters.")

    def test_word_boundaries_for_single_words(self) -> None:
        # "rationally" should NOT match "rationale"
        assert "rationale" not in h4.scan_keywords("This proceeds rationally.")
        assert "rationale" in h4.scan_keywords("State the rationale clearly.")

    def test_phrases_substring_match(self) -> None:
        assert "design decision" in h4.scan_keywords("Made a key design decision today.")

    def test_returns_distinct_set(self) -> None:
        text = "architecture is the architecture of architecture."
        found = h4.scan_keywords(text)
        assert found == {"architecture"}  # distinct, not 3 copies


# --------------------------------------------------------------------------- #
# Transcript reading
# --------------------------------------------------------------------------- #


class TestReadTranscript:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert h4.read_transcript(tmp_path / "missing.jsonl") == ""

    def test_extracts_content(self, tmp_path: Path) -> None:
        p = _make_transcript(tmp_path, "Hello world\nSecond line")
        text = h4.read_transcript(p)
        assert "Hello world" in text
        assert "Second line" in text

    def test_tolerates_malformed_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "transcript.jsonl"
        p.write_text(
            'not json\n{"role":"u","content":"good line"}\nalso bad\n',
            encoding="utf-8",
        )
        text = h4.read_transcript(p)
        assert "good line" in text


# --------------------------------------------------------------------------- #
# Vault-file detection
# --------------------------------------------------------------------------- #


class TestVaultFilesWritten:
    def test_finds_decision_file_mention(self) -> None:
        text = "I just wrote docs/vault/decisions/2026-05-20-new-thing.md"
        found = h4.vault_files_written_in_session(text)
        assert "docs/vault/decisions/2026-05-20-new-thing.md" in found

    def test_finds_learning_file_mention(self) -> None:
        text = "Edit: docs/vault/learnings/some-finding.md"
        found = h4.vault_files_written_in_session(text)
        assert "docs/vault/learnings/some-finding.md" in found

    def test_ignores_anthropic_docs_paths(self) -> None:
        text = "Read docs/vault/knowledge/anthropic-docs/hooks.md"
        # knowledge/ is NOT in the watched patterns (only decisions/ + learnings/)
        found = h4.vault_files_written_in_session(text)
        assert found == []

    def test_empty_when_no_paths(self) -> None:
        assert h4.vault_files_written_in_session("just chatting") == []


# --------------------------------------------------------------------------- #
# evaluate
# --------------------------------------------------------------------------- #


class TestEvaluate:
    def test_no_transcript_path_allows(self) -> None:
        code, block = h4.evaluate({})
        assert code == 0 and block == ""

    def test_single_keyword_allows(self, tmp_path: Path) -> None:
        p = _make_transcript(tmp_path, "We discussed architecture briefly.")
        code, block = h4.evaluate({"transcript_path": str(p)})
        assert code == 0 and block == ""

    def test_two_keywords_no_write_blocks(self, tmp_path: Path) -> None:
        p = _make_transcript(
            tmp_path,
            "Let's revisit the architecture and articulate the rationale.",
        )
        code, block = h4.evaluate({"transcript_path": str(p)})
        assert code == 0  # exit 0 — Stop blocks via JSON, not exit code
        assert block, "expected block JSON"
        data = json.loads(block)
        assert data["decision"] == "block"
        assert "architecture" in data["reason"]
        assert "rationale" in data["reason"]

    def test_two_keywords_with_vault_write_allows(self, tmp_path: Path) -> None:
        p = _make_transcript(
            tmp_path,
            (
                "Discussed the architecture and rationale. "
                "Wrote docs/vault/decisions/2026-05-20-new.md to capture."
            ),
        )
        code, block = h4.evaluate({"transcript_path": str(p)})
        assert code == 0 and block == ""


# --------------------------------------------------------------------------- #
# main (entry point)
# --------------------------------------------------------------------------- #


class TestMain:
    def test_stop_hook_active_skips_immediately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The critical anti-infinite-loop case."""
        input_json = json.dumps({"stop_hook_active": True, "transcript_path": "/tmp/x"})
        with patch.object(h4.sys, "stdin"):
            h4.sys.stdin.read = lambda: input_json  # type: ignore[method-assign]
            rc = h4.main()
        assert rc == 0  # Always allow on retry

    def test_master_bypass_skips(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("CMA_VAULT_BYPASS", "1")
        p = _make_transcript(tmp_path, "architecture + rationale + invariant")
        input_json = json.dumps({"transcript_path": str(p)})
        with patch.object(h4.sys, "stdin"):
            h4.sys.stdin.read = lambda: input_json  # type: ignore[method-assign]
            rc = h4.main()
        assert rc == 0

    def test_stop_specific_bypass_skips(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("CMA_VAULT_STOP_BYPASS", "1")
        p = _make_transcript(tmp_path, "architecture + rationale + invariant")
        input_json = json.dumps({"transcript_path": str(p)})
        with patch.object(h4.sys, "stdin"):
            h4.sys.stdin.read = lambda: input_json  # type: ignore[method-assign]
            rc = h4.main()
        assert rc == 0

    def test_block_prints_json_to_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        p = _make_transcript(
            tmp_path,
            "We discussed the architecture and the trade-off in depth.",
        )
        input_json = json.dumps({"transcript_path": str(p)})
        with patch.object(h4.sys, "stdin"):
            h4.sys.stdin.read = lambda: input_json  # type: ignore[method-assign]
            rc = h4.main()
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["decision"] == "block"
        assert "architecture" in data["reason"]
