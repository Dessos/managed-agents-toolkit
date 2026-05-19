"""Static bearer token authentication for the executor MCP endpoint.

Token is stored at ``.managed-agents/.state/executor_token`` (gitignored).
Rotation = generate a fresh urlsafe token, overwrite the file. Old token
is invalidated immediately because subsequent requests load the current
file contents.

The token comparison uses :func:`secrets.compare_digest` to defeat timing
side-channels.
"""

from __future__ import annotations

import contextlib
import secrets
from pathlib import Path

_TOKEN_LENGTH_BYTES = 32  # ~43 url-safe chars, 256 bits


def token_path(workspace_root: Path | str) -> Path:
    """Canonical location of the executor bearer token within a project."""
    return Path(workspace_root) / ".managed-agents" / ".state" / "executor_token"


def load_token(workspace_root: Path | str) -> str | None:
    """Return the current token, or ``None`` if no token file exists.

    Returning ``None`` lets the server explicitly refuse all requests until
    rotation, rather than silently accepting unauthenticated calls.
    """
    p = token_path(workspace_root)
    if not p.is_file():
        return None
    contents = p.read_text(encoding="utf-8").strip()
    return contents or None


def rotate_token(workspace_root: Path | str) -> str:
    """Generate + persist a fresh token. Returns the new value.

    The caller is responsible for telling the operator to update any place
    the OLD token was registered (e.g. a CMA vault credential). After this
    call, the old token will fail authentication on the next request.
    """
    p = token_path(workspace_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = secrets.token_urlsafe(_TOKEN_LENGTH_BYTES)
    # mode 0600 if possible — POSIX only; Windows ignores. Operator should
    # also rely on filesystem ACLs.
    p.write_text(new, encoding="utf-8")
    # Best-effort 0600 mode on POSIX; Windows ignores.
    with contextlib.suppress(NotImplementedError, OSError):
        p.chmod(0o600)
    return new


def verify_bearer(
    presented_token: str | None,
    *,
    workspace_root: Path | str,
) -> bool:
    """Constant-time comparison of ``presented_token`` against the stored token.

    Returns ``False`` if any of: presented is None/empty, no token file
    exists, comparison fails. Never returns True for a missing-token state.
    """
    if not presented_token:
        return False
    stored = load_token(workspace_root)
    if stored is None:
        return False
    return secrets.compare_digest(presented_token, stored)


def extract_bearer_from_header(header_value: str | None) -> str | None:
    """Parse ``Authorization: Bearer <token>`` and return just the token.

    Returns ``None`` for any malformed value. Tolerant of extra whitespace
    but strict about the ``Bearer`` scheme.
    """
    if not header_value:
        return None
    parts = header_value.strip().split(None, 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None
