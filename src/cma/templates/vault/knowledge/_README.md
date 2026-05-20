---
type: meta
status: active
created: 2026-05-20T00:00:00Z
updated: 2026-05-20T00:00:00Z
tags: [readme, knowledge, vault]
related: [[anthropic-docs/_INDEX]]
confidence: high
source: "src/cma/vault/scrape_anthropic_docs.py"
---

# Vault — `knowledge/`

> Cached reference material — both **scraped** (e.g. Anthropic Claude Code docs)
> and **operator-curated**. The goal: stop re-fetching the same canonical pages
> session after session.

## Contents

| Subfolder | What's in it | Refreshed by |
|---|---|---|
| [`anthropic-docs/`](anthropic-docs/) | Mirrors of `code.claude.com/docs/en/*.md`, curated v1 = 15 pages | `cma vault refresh-knowledge` |
| `_llms.txt` | Cached copy of the Anthropic docs index (the driver file for the scraper) | `cma vault refresh-knowledge` |

## When to refresh

- Anthropic releases a new Claude Code version (`whats-new/*` is the canary)
- A linked doc shows `⚠ stale` in `anthropic-docs/_INDEX.md` (>60 days)
- A scripted run via cron / `cma doctor` flags drift

## How to refresh

```bash
cma vault refresh-knowledge                       # curated 15 pages, 30d window
cma vault refresh-knowledge --all                 # all 134 pages from llms.txt
cma vault refresh-knowledge --refresh-older-than 7d
cma vault refresh-knowledge --page hooks --force  # one page, ignore cache
cma vault refresh-knowledge --dry-run             # show plan, write nothing
```

Each scraped page gets standard vault frontmatter:

```yaml
type: knowledge
status: active
source: "<full URL>"
fetched_at: <UTC ISO timestamp>   # required for type:knowledge with HTTP source
tags: [anthropic, claude-code, doc-mirror, <slug>]
confidence: high
```

## Why these are committed to git

Three reasons:

1. **Anti-decay**: every session can read the cached page without a WebFetch.
2. **Versioned reference**: `git log docs/vault/knowledge/anthropic-docs/hooks.md`
   shows when Anthropic's hooks API changed (and we know how our hooks need to
   adapt).
3. **Templating**: when `cma project init --with-vault` scaffolds a new
   consumer project, the scrape can ship pre-populated; consumers refresh on
   their own cadence.

## Why NOT to put operator-personal notes here

`knowledge/` is intentionally **public/citable** — content cited via wikilinks
from decisions, learnings, and the manage_adr summary. Operator scratch goes
under `context/` (current-priorities.md) or in a Tier-2 `notes/` folder when we
add one. Don't pollute `knowledge/` with anything not citable as authoritative.
