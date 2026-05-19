"""Authenticated Anthropic SDK client + beta header injection.

The toolkit's single entry point for instantiating the Anthropic client. All
other modules MUST go through :func:`get_client` so that the beta-header set,
retry policy, and telemetry hookup stay consistent.

Beta headers attached on every call:

* ``managed-agents-2026-04-01`` — the Managed Agents beta itself.
* ``cache-diagnosis-2026-04-07`` — enables ``diagnostics.previous_message_id``
  + ``cache_miss_reason`` on every response (free observability per
  ``§2.15.2`` of the plan).

For requests that touch the Files API (rubric uploads, deliverables), use
:func:`with_files_beta` to layer on ``files-api-2025-04-14`` without mutating
the shared client.

API key resolution
------------------

The toolkit resolves the API key from a chain of sources, stopping on the
first valid match. "Valid" means a real Anthropic key shape (starts with
``sk-ant-`` and is at least 30 characters). Common defensive sentinels
(e.g. ``sk-ant-..``) are explicitly rejected so they never reach the API.

Order of resolution:

1. ``ANTHROPIC_API_KEY`` env var (if real, not a sentinel).
2. ``CMA_API_KEY_HELPER`` env var — a shell command whose stdout is the key.
3. ``apiKeyHelper`` field in ``~/.claude/settings.json`` — same semantics.
4. Fail with a precise error message listing what was tried.

Examples of helper commands (cross-platform):

.. code-block:: text

    # Windows PowerShell + Microsoft.PowerShell.SecretManagement:
    powershell -NoProfile -Command "Get-Secret AnthropicApiKey -AsPlainText"

    # macOS Keychain via the security tool:
    security find-generic-password -a $USER -s anthropic-api-key -w

    # Linux secret-tool (GNOME keyring):
    secret-tool lookup service anthropic-api-key

See ``docs/api-key-storage.md`` for the full setup walkthrough.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

# The anthropic SDK is a required dependency (see pyproject.toml).
import anthropic

# ---------------------------------------------------------------------------
# Beta header set — single source of truth
# ---------------------------------------------------------------------------

MANAGED_AGENTS_BETA = "managed-agents-2026-04-01"
"""The Managed Agents beta header. Required on every endpoint."""

CACHE_DIAGNOSIS_BETA = "cache-diagnosis-2026-04-07"
"""Enables :attr:`diagnostics.previous_message_id` round-tripping and
``cache_miss_reason`` in responses."""

FILES_API_BETA = "files-api-2025-04-14"
"""Files API beta — needed for rubric uploads and deliverable downloads."""

DEFAULT_BETAS = (MANAGED_AGENTS_BETA, CACHE_DIAGNOSIS_BETA)
"""Headers attached to every CMA-issued request by default."""


# ---------------------------------------------------------------------------
# Sentinel detection — reject placeholder values before they hit the API
# ---------------------------------------------------------------------------

# Real Anthropic keys are ``sk-ant-api03-...`` followed by a long token; the
# total length is typically 80+ chars. We require at least 30 to catch even
# very abbreviated future shapes while still rejecting common decoys like
# ``sk-ant-..`` (10 chars), ``sk-ant-placeholder`` (~18 chars), etc.
_MIN_REAL_KEY_LENGTH = 30
_REAL_KEY_PREFIX = "sk-ant-"

# Patterns we explicitly identify as known decoys for clearer error messages.
_KNOWN_SENTINEL_PATTERNS = (
    re.compile(r"^sk-ant-\.+$"),               # sk-ant-.., sk-ant-...
    re.compile(r"^sk-ant-(placeholder|stub|todo|fake|disabled|none|null)$", re.IGNORECASE),
    re.compile(r"^sk-ant-x+$"),                # sk-ant-xxxx
)


def _looks_like_real_key(value: str) -> bool:
    """Return True iff *value* could plausibly be a real Anthropic API key."""
    if not value or not value.startswith(_REAL_KEY_PREFIX):
        return False
    if len(value) < _MIN_REAL_KEY_LENGTH:
        return False
    return not any(p.match(value) for p in _KNOWN_SENTINEL_PATTERNS)


def _why_not_real_key(value: str) -> str:
    """Human-readable reason why *value* was rejected. For error messages."""
    if not value:
        return "value is empty"
    if not value.startswith(_REAL_KEY_PREFIX):
        return f"does not start with {_REAL_KEY_PREFIX!r}"
    if len(value) < _MIN_REAL_KEY_LENGTH:
        return f"too short ({len(value)} chars; real keys are ~80+)"
    if any(p.match(value) for p in _KNOWN_SENTINEL_PATTERNS):
        return "matches a known sentinel/placeholder pattern"
    return "unknown rejection (this is a bug)"


# ---------------------------------------------------------------------------
# Helper command resolution
# ---------------------------------------------------------------------------


def _claude_code_settings_helper() -> str | None:
    """Return the ``apiKeyHelper`` from ``~/.claude/settings.json`` if set.

    Tolerant of missing file / unparseable JSON / missing field — returns
    None in all those cases. The function never raises; it's part of a
    fallback chain.
    """
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.is_file():
        return None
    try:
        raw = settings_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    helper = data.get("apiKeyHelper") if isinstance(data, dict) else None
    return helper if isinstance(helper, str) and helper.strip() else None


def _run_helper(command: str) -> str:
    """Execute *command* (shell string) and return its stdout, stripped.

    Uses ``shell=True`` because the apiKeyHelper format (matching Claude
    Code's convention) is a shell-command string, not an argv list. The
    helper source is operator-controlled (env var or settings.json), so
    shell-injection risk is bounded by trust in those sources.

    Raises :class:`RuntimeError` on non-zero exit, timeout, or empty stdout.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,  # PowerShell cold start ~200ms; 10s gives 50x headroom
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"apiKeyHelper command timed out after 10s: {command!r}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"apiKeyHelper command failed to start: {command!r}: {exc}"
        ) from exc

    if result.returncode != 0:
        stderr_tail = result.stderr.strip().splitlines()[-3:]
        raise RuntimeError(
            f"apiKeyHelper command exited {result.returncode}: {command!r}. "
            f"stderr tail: {stderr_tail!r}"
        )
    output = result.stdout.strip()
    if not output:
        raise RuntimeError(
            f"apiKeyHelper command produced empty stdout: {command!r}"
        )
    return output


# ---------------------------------------------------------------------------
# Resolution chain
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _api_key() -> str:
    """Return the API key from the first valid source in the resolution chain.

    Cached for the lifetime of the process — secret rotation requires a
    restart. Tests can clear the cache via ``_api_key.cache_clear()``.

    Raises :class:`RuntimeError` with a precise diagnostic if no source
    yields a real key.
    """
    rejection_reasons: list[str] = []

    # Source 1: ANTHROPIC_API_KEY env var.
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_key:
        if _looks_like_real_key(env_key):
            return env_key
        rejection_reasons.append(
            f"ANTHROPIC_API_KEY env var rejected: {_why_not_real_key(env_key)}"
        )

    # Source 2: CMA_API_KEY_HELPER env var.
    cma_helper = os.environ.get("CMA_API_KEY_HELPER", "").strip()
    if cma_helper:
        try:
            output = _run_helper(cma_helper)
        except RuntimeError as exc:
            rejection_reasons.append(f"CMA_API_KEY_HELPER: {exc}")
        else:
            if _looks_like_real_key(output):
                return output
            rejection_reasons.append(
                f"CMA_API_KEY_HELPER output rejected: {_why_not_real_key(output)}"
            )

    # Source 3: ~/.claude/settings.json apiKeyHelper.
    cc_helper = _claude_code_settings_helper()
    if cc_helper:
        try:
            output = _run_helper(cc_helper)
        except RuntimeError as exc:
            rejection_reasons.append(f"~/.claude/settings.json apiKeyHelper: {exc}")
        else:
            if _looks_like_real_key(output):
                return output
            rejection_reasons.append(
                "~/.claude/settings.json apiKeyHelper output rejected: "
                f"{_why_not_real_key(output)}"
            )

    # All sources exhausted.
    detail = "\n  - ".join(rejection_reasons) if rejection_reasons else "(no sources configured)"
    raise RuntimeError(
        "Could not resolve a valid Anthropic API key. Tried in order: "
        "ANTHROPIC_API_KEY env, CMA_API_KEY_HELPER env, "
        "~/.claude/settings.json apiKeyHelper.\n"
        f"  - {detail}\n"
        "See docs/api-key-storage.md for setup."
    )


# ---------------------------------------------------------------------------
# Client construction
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_client() -> anthropic.Anthropic:
    """Return the singleton authenticated Anthropic client.

    Cached because the SDK's underlying ``httpx.Client`` is expensive to
    construct and reuses connections. Tests can monkeypatch this function or
    call :func:`get_client.cache_clear` between cases.
    """
    return anthropic.Anthropic(
        api_key=_api_key(),
        default_headers={
            # Comma-separated per SDK convention. The SDK also accepts a
            # ``betas=`` kwarg per-call, but ``default_headers`` covers every
            # endpoint uniformly — important for resources we wrap (agents,
            # environments, vaults) where forgetting the header surfaces as
            # a 400 rather than a typed error.
            "anthropic-beta": ", ".join(DEFAULT_BETAS),
        },
    )


def with_files_beta(client: anthropic.Anthropic | None = None) -> dict[str, Any]:
    """Return per-call kwargs to layer on the Files API beta.

    Pass the result as ``**with_files_beta()`` to any SDK call that touches
    the Files API. Keeps the shared client's default header set unchanged.
    """
    return {
        "extra_headers": {
            "anthropic-beta": ", ".join((*DEFAULT_BETAS, FILES_API_BETA)),
        },
    }
