"""Resource identifier helpers.

Managed Agents resources are prefixed by type (``agent_``, ``envrn_``,
``sesn_``, ``sevt_``, ``vlt_``, ``vcrd_``, ``outc_``, ``sth_``). The toolkit
uses these prefixes to validate IDs at boundaries and to render badges in
the CLI.
"""

from __future__ import annotations

from enum import StrEnum


class ResourceKind(StrEnum):
    """Known Managed Agents resource prefixes.

    Values are the prefix WITHOUT the trailing underscore; the prefix on a
    real ID is ``f"{value}_..."``. Sourced from the docs (overview + sessions
    + vaults + multi-agent + outcomes pages).
    """

    AGENT = "agent"
    ENVIRONMENT = "envrn"
    SESSION = "sesn"
    SESSION_EVENT = "sevt"
    SESSION_THREAD = "sth"
    VAULT = "vlt"
    VAULT_CREDENTIAL = "vcrd"
    OUTCOME = "outc"
    FILE = "file"
    SKILL = "skill"
    WEBHOOK_EVENT = "event"


def kind_of(resource_id: str) -> ResourceKind | None:
    """Return the :class:`ResourceKind` for *resource_id*, or ``None``.

    Tolerant: returns ``None`` for unrecognised prefixes rather than raising,
    because the docs may introduce new resource types over time and we don't
    want toolkit code crashing on those.
    """
    if "_" not in resource_id:
        return None
    prefix, _ = resource_id.split("_", 1)
    try:
        return ResourceKind(prefix)
    except ValueError:
        return None


def expect_kind(resource_id: str, expected: ResourceKind) -> str:
    """Assert *resource_id* has the expected prefix; return it.

    Raises :class:`ValueError` with a precise message if the prefix is wrong
    or missing. Use this at boundaries where mistaking an agent ID for a
    session ID would be silently disastrous.
    """
    actual = kind_of(resource_id)
    if actual is None:
        raise ValueError(
            f"Resource ID {resource_id!r} has no recognisable prefix; "
            f"expected {expected.value}_..."
        )
    if actual is not expected:
        raise ValueError(
            f"Resource ID {resource_id!r} has prefix {actual.value!r}; "
            f"expected {expected.value!r}"
        )
    return resource_id
