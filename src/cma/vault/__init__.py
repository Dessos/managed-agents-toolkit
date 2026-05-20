"""Vault — knowledge management for the cma toolkit and its consumers.

Layout (see ``docs/vault/README.md``):

* ``docs/vault/decisions/`` — markdown ADRs (per-decision history).
* ``docs/vault/learnings/`` — empirical findings.
* ``docs/vault/knowledge/`` — scraped reference material (Anthropic Claude
  Code docs); refreshed via ``cma vault refresh-knowledge``.
* ``docs/vault/context/`` — operator-authored session brief + priorities.
* ``docs/vault/_templates/`` — frontmatter-conformant templates.

This module hosts the supporting Python: the four Claude Code hook scripts
(``session_brief``, ``enforce_changelog``, ``check_adr_section_drift``,
``check_session_writes``), the scraper, the linter, the index generator,
and the new-note helper. All hooks are invoked as
``python -m cma.vault.<module>`` for worktree-portability.

Distinct from :mod:`cma.api.vaults`, which manages Anthropic Managed Agents
*credential* vaults (MCP server bearer tokens). Different concept, same
English word — see ``docs/vault/README.md`` for the verbal convention.
"""

from __future__ import annotations

__all__: list[str] = []
