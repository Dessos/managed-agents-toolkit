"""Unit tests for ``cma.vault.scrape_anthropic_docs``.

Covers:
- llms.txt parsing (well-formed + edge cases + nested paths)
- DocEntry.slug derivation
- Duration parsing
- Frontmatter helpers (parse + build + strip + preserve created date)
- Freshness gate (is_fresh)
- Select entries (curated / all / single)
- Scrape end-to-end (with urllib mocked)
- Index regeneration (idempotency + stale markers)

Network is fully mocked — no test reaches the internet.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from cma.vault import scrape_anthropic_docs as scraper

# --------------------------------------------------------------------------- #
# Test fixtures
# --------------------------------------------------------------------------- #

SAMPLE_LLMS_TXT = """\
# Claude Code Docs

> The complete docs index.

## Docs

- [Hooks](https://code.claude.com/docs/en/hooks.md): full schemas
- [Hooks guide](https://code.claude.com/docs/en/hooks-guide.md): worked examples
- [Agent SDK Hooks](https://code.claude.com/docs/en/agent-sdk/hooks.md): SDK variant
- [Weekly notes](https://code.claude.com/docs/en/whats-new/2026-w20.md)
- Not a list item — should be ignored
- [Bad URL](https://example.com/not-claude) — wrong host, must miss
"""

SAMPLE_FRONTMATTER = """\
---
type: knowledge
status: active
created: 2026-05-19
updated: 2026-05-20T04:39:33Z
tags: [anthropic, claude-code, doc-mirror, hooks]
related: []
confidence: high
source: "https://code.claude.com/docs/en/hooks.md"
fetched_at: 2026-05-20T04:39:33Z
---

# Hooks reference

Body content here.
"""


# --------------------------------------------------------------------------- #
# parse_llms_txt
# --------------------------------------------------------------------------- #


class TestParseLlmsTxt:
    def test_parses_well_formed_entries(self) -> None:
        entries = scraper.parse_llms_txt(SAMPLE_LLMS_TXT)
        # The "Bad URL" line still matches the regex (URL pattern), so we get
        # entries for everything that looks like a doc URL ending in .md.
        # The bad-URL line doesn't end in .md, so it's filtered out.
        slugs = [e.slug for e in entries]
        assert "hooks" in slugs
        assert "hooks-guide" in slugs
        assert "agent-sdk-hooks" in slugs
        assert "whats-new-2026-w20" in slugs

    def test_skips_lines_not_matching_pattern(self) -> None:
        entries = scraper.parse_llms_txt(SAMPLE_LLMS_TXT)
        # The "Not a list item" line must not produce an entry.
        for e in entries:
            assert "not-claude" not in e.url
            assert "Not a list item" not in e.title

    def test_empty_input(self) -> None:
        assert scraper.parse_llms_txt("") == []

    def test_preserves_titles_and_descriptions(self) -> None:
        entries = scraper.parse_llms_txt(SAMPLE_LLMS_TXT)
        by_slug = {e.slug: e for e in entries}
        assert by_slug["hooks"].title == "Hooks"
        assert by_slug["hooks"].description == "full schemas"
        # Weekly notes has no description after colon — should be empty string.
        assert by_slug["whats-new-2026-w20"].description == ""


# --------------------------------------------------------------------------- #
# DocEntry.slug
# --------------------------------------------------------------------------- #


class TestDocEntrySlug:
    def test_flat_slug(self) -> None:
        e = scraper.DocEntry(title="t", url="https://code.claude.com/docs/en/hooks.md")
        assert e.slug == "hooks"

    def test_hyphenated_slug_preserved(self) -> None:
        e = scraper.DocEntry(title="t", url="https://code.claude.com/docs/en/hooks-guide.md")
        assert e.slug == "hooks-guide"

    def test_nested_path_flattened_to_dashes(self) -> None:
        e = scraper.DocEntry(title="t", url="https://code.claude.com/docs/en/agent-sdk/hooks.md")
        assert e.slug == "agent-sdk-hooks"

    def test_deeply_nested(self) -> None:
        e = scraper.DocEntry(
            title="t", url="https://code.claude.com/docs/en/whats-new/2026-w20.md"
        )
        assert e.slug == "whats-new-2026-w20"


# --------------------------------------------------------------------------- #
# parse_duration
# --------------------------------------------------------------------------- #


class TestParseDuration:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("30d", timedelta(days=30)),
            ("7d", timedelta(days=7)),
            ("24h", timedelta(hours=24)),
            ("60m", timedelta(minutes=60)),
            ("120s", timedelta(seconds=120)),
        ],
    )
    def test_valid_durations(self, text: str, expected: timedelta) -> None:
        assert scraper.parse_duration(text) == expected

    def test_strips_whitespace(self) -> None:
        assert scraper.parse_duration("  30d  ") == timedelta(days=30)

    @pytest.mark.parametrize("bad", ["30", "30days", "abc", "", "1y", "1.5d"])
    def test_invalid_raises(self, bad: str) -> None:
        with pytest.raises(ValueError, match="unrecognized duration"):
            scraper.parse_duration(bad)


# --------------------------------------------------------------------------- #
# Frontmatter helpers
# --------------------------------------------------------------------------- #


class TestFrontmatterHelpers:
    def test_parse_fetched_at_well_formed(self) -> None:
        dt = scraper.parse_fetched_at(SAMPLE_FRONTMATTER)
        assert dt is not None
        assert dt.year == 2026 and dt.month == 5 and dt.day == 20
        assert dt.tzinfo == UTC

    def test_parse_fetched_at_no_frontmatter(self) -> None:
        assert scraper.parse_fetched_at("no frontmatter here") is None

    def test_parse_fetched_at_no_field(self) -> None:
        content = "---\ntype: knowledge\n---\nbody\n"
        assert scraper.parse_fetched_at(content) is None

    def test_parse_fetched_at_malformed_date(self) -> None:
        content = "---\nfetched_at: not-a-date\n---\nbody\n"
        assert scraper.parse_fetched_at(content) is None

    def test_build_frontmatter_shape(self) -> None:
        fm = scraper.build_frontmatter(
            slug="hooks",
            url="https://code.claude.com/docs/en/hooks.md",
            created="2026-05-19",
            updated="2026-05-20T00:00:00Z",
            fetched_at="2026-05-20T00:00:00Z",
        )
        assert fm.startswith("---\n")
        assert fm.endswith("---\n\n")
        assert "type: knowledge" in fm
        assert "source: \"https://code.claude.com/docs/en/hooks.md\"" in fm
        assert "fetched_at: 2026-05-20T00:00:00Z" in fm
        assert "doc-mirror, hooks" in fm  # slug ends up in tags

    def test_strip_existing_frontmatter(self) -> None:
        stripped = scraper.strip_existing_frontmatter(SAMPLE_FRONTMATTER)
        assert not stripped.startswith("---")
        assert stripped.startswith("\n# Hooks reference") or stripped.startswith(
            "# Hooks reference"
        )

    def test_strip_idempotent_on_no_frontmatter(self) -> None:
        content = "# No frontmatter\n\nbody"
        assert scraper.strip_existing_frontmatter(content) == content


# --------------------------------------------------------------------------- #
# is_fresh
# --------------------------------------------------------------------------- #


class TestIsFresh:
    def test_missing_file_not_fresh(self, tmp_path: Path) -> None:
        p = tmp_path / "missing.md"
        assert not scraper.is_fresh(p, timedelta(days=30))

    def test_no_frontmatter_not_fresh(self, tmp_path: Path) -> None:
        p = tmp_path / "x.md"
        p.write_text("no frontmatter", encoding="utf-8")
        assert not scraper.is_fresh(p, timedelta(days=30))

    def test_recent_is_fresh(self, tmp_path: Path) -> None:
        p = tmp_path / "x.md"
        p.write_text(SAMPLE_FRONTMATTER, encoding="utf-8")
        # SAMPLE_FRONTMATTER is dated 2026-05-20; pin "now" to a day later
        now = datetime(2026, 5, 21, tzinfo=UTC)
        assert scraper.is_fresh(p, timedelta(days=30), now=now)

    def test_old_is_stale(self, tmp_path: Path) -> None:
        p = tmp_path / "x.md"
        p.write_text(SAMPLE_FRONTMATTER, encoding="utf-8")
        now = datetime(2027, 1, 1, tzinfo=UTC)  # 7 months later
        assert not scraper.is_fresh(p, timedelta(days=30), now=now)


# --------------------------------------------------------------------------- #
# select_entries
# --------------------------------------------------------------------------- #


class TestSelectEntries:
    def _entries(self) -> list[scraper.DocEntry]:
        return [
            scraper.DocEntry("Overview", "https://code.claude.com/docs/en/overview.md"),
            scraper.DocEntry("Hooks", "https://code.claude.com/docs/en/hooks.md"),
            scraper.DocEntry("Other", "https://code.claude.com/docs/en/random-page.md"),
        ]

    def test_curated_filters_to_known(self) -> None:
        result = scraper.select_entries(self._entries(), curated=True, only_page=None)
        slugs = {e.slug for e in result}
        assert "overview" in slugs and "hooks" in slugs
        assert "random-page" not in slugs

    def test_all_keeps_everything(self) -> None:
        result = scraper.select_entries(self._entries(), curated=False, only_page=None)
        assert len(result) == 3

    def test_only_page_picks_one(self) -> None:
        result = scraper.select_entries(self._entries(), curated=True, only_page="hooks")
        assert len(result) == 1 and result[0].slug == "hooks"

    def test_only_page_with_unknown_slug_returns_empty(self) -> None:
        result = scraper.select_entries(self._entries(), curated=True, only_page="bogus")
        assert result == []


# --------------------------------------------------------------------------- #
# scrape (integration with urllib mocked)
# --------------------------------------------------------------------------- #


@pytest.fixture
def fake_urlopen():
    """Patch ``urllib.request.urlopen`` to return canned responses by URL."""
    responses: dict[str, bytes] = {
        scraper.LLMS_TXT_URL: SAMPLE_LLMS_TXT.encode("utf-8"),
        "https://code.claude.com/docs/en/hooks.md": b"# Hooks reference\n\nBody content.\n",
        "https://code.claude.com/docs/en/hooks-guide.md": b"# Hooks guide\n\nMore body.\n",
        "https://code.claude.com/docs/en/agent-sdk/hooks.md": b"# SDK hooks\n",
        "https://code.claude.com/docs/en/whats-new/2026-w20.md": b"# Week 20\n",
    }

    def _fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url not in responses:
            from urllib.error import HTTPError

            raise HTTPError(url, 404, "Not Found", {}, None)

        class FakeResponse:
            def __init__(self, data: bytes) -> None:
                self._buf = io.BytesIO(data)

            def read(self) -> bytes:
                return self._buf.getvalue()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return FakeResponse(responses[url])

    with patch("cma.vault.scrape_anthropic_docs.urllib.request.urlopen", side_effect=_fake_urlopen):
        yield responses


class TestScrapeIntegration:
    def test_dry_run_writes_nothing(self, fake_urlopen, tmp_path: Path) -> None:
        target = tmp_path / "out"
        report = scraper.scrape(target_dir=target, curated=False, dry_run=True)
        assert report.dry_run is True
        assert not target.exists() or not any(target.iterdir())
        # All 4 valid pages would have been fetched.
        assert set(report.fetched) >= {"hooks", "hooks-guide"}

    def test_live_scrape_writes_files_with_frontmatter(
        self, fake_urlopen, tmp_path: Path
    ) -> None:
        target = tmp_path / "anthropic-docs"
        report = scraper.scrape(target_dir=target, curated=False)
        assert "hooks" in report.fetched
        hooks = (target / "hooks.md").read_text(encoding="utf-8")
        assert hooks.startswith("---\n")
        assert "type: knowledge" in hooks
        assert 'source: "https://code.claude.com/docs/en/hooks.md"' in hooks
        assert "# Hooks reference" in hooks

    def test_freshness_skips_on_second_run(self, fake_urlopen, tmp_path: Path) -> None:
        target = tmp_path / "anthropic-docs"
        scraper.scrape(target_dir=target, curated=False)
        report = scraper.scrape(target_dir=target, curated=False)
        # Second run should skip every page as fresh.
        assert report.fetched == []
        assert len(report.skipped_fresh) >= 4

    def test_force_overrides_freshness(self, fake_urlopen, tmp_path: Path) -> None:
        target = tmp_path / "anthropic-docs"
        scraper.scrape(target_dir=target, curated=False)
        report = scraper.scrape(target_dir=target, curated=False, force=True)
        assert "hooks" in report.fetched
        assert report.skipped_fresh == []

    def test_failed_page_does_not_abort(self, fake_urlopen, tmp_path: Path) -> None:
        # Add a slug to llms.txt that we don't have a canned response for —
        # except llms parsing pulls only what's in SAMPLE_LLMS_TXT, and all of
        # those are in the canned set already. Inject a failure by deleting
        # one response.
        del fake_urlopen["https://code.claude.com/docs/en/hooks-guide.md"]
        target = tmp_path / "anthropic-docs"
        report = scraper.scrape(target_dir=target, curated=False)
        assert ("hooks-guide", report.failed[0][1]) in [(s, e) for s, e in report.failed]
        # Other pages still succeed.
        assert "hooks" in report.fetched

    def test_caches_llms_txt(self, fake_urlopen, tmp_path: Path) -> None:
        target = tmp_path / "knowledge" / "anthropic-docs"
        scraper.scrape(target_dir=target, curated=False)
        cached = target.parent / "_llms.txt"
        assert cached.is_file()
        assert "Hooks reference" not in cached.read_text(encoding="utf-8")  # this is the index
        assert "Hooks](https://code.claude.com" in cached.read_text(encoding="utf-8")

    def test_preserves_created_on_refresh(self, fake_urlopen, tmp_path: Path) -> None:
        target = tmp_path / "anthropic-docs"
        scraper.scrape(target_dir=target, curated=False)
        # Manually edit `created` to an old date, then force-refresh.
        hooks_path = target / "hooks.md"
        content = hooks_path.read_text(encoding="utf-8")
        modified = content.replace("created: 2026", "created: 2024", 1)
        hooks_path.write_text(modified, encoding="utf-8")
        scraper.scrape(target_dir=target, curated=False, force=True, only_page="hooks")
        refreshed = hooks_path.read_text(encoding="utf-8")
        # `created` should still be 2024 (we preserve), `updated` should be current.
        assert "created: 2024" in refreshed


# --------------------------------------------------------------------------- #
# regenerate_index
# --------------------------------------------------------------------------- #


class TestRegenerateIndex:
    def _make_page(
        self, target: Path, slug: str, fetched: str, source: str = "https://example/x.md"
    ) -> None:
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{slug}.md").write_text(
            f"---\ntype: knowledge\nfetched_at: {fetched}\nsource: \"{source}\"\n---\nbody\n",
            encoding="utf-8",
        )

    def test_idempotent(self, tmp_path: Path) -> None:
        self._make_page(tmp_path, "a", "2026-05-20T00:00:00Z")
        self._make_page(tmp_path, "b", "2026-05-20T00:00:00Z")
        fixed_now = datetime(2026, 5, 20, tzinfo=UTC)
        first = scraper.regenerate_index(tmp_path, now=fixed_now)
        second = scraper.regenerate_index(tmp_path, now=fixed_now)
        assert first == second

    def test_skips_underscore_files(self, tmp_path: Path) -> None:
        self._make_page(tmp_path, "real-page", "2026-05-20T00:00:00Z")
        (tmp_path / "_README.md").write_text(
            "---\ntype: meta\n---\nreadme body\n", encoding="utf-8"
        )
        idx = scraper.regenerate_index(tmp_path)
        assert "real-page" in idx
        assert "_README" not in idx

    def test_marks_stale_entries(self, tmp_path: Path) -> None:
        self._make_page(tmp_path, "ancient", "2025-01-01T00:00:00Z")
        self._make_page(tmp_path, "fresh", "2026-05-20T00:00:00Z")
        now = datetime(2026, 5, 20, tzinfo=UTC)
        idx = scraper.regenerate_index(tmp_path, now=now)
        # Find the line for "ancient" and check for stale marker.
        ancient_line = next(line for line in idx.splitlines() if "ancient" in line)
        fresh_line = next(line for line in idx.splitlines() if "fresh" in line)
        assert "⚠ stale" in ancient_line
        assert "⚠ stale" not in fresh_line


# --------------------------------------------------------------------------- #
# main (CLI integration)
# --------------------------------------------------------------------------- #


class TestMain:
    def test_main_dry_run_returns_zero(
        self, fake_urlopen, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = scraper.main(["--target-dir", str(tmp_path / "out"), "--all", "--dry-run"])
        captured = capsys.readouterr()
        # stdout is the JSON report; stderr has [scrape] lines.
        report = json.loads(captured.out)
        assert report["dry_run"] is True
        assert rc == 0

    def test_main_bad_duration_returns_one(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = scraper.main(["--refresh-older-than", "bogus"])
        assert rc == 1

    def test_main_with_failures_returns_two(self, fake_urlopen, tmp_path: Path) -> None:
        del fake_urlopen["https://code.claude.com/docs/en/hooks-guide.md"]
        rc = scraper.main(["--target-dir", str(tmp_path / "out"), "--all"])
        assert rc == 2
