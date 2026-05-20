"""Unit tests for ``cma.vault.index_gen``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cma.vault import index_gen


def _adr(target: Path, name: str, title: str, status: str = "active", created: str = "2026-05-20T00:00:00Z") -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_text(
        f"---\ntype: decision\nstatus: {status}\ncreated: {created}\n"
        f"updated: 2026-05-20T00:00:00Z\n"
        f"tags: [adr, sample]\nconfidence: high\nsource: \"test\"\n---\n\n# {title}\n",
        encoding="utf-8",
    )


class TestExtract:
    def test_pulls_title_and_status(self, tmp_path: Path) -> None:
        _adr(tmp_path, "a.md", "First ADR", status="active")
        info = index_gen._extract(tmp_path / "a.md")
        assert info["title"] == "First ADR"
        assert info["status"] == "active"

    def test_empty_on_no_frontmatter(self, tmp_path: Path) -> None:
        (tmp_path / "x.md").write_text("# Just a heading\n", encoding="utf-8")
        assert index_gen._extract(tmp_path / "x.md") == {}


class TestRenderIndex:
    def test_basic_rendering(self, tmp_path: Path) -> None:
        _adr(tmp_path, "a.md", "First")
        _adr(tmp_path, "b.md", "Second")
        out = index_gen.render_index(tmp_path)
        assert "First" in out
        assert "Second" in out
        assert "**Total**: 2 entries" in out
        # The actual file should be written.
        assert (tmp_path / "_INDEX.md").is_file()

    def test_skips_underscore_files(self, tmp_path: Path) -> None:
        _adr(tmp_path, "real.md", "Real ADR")
        (tmp_path / "_README.md").write_text(
            "---\ntype: meta\nstatus: active\ncreated: 2026-05-20T00:00:00Z\n"
            "updated: 2026-05-20T00:00:00Z\ntags: [readme]\nconfidence: high\n"
            "source: \"test\"\n---\n\n# Readme\n",
            encoding="utf-8",
        )
        out = index_gen.render_index(tmp_path)
        # _README appears in frontmatter `related: [[_README]]` by design;
        # what we test is that no TABLE ROW links to _README.md.
        assert "Real ADR" in out
        assert "[_README.md]" not in out  # no link in the table
        assert "real.md" in out  # the real file IS linked

    def test_idempotent(self, tmp_path: Path) -> None:
        _adr(tmp_path, "a.md", "First")
        now = datetime(2026, 5, 20, tzinfo=UTC)
        first = index_gen.render_index(tmp_path, now=now)
        second = index_gen.render_index(tmp_path, now=now)
        assert first == second

    def test_empty_folder_shows_placeholder(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        out = index_gen.render_index(tmp_path)
        assert "(no entries yet)" in out
        assert "**Total**: 0 entries" in out

    def test_missing_folder_returns_empty(self, tmp_path: Path) -> None:
        out = index_gen.render_index(tmp_path / "missing")
        assert out == ""


class TestRegenerateAll:
    def test_processes_known_folders(self, tmp_path: Path) -> None:
        _adr(tmp_path / "decisions", "a.md", "Dec")
        _adr(tmp_path / "learnings", "b.md", "Learn")
        counts = index_gen.regenerate_all(tmp_path)
        assert counts.get("decisions") == 1
        assert counts.get("learnings") == 1
        assert (tmp_path / "decisions" / "_INDEX.md").is_file()
        assert (tmp_path / "learnings" / "_INDEX.md").is_file()

    def test_skips_missing_folders(self, tmp_path: Path) -> None:
        counts = index_gen.regenerate_all(tmp_path)
        assert counts == {}
