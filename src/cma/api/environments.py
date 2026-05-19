"""Environments API wrapper.

Environments aren't versioned API-side per the docs, so the registry stays
simple: just ``(project, name) → environment_id``. The toolkit treats
``cma env sync`` as create-if-missing; updates require explicit
``--force`` because they affect every future session sharing the env.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cma.api.client import get_client
from cma.core.identifiers import ResourceKind, expect_kind
from cma.telemetry import emit

_REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS environment_registry (
    project        TEXT NOT NULL,
    name           TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    config_json    TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (project, name)
);
"""


class EnvironmentRegistry:
    """Local cache of registered environments."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_REGISTRY_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
        finally:
            conn.close()

    def get_id(self, *, project: str, name: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT environment_id FROM environment_registry WHERE project = ? AND name = ?",
                (project, name),
            ).fetchone()
        return row[0] if row else None

    def upsert(self, *, project: str, name: str, environment_id: str, config_json: str, updated_at: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO environment_registry
                    (project, name, environment_id, config_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project, name) DO UPDATE SET
                    environment_id=excluded.environment_id,
                    config_json=excluded.config_json,
                    updated_at=excluded.updated_at
                """,
                (project, name, environment_id, config_json, updated_at),
            )


# ---------------------------------------------------------------------------
# Wrappers
# ---------------------------------------------------------------------------


def create_environment(*, name: str, config: dict[str, Any]) -> Any:
    """Create a fresh cloud environment.

    The Environments page notes a doc-drift between "unrestricted is the
    default" (Environments page) and "Network is disabled by default" (Cloud
    Containers page). The toolkit ALWAYS sets ``networking`` explicitly to
    bypass that ambiguity — callers must provide it. We refuse to omit it.
    """
    if "networking" not in config:
        raise ValueError(
            "Environment config must set 'networking' explicitly (no default). "
            "Use {'type': 'limited', 'allowed_hosts': [...]} for production or "
            "{'type': 'unrestricted'} for dev."
        )
    client = get_client()
    emit(
        domain="cma.api.environments",
        action="create_start",
        extra={"name": name, "networking_type": config["networking"].get("type")},
    )
    # SDK wants a typed BetaCloudConfigParams here; we pass dict for ergonomics.
    env = client.beta.environments.create(name=name, config=config)  # type: ignore[arg-type]
    emit(
        domain="cma.api.environments",
        action="create_done",
        extra={"name": name, "environment_id": env.id},
    )
    return env


def archive_environment(environment_id: str) -> Any:
    """Archive an environment — existing sessions continue, no new sessions."""
    # Validate prefix FIRST — fails fast on operator typos without burning
    # auth setup or hitting the SDK.
    expect_kind(environment_id, ResourceKind.ENVIRONMENT)
    emit(domain="cma.api.environments", action="archive", extra={"environment_id": environment_id})
    return get_client().beta.environments.archive(environment_id)
