"""Unit tests for ``cma.vault.new_note``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cma.vault import new_note


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Build a minimal vault with all 4 templates."""
    v = tmp_path / "vault"
    (v / "_templates").mkdir(parents=True)
    for kind in ("decision", "learning", "evaluation", "incident"):
        (v / "_templates" / f"{kind}.md").write_text(
            f"---\ntype: {kind}\ncreated: {{{{CREATED}}}}\n---\n# {{{{TITLE}}}}\n\n{{{{SOURCE}}}}\n",
            encoding="utf-8",
        )
    return v


# --------------------------------------------------------------------------- #
# slugify
# --------------------------------------------------------------------------- #


class TestSlugify:
    def test_lowercases(self) -> None:
        assert new_note._slugify("HELLO World") == "hello-world"

    def test_collapses_spaces(self) -> None:
        assert new_note._slugify("a   b   c") == "a-b-c"

    def test_dots_become_dashes(self) -> None:
        assert new_note._slugify("v1.2.3") == "v1-2-3"

    def test_strips_dashes(self) -> None:
        assert new_note._slugify("---foo---") == "foo"

    def test_empty_after_normalization(self) -> None:
        assert new_note._slugify("!@#$%") == ""


# --------------------------------------------------------------------------- #
# make_note
# --------------------------------------------------------------------------- #


class TestMakeNote:
    def test_creates_file_with_substitutions(self, vault: Path) -> None:
        now = datetime(2026, 5, 20, tzinfo=UTC)
        result = new_note.make_note(
            kind="decision",
            slug="my-thing",
            vault_root=vault,
            open_editor=False,
            now=now,
        )
        assert result.created
        assert result.path == vault / "decisions" / "2026-05-20-my-thing.md"
        content = result.path.read_text(encoding="utf-8")
        assert "type: decision" in content
        assert "created: 2026-05-20T00:00:00Z" in content
        assert "{{CREATED}}" not in content  # all substituted
        assert "{{TITLE}}" not in content

    def test_title_override(self, vault: Path) -> None:
        result = new_note.make_note(
            kind="decision",
            slug="x",
            vault_root=vault,
            title="Custom Title Here",
            open_editor=False,
        )
        content = result.path.read_text(encoding="utf-8")
        assert "# Custom Title Here" in content

    def test_source_override(self, vault: Path) -> None:
        result = new_note.make_note(
            kind="learning",
            slug="x",
            vault_root=vault,
            source="commit abc1234",
            open_editor=False,
        )
        content = result.path.read_text(encoding="utf-8")
        assert "commit abc1234" in content

    def test_refuses_existing_without_force(self, vault: Path) -> None:
        new_note.make_note(kind="decision", slug="dup", vault_root=vault, open_editor=False)
        with pytest.raises(FileExistsError, match="already exists"):
            new_note.make_note(
                kind="decision", slug="dup", vault_root=vault, open_editor=False
            )

    def test_force_overwrites(self, vault: Path) -> None:
        new_note.make_note(kind="decision", slug="x", vault_root=vault, open_editor=False)
        # Should not raise.
        new_note.make_note(
            kind="decision", slug="x", vault_root=vault, force=True, open_editor=False
        )

    def test_unknown_kind_raises(self, vault: Path) -> None:
        with pytest.raises(ValueError, match="unknown note kind"):
            new_note.make_note(kind="bogus", slug="x", vault_root=vault, open_editor=False)

    def test_empty_slug_raises(self, vault: Path) -> None:
        with pytest.raises(ValueError, match="reduced to empty"):
            new_note.make_note(
                kind="decision", slug="!@#", vault_root=vault, open_editor=False
            )

    def test_missing_template_raises(self, tmp_path: Path) -> None:
        # vault without _templates
        v = tmp_path / "vault"
        v.mkdir()
        with pytest.raises(FileNotFoundError, match="template not found"):
            new_note.make_note(
                kind="decision", slug="x", vault_root=v, open_editor=False
            )

    def test_subfolder_created_if_missing(self, vault: Path) -> None:
        # decisions/ doesn't pre-exist in the fixture.
        result = new_note.make_note(
            kind="decision", slug="x", vault_root=vault, open_editor=False
        )
        assert result.path.parent.is_dir()
