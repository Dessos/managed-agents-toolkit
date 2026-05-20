"""Unit tests for ``cma.vault.enforce_changelog`` — Hook 2 (anti-decay).

Covers the 8 spec cases from plan v4.1:

1. Positive (block) — new bullet, no ADR staged → exit 2.
2. Negative (allow) — new bullet + ADR staged → exit 0.
3. Edit case (allow) — rewording existing bullet w/o new → exit 0.
4. Multi-bullet (block) — 3 new bullets, 1 ADR → still exit 0 (≥1 ADR is enough).
5. Multi-section (block) — bullets in two sections → both counted.
6. Revert (allow) — removes bullets, adds nothing → exit 0.
7. Amend handling — baseline = HEAD~1 when amend env var set.
8. Bypass — env var skips all checks, returns 0.

All git invocations are mocked via ``cma.vault.diff_parser``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cma.vault import enforce_changelog

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _diff_with_new_bullets(*titles: str) -> str:
    """Build a fake diff that adds the given bullets in a Decisions section."""
    body = ["diff --git a/CHANGELOG.md b/CHANGELOG.md", "@@ -1,1 +1,5 @@", " ### Decisions baked in"]
    for t in titles:
        body.append(f"+- **{t}**: some prose.")
    return "\n".join(body) + "\n"


def _diff_removing_bullets(*titles: str) -> str:
    body = ["diff --git a/CHANGELOG.md b/CHANGELOG.md", "@@ -1,5 +1,1 @@", " ### Decisions baked in"]
    for t in titles:
        body.append(f"-- **{t}**: some prose.")
    return "\n".join(body) + "\n"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def clear_bypass_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure bypass env vars never leak between tests."""
    monkeypatch.delenv("CMA_VAULT_BYPASS", raising=False)
    monkeypatch.delenv("CMA_VAULT_COMMIT_BYPASS", raising=False)


@pytest.fixture(autouse=True)
def silence_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect telemetry log to tmp_path so we don't pollute O:/Temp."""
    monkeypatch.setenv("CMA_VAULT_HOOK_LOG", str(tmp_path / "telemetry.jsonl"))


@pytest.fixture
def mock_diff_module(tmp_path: Path):
    """Patch the diff_parser module's git-invoking helpers wholesale."""
    state: dict[str, Any] = {
        "diff": "",
        "baseline": "",
        "staged": [],
        "is_amend": False,
    }

    with (
        patch(
            "cma.vault.enforce_changelog.diff_parser.detect_is_amend",
            side_effect=lambda **_: state["is_amend"],
        ),
        patch(
            "cma.vault.enforce_changelog.diff_parser.staged_changelog_diff",
            side_effect=lambda **_: state["diff"],
        ),
        patch(
            "cma.vault.enforce_changelog.diff_parser.file_content_at_ref",
            side_effect=lambda *_, **__: state["baseline"],
        ),
        patch(
            "cma.vault.enforce_changelog.diff_parser.staged_files",
            side_effect=lambda **_: state["staged"],
        ),
    ):
        yield state


# --------------------------------------------------------------------------- #
# 8 spec cases
# --------------------------------------------------------------------------- #


class TestEnforceChangelog:
    def test_1_positive_block(
        self, mock_diff_module: dict, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """New bullet, no ADR → exit 2 with stderr."""
        mock_diff_module["diff"] = _diff_with_new_bullets("Subprocess per job")
        mock_diff_module["baseline"] = ""  # empty baseline → bullet is new
        mock_diff_module["staged"] = ["CHANGELOG.md"]

        code, msg = enforce_changelog.evaluate(tmp_path)
        assert code == 2
        assert "Subprocess per job" in msg
        assert "cma vault new-decision" in msg
        assert "CMA_VAULT_COMMIT_BYPASS" in msg

    def test_2_negative_allow_with_adr(
        self, mock_diff_module: dict, tmp_path: Path
    ) -> None:
        """New bullet + staged ADR → exit 0."""
        mock_diff_module["diff"] = _diff_with_new_bullets("Subprocess per job")
        mock_diff_module["baseline"] = ""
        mock_diff_module["staged"] = [
            "CHANGELOG.md",
            "docs/vault/decisions/2026-05-19-subprocess.md",
        ]

        code, msg = enforce_changelog.evaluate(tmp_path)
        assert code == 0
        assert msg == ""

    def test_3_edit_only_allow(self, mock_diff_module: dict, tmp_path: Path) -> None:
        """Reword existing bullet without adding new → exit 0."""
        diff = (
            "diff --git a/CHANGELOG.md b/CHANGELOG.md\n"
            "@@ -1,3 +1,3 @@\n"
            " ### Decisions baked in\n"
            "-- **Existing**: original.\n"
            "+- **Existing**: revised.\n"
        )
        mock_diff_module["diff"] = diff
        mock_diff_module["baseline"] = "### Decisions baked in\n- **Existing**: original.\n"
        mock_diff_module["staged"] = ["CHANGELOG.md"]

        code, msg = enforce_changelog.evaluate(tmp_path)
        assert code == 0
        assert msg == ""

    def test_4_multi_bullet_one_adr_still_allows(
        self, mock_diff_module: dict, tmp_path: Path
    ) -> None:
        """Spec: ≥1 ADR file is enough (one ADR can cover multiple bullets)."""
        mock_diff_module["diff"] = _diff_with_new_bullets("A", "B", "C")
        mock_diff_module["baseline"] = ""
        mock_diff_module["staged"] = [
            "CHANGELOG.md",
            "docs/vault/decisions/2026-05-19-combined.md",
        ]

        code, _ = enforce_changelog.evaluate(tmp_path)
        assert code == 0

    def test_5_multi_section_aggregation(
        self, mock_diff_module: dict, tmp_path: Path
    ) -> None:
        """Bullets in two ``### Decisions baked in`` sections → both counted."""
        diff = (
            "diff --git a/CHANGELOG.md b/CHANGELOG.md\n"
            "@@ -5,1 +5,2 @@\n"
            " ### Decisions baked in\n"
            "+- **Bullet from section 1**: yes.\n"
            "@@ -15,1 +15,2 @@\n"
            " ### Decisions baked in\n"
            "+- **Bullet from section 2**: also yes.\n"
        )
        mock_diff_module["diff"] = diff
        mock_diff_module["baseline"] = ""
        mock_diff_module["staged"] = ["CHANGELOG.md"]  # no ADR

        code, msg = enforce_changelog.evaluate(tmp_path)
        assert code == 2
        assert "Bullet from section 1" in msg
        assert "Bullet from section 2" in msg

    def test_6_revert_allowed(self, mock_diff_module: dict, tmp_path: Path) -> None:
        """Removing bullets without adding any → exit 0."""
        mock_diff_module["diff"] = _diff_removing_bullets("Going away A", "Going away B")
        mock_diff_module["baseline"] = ""
        mock_diff_module["staged"] = ["CHANGELOG.md"]

        code, _ = enforce_changelog.evaluate(tmp_path)
        assert code == 0

    def test_7_amend_uses_head_minus_one(
        self, mock_diff_module: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When --amend is detected, the baseline is HEAD~1."""
        monkeypatch.setenv("GIT_REFLOG_ACTION", "commit (amend): tweaking")
        mock_diff_module["is_amend"] = True
        mock_diff_module["diff"] = _diff_with_new_bullets("Amended new bullet")
        mock_diff_module["baseline"] = ""  # HEAD~1 has no Decisions
        mock_diff_module["staged"] = ["CHANGELOG.md"]  # no ADR

        code, msg = enforce_changelog.evaluate(tmp_path)
        assert code == 2
        assert "Amended new bullet" in msg

    def test_8a_master_bypass(
        self, mock_diff_module: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Master CMA_VAULT_BYPASS=1 → exit 0 regardless."""
        monkeypatch.setenv("CMA_VAULT_BYPASS", "1")
        mock_diff_module["diff"] = _diff_with_new_bullets("Would-be blocker")
        mock_diff_module["staged"] = []  # no ADR

        with patch.object(enforce_changelog.sys, "stdin"):
            enforce_changelog.sys.stdin.read = lambda: ""  # type: ignore[method-assign]
            rc = enforce_changelog.main()
        assert rc == 0

    def test_8b_commit_specific_bypass(
        self, mock_diff_module: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CMA_VAULT_COMMIT_BYPASS=1 → exit 0 (this hook only)."""
        monkeypatch.setenv("CMA_VAULT_COMMIT_BYPASS", "1")
        mock_diff_module["diff"] = _diff_with_new_bullets("Would-be blocker")
        mock_diff_module["staged"] = []

        with patch.object(enforce_changelog.sys, "stdin"):
            enforce_changelog.sys.stdin.read = lambda: ""  # type: ignore[method-assign]
            rc = enforce_changelog.main()
        assert rc == 0


# --------------------------------------------------------------------------- #
# Edge cases beyond the 8
# --------------------------------------------------------------------------- #


class TestEnforceChangelogEdges:
    def test_no_changelog_change_allows(
        self, mock_diff_module: dict, tmp_path: Path
    ) -> None:
        """Empty diff → exit 0 (nothing to enforce)."""
        mock_diff_module["diff"] = ""
        code, _ = enforce_changelog.evaluate(tmp_path)
        assert code == 0

    def test_underscore_files_dont_count_as_adrs(
        self, mock_diff_module: dict, tmp_path: Path
    ) -> None:
        """_README.md and _INDEX.md in decisions/ don't satisfy the ADR requirement."""
        mock_diff_module["diff"] = _diff_with_new_bullets("Needs real ADR")
        mock_diff_module["baseline"] = ""
        mock_diff_module["staged"] = [
            "CHANGELOG.md",
            "docs/vault/decisions/_INDEX.md",
            "docs/vault/decisions/_README.md",
        ]

        code, msg = enforce_changelog.evaluate(tmp_path)
        assert code == 2
        assert "Needs real ADR" in msg

    def test_main_returns_zero_on_allow_path(
        self, mock_diff_module: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end main() on the allow path."""
        mock_diff_module["diff"] = ""
        with patch.object(enforce_changelog.sys, "stdin"):
            enforce_changelog.sys.stdin.read = lambda: ""  # type: ignore[method-assign]
            rc = enforce_changelog.main()
        assert rc == 0

    def test_main_returns_two_on_block_path(
        self,
        mock_diff_module: dict,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end main() on the block path: exit 2 + stderr message."""
        mock_diff_module["diff"] = _diff_with_new_bullets("Forced block")
        mock_diff_module["baseline"] = ""
        mock_diff_module["staged"] = ["CHANGELOG.md"]

        with patch.object(enforce_changelog.sys, "stdin"):
            enforce_changelog.sys.stdin.read = lambda: ""  # type: ignore[method-assign]
            rc = enforce_changelog.main()
        assert rc == 2
        err = capsys.readouterr().err
        assert "Forced block" in err
