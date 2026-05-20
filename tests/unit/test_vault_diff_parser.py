"""Unit tests for ``cma.vault.diff_parser``.

Covers:
- extract_decision_bullets: bullet collection across multiple sections
- parse_changelog_diff: new vs removed vs edit detection (with + without baseline)
- ChangelogDiffResult.is_revert
- DecisionBullet equality + hashing

Git invocation paths (``staged_files``, ``staged_changelog_diff``,
``file_content_at_ref``, ``detect_is_amend``) are tested via mocked
``subprocess.run`` — no real git repo required.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from cma.vault import diff_parser
from cma.vault.diff_parser import (
    DecisionBullet,
    extract_decision_bullets,
    parse_changelog_diff,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

CHANGELOG_WITH_TWO_DECISIONS = """\
# Changelog

## [Unreleased]

### Added
- New feature X.

### Decisions baked in
- **Subprocess per job**: isolation matters more than throughput.
- **Static bearer auth**: rotation invalidates immediately.

### Tests
- 12 new tests.
"""

CHANGELOG_WITH_MULTIPLE_SECTIONS = """\
# Changelog

## [0.5.0]

### Decisions baked in
- **Bridge transport optional**: backwards compat.
- **HTTP must be HTTPS**: defense in depth.

## [0.4.0]

### Decisions baked in
- **OAuth pivot**: Claude Code MCP, not Managed Agents adjunct.

## [0.3.0]

### Decisions baked in
- **Cache for process lifetime**: rotation = restart.
"""


# --------------------------------------------------------------------------- #
# extract_decision_bullets
# --------------------------------------------------------------------------- #


class TestExtractDecisionBullets:
    def test_simple_two_bullets(self) -> None:
        bullets = extract_decision_bullets(CHANGELOG_WITH_TWO_DECISIONS)
        titles = [b.title for b in bullets]
        assert titles == ["Subprocess per job", "Static bearer auth"]

    def test_multiple_sections_aggregated(self) -> None:
        bullets = extract_decision_bullets(CHANGELOG_WITH_MULTIPLE_SECTIONS)
        titles = [b.title for b in bullets]
        assert titles == [
            "Bridge transport optional",
            "HTTP must be HTTPS",
            "OAuth pivot",
            "Cache for process lifetime",
        ]

    def test_empty_input(self) -> None:
        assert extract_decision_bullets("") == []

    def test_no_decisions_section(self) -> None:
        text = "# Changelog\n\n## [Unreleased]\n\n### Added\n- A thing\n"
        assert extract_decision_bullets(text) == []

    def test_decisions_section_ends_at_next_heading(self) -> None:
        text = (
            "### Decisions baked in\n"
            "- **Decision A**: one\n"
            "- **Decision B**: two\n"
            "\n"
            "### Tests\n"
            "- **Not a decision**: should not be picked up\n"
        )
        titles = [b.title for b in extract_decision_bullets(text)]
        assert titles == ["Decision A", "Decision B"]

    def test_bullet_without_bold_title_ignored(self) -> None:
        text = (
            "### Decisions baked in\n"
            "- Just a regular bullet, no bold title\n"
            "- **Real decision**: with bold\n"
        )
        titles = [b.title for b in extract_decision_bullets(text)]
        assert titles == ["Real decision"]


# --------------------------------------------------------------------------- #
# DecisionBullet (dataclass behavior)
# --------------------------------------------------------------------------- #


class TestDecisionBullet:
    def test_equality(self) -> None:
        a = DecisionBullet(title="X", first_line="- **X**: ...")
        b = DecisionBullet(title="X", first_line="- **X**: ...")
        assert a == b

    def test_hashable(self) -> None:
        b = DecisionBullet(title="X", first_line="- **X**: ...")
        assert {b}  # should not raise


# --------------------------------------------------------------------------- #
# parse_changelog_diff — happy paths
# --------------------------------------------------------------------------- #

SAMPLE_DIFF_NEW_BULLET = """\
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 1234567..89abcde 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -10,6 +10,8 @@
 ### Decisions baked in
 - **Existing decision**: was here before.
+- **New decision**: just added.
+- **Another new one**: also added.
"""

SAMPLE_DIFF_REVERT = """\
diff --git a/CHANGELOG.md b/CHANGELOG.md
@@ -10,8 +10,6 @@
 ### Decisions baked in
 - **Kept decision**: still here.
-- **Removed A**: gone.
-- **Removed B**: also gone.
"""

SAMPLE_DIFF_EDIT_ONLY = """\
diff --git a/CHANGELOG.md b/CHANGELOG.md
@@ -10,4 +10,4 @@
 ### Decisions baked in
-- **Existing decision**: original wording.
+- **Existing decision**: revised wording.
"""

SAMPLE_DIFF_TWO_SECTIONS = """\
diff --git a/CHANGELOG.md b/CHANGELOG.md
@@ -5,6 +5,7 @@
 ## [Unreleased]
 ### Decisions baked in
+- **New in unreleased**: bullet.

@@ -20,4 +20,5 @@
 ## [0.5.0]
 ### Decisions baked in
+- **New in past release**: bullet.
"""

SAMPLE_DIFF_NOT_IN_DECISIONS_SECTION = """\
diff --git a/CHANGELOG.md b/CHANGELOG.md
@@ -5,6 +5,7 @@
 ## [Unreleased]
 ### Added
+- **Bold thing in Added, not Decisions**: should NOT count.
"""


class TestParseChangelogDiff:
    def test_new_bullet_detected(self) -> None:
        result = parse_changelog_diff(SAMPLE_DIFF_NEW_BULLET)
        titles = [b.title for b in result.new_bullets]
        assert titles == ["New decision", "Another new one"]
        assert result.removed_bullets == []
        assert not result.is_revert

    def test_revert_detected(self) -> None:
        result = parse_changelog_diff(SAMPLE_DIFF_REVERT)
        assert result.new_bullets == []
        removed_titles = [b.title for b in result.removed_bullets]
        assert removed_titles == ["Removed A", "Removed B"]
        assert result.is_revert

    def test_edit_counts_as_both_without_baseline(self) -> None:
        # Without baseline, an edit looks like remove + add.
        result = parse_changelog_diff(SAMPLE_DIFF_EDIT_ONLY)
        assert [b.title for b in result.new_bullets] == ["Existing decision"]
        assert [b.title for b in result.removed_bullets] == ["Existing decision"]

    def test_edit_not_new_with_baseline(self) -> None:
        # With baseline, the added bullet's title is found in baseline → not new.
        baseline = (
            "### Decisions baked in\n- **Existing decision**: original wording.\n"
        )
        result = parse_changelog_diff(SAMPLE_DIFF_EDIT_ONLY, baseline_text=baseline)
        assert result.new_bullets == []  # not new!
        assert [b.title for b in result.removed_bullets] == ["Existing decision"]

    def test_multi_section_aggregation(self) -> None:
        result = parse_changelog_diff(SAMPLE_DIFF_TWO_SECTIONS)
        titles = [b.title for b in result.new_bullets]
        # Both sections contribute.
        assert "New in unreleased" in titles
        assert "New in past release" in titles
        assert len(titles) == 2

    def test_bullets_outside_decisions_section_ignored(self) -> None:
        result = parse_changelog_diff(SAMPLE_DIFF_NOT_IN_DECISIONS_SECTION)
        assert result.new_bullets == []

    def test_empty_diff(self) -> None:
        result = parse_changelog_diff("")
        assert result.new_bullets == []
        assert result.removed_bullets == []
        assert not result.is_revert


# --------------------------------------------------------------------------- #
# Git-invoking helpers (mocked subprocess)
# --------------------------------------------------------------------------- #


def _mock_run(stdout: bytes = b"", returncode: int = 0):
    """Build a fake CompletedProcess."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=b"")


class TestStagedFiles:
    def test_returns_filenames(self) -> None:
        with patch(
            "cma.vault.diff_parser.subprocess.run",
            return_value=_mock_run(b"a.py\nb.md\n"),
        ):
            assert diff_parser.staged_files() == ["a.py", "b.md"]

    def test_empty_on_failure(self) -> None:
        with patch(
            "cma.vault.diff_parser.subprocess.run",
            return_value=_mock_run(b"", returncode=128),
        ):
            assert diff_parser.staged_files() == []

    def test_empty_on_missing_git(self) -> None:
        with patch(
            "cma.vault.diff_parser.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert diff_parser.staged_files() == []


class TestStagedChangelogDiff:
    def test_returns_decoded(self) -> None:
        with patch(
            "cma.vault.diff_parser.subprocess.run",
            return_value=_mock_run(SAMPLE_DIFF_NEW_BULLET.encode("utf-8")),
        ):
            out = diff_parser.staged_changelog_diff()
            assert "New decision" in out

    def test_empty_on_timeout(self) -> None:
        with patch(
            "cma.vault.diff_parser.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=[], timeout=5),
        ):
            assert diff_parser.staged_changelog_diff() == ""


class TestFileContentAtRef:
    def test_returns_decoded(self) -> None:
        with patch(
            "cma.vault.diff_parser.subprocess.run",
            return_value=_mock_run(b"# Changelog\n"),
        ):
            content = diff_parser.file_content_at_ref("CHANGELOG.md")
            assert content == "# Changelog\n"

    def test_none_on_failure(self) -> None:
        with patch(
            "cma.vault.diff_parser.subprocess.run",
            return_value=_mock_run(returncode=128),
        ):
            assert diff_parser.file_content_at_ref("CHANGELOG.md") is None


class TestDetectIsAmend:
    def test_detects_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_REFLOG_ACTION", "commit (amend): tweaking")
        assert diff_parser.detect_is_amend()

    def test_false_when_env_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GIT_REFLOG_ACTION", raising=False)
        assert not diff_parser.detect_is_amend()

    def test_false_when_env_unrelated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GIT_REFLOG_ACTION", "commit")
        assert not diff_parser.detect_is_amend()
