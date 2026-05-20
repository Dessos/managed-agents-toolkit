"""Shared JSONL telemetry for vault hooks.

Every hook event (allow / block / bypass / drift / skip) appends one JSONL
line to ``$CMA_VAULT_HOOK_LOG`` (default ``O:/Temp/cma-vault-hook.jsonl`` on
Windows, ``/tmp/cma-vault-hook.jsonl`` elsewhere).

The log is intentionally out-of-repo: it's per-machine observability, not
shared history. Use ``cma vault audit`` (Tier 2) for queries.

Best-effort: swallows all errors. Hook execution must never fail because
telemetry plumbing broke.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def _default_log_path() -> Path:
    """Where the telemetry log lives by default.

    Windows operators per CLAUDE.md use ``O:/Temp/`` for ephemeral state.
    Others fall back to ``/tmp/``. Override via ``$CMA_VAULT_HOOK_LOG``.
    """
    if sys.platform.startswith("win"):
        return Path("O:/Temp/cma-vault-hook.jsonl")
    return Path("/tmp/cma-vault-hook.jsonl")


def log_event(**fields: object) -> None:
    """Append one JSONL line. Best-effort — swallows all errors.

    Standard fields the hooks supply:

    - ``hook`` (str) — module name (``enforce_changelog``, ``check_session_writes``, etc.)
    - ``outcome`` (str) — ``allow`` / ``block`` / ``bypass`` / ``warn`` / ``skip``
    - ``cwd`` (str) — working directory
    - ``ts`` (str) — auto-added if absent

    Plus hook-specific fields the caller passes (e.g., ``bypass_var``,
    ``new_bullets_count``).
    """
    path = Path(os.environ.get("CMA_VAULT_HOOK_LOG", str(_default_log_path())))
    fields.setdefault("ts", datetime.now(UTC).isoformat())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False, default=str) + "\n")
    except OSError:
        # Telemetry must never break the hook.
        pass
