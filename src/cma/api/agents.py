"""Agents API wrapper.

Thin pass-through over ``client.beta.agents.*`` with telemetry + a local
registry that records the mapping from ``(project, agent_name) →
(agent_id, version)``. The registry enables ``cma agent sync`` to no-op
when local YAML matches the registered version.

The registry lives in :mod:`cma.core.budget`'s SQLite DB (single file) to
keep the toolkit's state footprint tight.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cma.api.client import get_client
from cma.core.identifiers import ResourceKind, expect_kind
from cma.telemetry import emit

# ---------------------------------------------------------------------------
# Registry — tracks what's registered server-side so we can avoid no-op
# updates and so ``cma agent sync`` is deterministic.
# ---------------------------------------------------------------------------


_REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_registry (
    project       TEXT NOT NULL,
    name          TEXT NOT NULL,
    agent_id      TEXT NOT NULL,
    version       INTEGER NOT NULL,
    spec_hash     TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (project, name)
);

CREATE INDEX IF NOT EXISTS agent_registry_agent_id_idx
    ON agent_registry (agent_id);
"""


@dataclass(frozen=True, slots=True)
class RegisteredAgent:
    """One row from the agent registry."""

    project: str
    name: str
    agent_id: str
    version: int
    spec_hash: str
    updated_at: str


class AgentRegistry:
    """Local SQLite cache of registered agents.

    Lookup is by ``(project, name)``. The registry is purely a cache — losing
    it just means the next ``cma agent sync`` re-discovers via list calls.
    """

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

    def get(self, *, project: str, name: str) -> RegisteredAgent | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT project, name, agent_id, version, spec_hash, updated_at
                FROM agent_registry WHERE project = ? AND name = ?
                """,
                (project, name),
            ).fetchone()
        if row is None:
            return None
        return RegisteredAgent(*row)

    def upsert(self, agent: RegisteredAgent) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_registry
                    (project, name, agent_id, version, spec_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project, name) DO UPDATE SET
                    agent_id=excluded.agent_id,
                    version=excluded.version,
                    spec_hash=excluded.spec_hash,
                    updated_at=excluded.updated_at
                """,
                (
                    agent.project,
                    agent.name,
                    agent.agent_id,
                    agent.version,
                    agent.spec_hash,
                    agent.updated_at,
                ),
            )


# ---------------------------------------------------------------------------
# Spec hashing — used to detect "nothing changed; skip API call"
# ---------------------------------------------------------------------------


def spec_hash(spec: dict[str, Any]) -> str:
    """Stable hash of an agent spec for change detection.

    We sort keys recursively so dict insertion order doesn't affect the hash.
    Lists are kept in order — list reorders are semantically meaningful for
    tools (per the docs: reordering tools invalidates the cache and means
    the agent's tool palette changed).

    Returns 32 hex chars (128 bits of SHA-256). Birthday-bound collision is
    ~2^64 entries; far beyond any realistic agent registry size. Was 16
    chars in 0.0.1; bumped in 0.1.0 for future-proofing at zero cost.
    """
    serialized = json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Wrappers
# ---------------------------------------------------------------------------


def create_agent(
    *,
    project: str,
    spec: dict[str, Any],
    registry: AgentRegistry,
) -> Any:
    """Create a new agent. Records the result in the local registry.

    Returns the SDK's agent object verbatim so callers can read ``.id``,
    ``.version``, etc.
    """
    client = get_client()
    emit(
        domain="cma.api.agents",
        action="create_start",
        extra={"project": project, "name": spec.get("name"), "model": spec.get("model")},
    )
    agent = client.beta.agents.create(**spec)
    registry.upsert(
        RegisteredAgent(
            project=project,
            name=spec.get("name", "<unnamed>"),
            agent_id=expect_kind(agent.id, ResourceKind.AGENT),
            version=getattr(agent, "version", 1),
            spec_hash=spec_hash(spec),
            updated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    )
    emit(
        domain="cma.api.agents",
        action="create_done",
        extra={
            "project": project,
            "name": spec.get("name"),
            "agent_id": agent.id,
            "version": getattr(agent, "version", 1),
        },
    )
    return agent


def sync_agent(
    *,
    project: str,
    spec: dict[str, Any],
    registry: AgentRegistry,
) -> Any:
    """Idempotent create-or-update.

    Compares local ``spec`` against the registry's hash. If it matches, no
    API call is made (mirrors the SDK's own no-op detection but saves a
    round-trip). If the spec has drifted, calls ``update`` to bump version.
    If no registry entry exists, calls ``create``.
    """
    name = spec.get("name", "<unnamed>")
    current = registry.get(project=project, name=name)
    new_hash = spec_hash(spec)

    if current is None:
        return create_agent(project=project, spec=spec, registry=registry)

    if current.spec_hash == new_hash:
        emit(
            domain="cma.api.agents",
            action="sync_noop",
            extra={
                "project": project,
                "name": name,
                "agent_id": current.agent_id,
                "version": current.version,
            },
        )
        return current

    client = get_client()
    emit(
        domain="cma.api.agents",
        action="update_start",
        extra={
            "project": project,
            "name": name,
            "agent_id": current.agent_id,
            "old_hash": current.spec_hash,
            "new_hash": new_hash,
        },
    )
    # The SDK's update endpoint expects (agent_id, version=..., **fields).
    update_kwargs = {k: v for k, v in spec.items() if k != "name"}
    update_kwargs["version"] = current.version
    agent = client.beta.agents.update(current.agent_id, **update_kwargs)
    registry.upsert(
        RegisteredAgent(
            project=project,
            name=name,
            agent_id=current.agent_id,
            version=getattr(agent, "version", current.version + 1),
            spec_hash=new_hash,
            updated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    )
    emit(
        domain="cma.api.agents",
        action="update_done",
        extra={
            "project": project,
            "name": name,
            "agent_id": current.agent_id,
            "version": getattr(agent, "version", current.version + 1),
        },
    )
    return agent


def archive_agent(agent_id: str) -> Any:
    """Archive an agent so existing sessions continue but no new ones start."""
    # Validate prefix FIRST — fails fast on operator typos without burning
    # auth setup or hitting the SDK.
    expect_kind(agent_id, ResourceKind.AGENT)
    emit(domain="cma.api.agents", action="archive", extra={"agent_id": agent_id})
    return get_client().beta.agents.archive(agent_id)
