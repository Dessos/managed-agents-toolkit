"""Scrape Anthropic Claude Code docs into ``docs/vault/knowledge/anthropic-docs/``.

The Anthropic docs publish raw markdown at predictable URLs (every entry in
``https://code.claude.com/docs/llms.txt`` ends in ``.md``). This scraper:

1. Fetches ``llms.txt`` (the canonical doc index Anthropic maintains).
2. Filters to the curated v1 page set (see :data:`CURATED_PAGES`) by default,
   or fetches all 134 pages with ``--all``.
3. For each page: skips if cached and fresh (``fetched_at`` within
   ``--refresh-older-than``, default 30 days); else fetches via stdlib
   ``urllib.request`` and writes ``<slug>.md`` with prepended YAML frontmatter.
4. Regenerates ``_INDEX.md`` listing every page + last-fetched date.

Pure stdlib — no ``httpx``, no ``WebFetch``, no HTML→markdown conversion (the
upstream is already markdown). Designed to run from a Claude Code hook OR
standalone via ``python -m cma.vault.scrape_anthropic_docs``.

Exit codes:
  0 — success (some pages may have been skipped fresh).
  1 — unrecoverable error (network down, llms.txt unreachable).
  2 — at least one page failed to fetch but others succeeded; check stderr.

Telemetry:
  Summary is printed to stdout as a single JSON line for piping. Per-page
  events go to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

LLMS_TXT_URL = "https://code.claude.com/docs/llms.txt"
DOC_BASE_URL = "https://code.claude.com/docs/en"

#: The curated v1 scrape list. Chosen for highest leverage on vault + hook
#: work — see ``docs/vault/README.md`` for the rationale per page.
#: Slugs are relative to ``DOC_BASE_URL`` and exclude the ``.md`` suffix.
CURATED_PAGES: tuple[str, ...] = (
    "overview",
    "hooks",
    "hooks-guide",
    "skills",
    "sub-agents",
    "commands",  # Anthropic's slug for "slash commands" docs
    "mcp",
    "memory",
    "settings",
    "permissions",
    "best-practices",
    "common-workflows",
    "cli-reference",
    "plugins",
    "output-styles",
)

DEFAULT_REFRESH_WINDOW = timedelta(days=30)
DEFAULT_TIMEOUT_SECONDS = 15

# --------------------------------------------------------------------------- #
# Frontmatter helpers
# --------------------------------------------------------------------------- #

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
FETCHED_AT_RE = re.compile(r"^fetched_at:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)


def _now_iso() -> str:
    """Current UTC time in ISO 8601 with seconds precision (no microseconds)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def parse_fetched_at(content: str) -> datetime | None:
    """Extract the ``fetched_at:`` field from a cached page's frontmatter.

    Returns ``None`` if absent / malformed (caller treats as stale → refresh).
    """
    fm = FRONTMATTER_RE.match(content)
    if not fm:
        return None
    m = FETCHED_AT_RE.search(fm.group(1))
    if not m:
        return None
    raw = m.group(1).strip().rstrip("Z")
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except ValueError:
        return None


def build_frontmatter(*, slug: str, url: str, created: str, updated: str, fetched_at: str) -> str:
    """Render the YAML frontmatter block for a scraped page.

    Schema matches ``docs/vault/_templates`` conventions:
    ``type: knowledge`` + required ``fetched_at`` for HTTP-sourced notes.
    """
    return (
        "---\n"
        f"type: knowledge\n"
        f"status: active\n"
        f"created: {created}\n"
        f"updated: {updated}\n"
        f'tags: [anthropic, claude-code, doc-mirror, {slug}]\n'
        f"related: []\n"
        f"confidence: high\n"
        f'source: "{url}"\n'
        f"fetched_at: {fetched_at}\n"
        "---\n\n"
    )


def strip_existing_frontmatter(content: str) -> str:
    """Remove any leading YAML frontmatter so we can re-prepend a fresh one."""
    return FRONTMATTER_RE.sub("", content, count=1)


# --------------------------------------------------------------------------- #
# llms.txt parsing
# --------------------------------------------------------------------------- #

#: Matches a Markdown list entry: ``- [Title](https://...md): description``
LLMS_ENTRY_RE = re.compile(
    r"^- \[(?P<title>[^\]]+)\]\((?P<url>https://[^\)]+\.md)\)(?::\s*(?P<desc>.+))?$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class DocEntry:
    """One entry in ``llms.txt`` — a title + URL + description."""

    title: str
    url: str
    description: str = ""

    @property
    def slug(self) -> str:
        """Filename slug (URL basename minus ``.md``).

        Examples:
            ``.../hooks-guide.md`` → ``hooks-guide``
            ``.../agent-sdk/hooks.md`` → ``agent-sdk-hooks``
            ``.../whats-new/2026-w20.md`` → ``whats-new-2026-w20``
        """
        # Drop scheme + host, drop the ``/docs/en/`` prefix.
        path = self.url.split("/docs/en/", 1)[-1]
        # Strip .md suffix.
        if path.endswith(".md"):
            path = path[:-3]
        # Replace nested slashes with dashes so we get a flat filename.
        return path.replace("/", "-")


def parse_llms_txt(text: str) -> list[DocEntry]:
    """Parse ``llms.txt`` into a list of :class:`DocEntry`.

    Tolerates section headings, blockquotes, and other Markdown — only lines
    matching :data:`LLMS_ENTRY_RE` become entries.
    """
    return [
        DocEntry(title=m.group("title"), url=m.group("url"), description=m.group("desc") or "")
        for m in LLMS_ENTRY_RE.finditer(text)
    ]


# --------------------------------------------------------------------------- #
# HTTP fetch (stdlib only)
# --------------------------------------------------------------------------- #


def fetch_url(url: str, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """GET a URL and return the response body as UTF-8 text.

    Raises :class:`urllib.error.URLError` or :class:`TimeoutError` on failure;
    callers should catch and decide whether to abort or skip.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "cma-vault-scraper/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw: bytes = resp.read()
    return raw.decode("utf-8")


# --------------------------------------------------------------------------- #
# Cache freshness
# --------------------------------------------------------------------------- #


def is_fresh(path: Path, refresh_window: timedelta, *, now: datetime | None = None) -> bool:
    """True if ``path`` exists, has parseable ``fetched_at``, and is within window.

    A missing file, missing frontmatter, or unparseable date all count as
    "stale" — better to re-fetch than trust ambiguous cache state.
    """
    if not path.is_file():
        return False
    try:
        fetched = parse_fetched_at(path.read_text(encoding="utf-8"))
    except OSError:
        return False
    if fetched is None:
        return False
    now = now or datetime.now(UTC)
    return (now - fetched) < refresh_window


# --------------------------------------------------------------------------- #
# Index generation
# --------------------------------------------------------------------------- #


def regenerate_index(target_dir: Path, *, now: datetime | None = None) -> str:
    """Build ``_INDEX.md`` listing every scraped page + last-fetched date.

    Idempotent — running twice without intervening changes produces identical
    bytes. Returns the rendered markdown (also written to disk).
    """
    now = now or datetime.now(UTC)
    rows: list[tuple[str, str, str]] = []  # (slug, source, fetched_at)
    for md in sorted(target_dir.glob("*.md")):
        # Skip underscore-prefixed files (_INDEX.md, _README.md, etc.) — these
        # are meta-files describing the folder, not indexable knowledge entries.
        if md.name.startswith("_"):
            continue
        content = md.read_text(encoding="utf-8")
        fetched = parse_fetched_at(content)
        fetched_str = fetched.strftime("%Y-%m-%d") if fetched else "?"
        # Extract source URL from frontmatter for the table.
        src_match = re.search(r'^source:\s*"(.+?)"\s*$', content, re.MULTILINE)
        src = src_match.group(1) if src_match else ""
        rows.append((md.stem, src, fetched_str))

    lines: list[str] = [
        "---",
        "type: meta",
        "status: active",
        f"created: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"updated: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "tags: [index, anthropic, claude-code]",
        "related: []",
        "confidence: high",
        'source: "auto-generated by cma.vault.scrape_anthropic_docs"',
        "---",
        "",
        "# Anthropic Claude Code Docs — Index",
        "",
        "> Auto-generated by `cma vault refresh-knowledge`. Do not edit by hand;",
        "> changes will be overwritten on the next refresh.",
        "",
        "| Page | Source | Last fetched |",
        "|---|---|---|",
    ]
    stale_threshold = now - timedelta(days=60)
    for slug, src, fetched_str in rows:
        stale = ""
        if fetched_str != "?":
            try:
                fetched_dt = datetime.strptime(fetched_str, "%Y-%m-%d").replace(tzinfo=UTC)
                if fetched_dt < stale_threshold:
                    stale = " ⚠ stale"
            except ValueError:
                pass
        lines.append(f"| [{slug}]({slug}.md) | {src} | {fetched_str}{stale} |")
    lines.append("")
    rendered = "\n".join(lines)
    (target_dir / "_INDEX.md").write_text(rendered, encoding="utf-8")
    return rendered


# --------------------------------------------------------------------------- #
# Main scrape loop
# --------------------------------------------------------------------------- #


@dataclass
class ScrapeReport:
    """Summary of one scraper invocation, emitted as JSON on stdout."""

    fetched: list[str] = field(default_factory=list)
    skipped_fresh: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (slug, error)
    target_dir: str = ""
    refresh_window_days: int = 0
    mode: str = "curated"  # curated | all | single
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "skipped_fresh": self.skipped_fresh,
            "failed": [{"slug": s, "error": e} for s, e in self.failed],
            "target_dir": self.target_dir,
            "refresh_window_days": self.refresh_window_days,
            "mode": self.mode,
            "dry_run": self.dry_run,
        }


def select_entries(
    all_entries: list[DocEntry],
    *,
    curated: bool,
    only_page: str | None,
) -> list[DocEntry]:
    """Filter ``llms.txt`` entries down to what this run should fetch."""
    if only_page:
        return [e for e in all_entries if e.slug == only_page]
    if curated:
        return [e for e in all_entries if e.slug in CURATED_PAGES]
    return all_entries


def scrape(
    *,
    target_dir: Path,
    curated: bool = True,
    only_page: str | None = None,
    refresh_window: timedelta = DEFAULT_REFRESH_WINDOW,
    force: bool = False,
    dry_run: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> ScrapeReport:
    """Run the scrape. Returns a :class:`ScrapeReport` summary.

    On llms.txt unreachable, raises :class:`urllib.error.URLError`. On per-page
    failures, records the error in the report and keeps going.
    """
    report = ScrapeReport(
        target_dir=str(target_dir),
        refresh_window_days=int(refresh_window.total_seconds() // 86400),
        mode="single" if only_page else ("curated" if curated else "all"),
        dry_run=dry_run,
    )

    # Step 1: fetch llms.txt (cache it too — it's the entry-point doc).
    knowledge_root = target_dir.parent
    print(f"[scrape] fetching index: {LLMS_TXT_URL}", file=sys.stderr)
    llms_text = fetch_url(LLMS_TXT_URL, timeout=timeout)
    all_entries = parse_llms_txt(llms_text)
    print(f"[scrape] index lists {len(all_entries)} pages", file=sys.stderr)

    # Persist the index file itself for traceability + later parsing.
    if not dry_run:
        knowledge_root.mkdir(parents=True, exist_ok=True)
        (knowledge_root / "_llms.txt").write_text(llms_text, encoding="utf-8")

    # Step 2: filter to selection.
    targets = select_entries(all_entries, curated=curated, only_page=only_page)
    if not targets:
        msg = f"no pages matched selection (curated={curated}, only_page={only_page!r})"
        print(f"[scrape] WARNING: {msg}", file=sys.stderr)
        return report
    print(f"[scrape] selected {len(targets)} pages to consider", file=sys.stderr)

    # Step 3: ensure target dir + fetch each.
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    today = _today_iso()
    now_iso = _now_iso()

    for entry in targets:
        slug = entry.slug
        out_path = target_dir / f"{slug}.md"

        # Freshness gate.
        if not force and is_fresh(out_path, refresh_window, now=now):
            report.skipped_fresh.append(slug)
            print(f"[scrape] SKIP fresh: {slug}", file=sys.stderr)
            continue

        if dry_run:
            report.fetched.append(slug)
            print(f"[scrape] DRY-RUN would fetch: {slug}", file=sys.stderr)
            continue

        # Fetch.
        print(f"[scrape] FETCH: {entry.url}", file=sys.stderr)
        try:
            body = fetch_url(entry.url, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            err = f"{type(exc).__name__}: {exc}"
            report.failed.append((slug, err))
            print(f"[scrape] FAIL: {slug} — {err}", file=sys.stderr)
            continue

        # Compose: frontmatter + body (stripping any upstream frontmatter,
        # which is unlikely for raw markdown but cheap to guard against).
        created = today
        if out_path.is_file():
            existing = out_path.read_text(encoding="utf-8")
            fm = FRONTMATTER_RE.match(existing)
            if fm:
                created_match = re.search(r"^created:\s*(\S+)\s*$", fm.group(1), re.MULTILINE)
                if created_match:
                    created = created_match.group(1)
        frontmatter = build_frontmatter(
            slug=slug,
            url=entry.url,
            created=created,
            updated=now_iso,
            fetched_at=now_iso,
        )
        new_content = frontmatter + strip_existing_frontmatter(body).lstrip("\n")

        # Atomic write.
        tmp_path = out_path.with_suffix(".md.tmp")
        tmp_path.write_text(new_content, encoding="utf-8")
        os.replace(tmp_path, out_path)
        report.fetched.append(slug)

    # Step 4: regenerate index.
    if not dry_run:
        regenerate_index(target_dir, now=now)

    return report


# --------------------------------------------------------------------------- #
# CLI / __main__
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m cma.vault.scrape_anthropic_docs",
        description="Scrape Anthropic Claude Code docs into docs/vault/knowledge/anthropic-docs/",
    )
    p.add_argument(
        "--target-dir",
        type=Path,
        default=Path("docs/vault/knowledge/anthropic-docs"),
        help="Output directory (default: ./docs/vault/knowledge/anthropic-docs)",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--all",
        dest="curated",
        action="store_false",
        help="Scrape ALL pages in llms.txt (~134), not just the curated 15",
    )
    g.add_argument(
        "--page",
        type=str,
        default=None,
        help="Scrape exactly one page by slug (e.g. 'hooks-guide')",
    )
    p.add_argument(
        "--refresh-older-than",
        type=str,
        default="30d",
        help="Refresh window, e.g. '30d', '7d', '24h'. Default: 30d.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch even if cache is fresh",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fetched without writing anything",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    p.set_defaults(curated=True)
    return p


_DURATION_RE = re.compile(r"^(?P<n>\d+)(?P<unit>[smhd])$")


def parse_duration(text: str) -> timedelta:
    """Parse a short duration string like ``30d``, ``7d``, ``24h``, ``120s``."""
    m = _DURATION_RE.match(text.strip())
    if not m:
        raise ValueError(f"unrecognized duration: {text!r} (expected e.g. '30d', '24h', '120s')")
    n = int(m.group("n"))
    unit = m.group("unit")
    return {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
    }[unit]


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        window = parse_duration(args.refresh_older_than)
    except ValueError as exc:
        print(f"[scrape] ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        report = scrape(
            target_dir=args.target_dir,
            curated=args.curated,
            only_page=args.page,
            refresh_window=window,
            force=args.force,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[scrape] FATAL: index unreachable — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.to_dict(), indent=2))
    if report.failed:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
