"""Events API wrapper — send + stream + replay-safe tailing.

Implements the "history-first then tail with dedup" pattern from the docs
(events-and-streaming page). The pattern is:

1. Open stream.
2. List historical events; record their IDs.
3. Tail the stream, skipping events whose IDs are already known.

This means a reconnect after a transient network blip doesn't miss events
and doesn't process them twice.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from cma.api.client import get_client
from cma.core.identifiers import ResourceKind, expect_kind
from cma.telemetry import emit

# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


def send_events(session_id: str, events: list[dict[str, Any]]) -> Any:
    """Send one or more events to a session.

    Each event must have ``type`` matching the event type catalog (see
    docs/events-and-streaming page or :class:`cma.core.events.EventType`).
    """
    expect_kind(session_id, ResourceKind.SESSION)
    if not events:
        raise ValueError("Cannot send an empty events list.")
    emit(
        domain="cma.api.events",
        action="send",
        extra={
            "session_id": session_id,
            "count": len(events),
            "types": [e.get("type") for e in events],
        },
    )
    # SDK expects typed param classes per-event; we accept dicts and let the
    # SDK's pydantic adapter coerce. Type-ignored because the union of typed
    # classes vs dict-in is one of those SDK-author-vs-consumer tradeoffs we
    # accept here (the alternative is wrapping every event type explicitly).
    return get_client().beta.sessions.events.send(session_id, events=events)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# List + stream
# ---------------------------------------------------------------------------


def list_events(
    session_id: str,
    *,
    types: list[str] | None = None,
) -> list[Any]:
    """List historical events. Optional ``types`` filter narrows the response."""
    expect_kind(session_id, ResourceKind.SESSION)
    client = get_client()
    kwargs: dict[str, Any] = {}
    if types:
        kwargs["types"] = types
    page = client.beta.sessions.events.list(session_id, **kwargs)
    # The SDK returns a pagination object; we collect data eagerly. For very
    # long sessions a caller should iterate rather than collect — see
    # ``iter_events`` below for that path.
    return list(getattr(page, "data", []) or [])


def iter_events(
    session_id: str,
    *,
    types: list[str] | None = None,
) -> Iterator[Any]:
    """Yield historical events lazily — useful for very long sessions."""
    expect_kind(session_id, ResourceKind.SESSION)
    client = get_client()
    kwargs: dict[str, Any] = {}
    if types:
        kwargs["types"] = types
    # The SDK's list method supports auto-pagination; we iterate that.
    yield from client.beta.sessions.events.list(session_id, **kwargs)


def stream_with_replay_safety(session_id: str) -> Iterator[Any]:
    """Yield live events, skipping anything already seen via list_events.

    Implementation follows the docs pattern verbatim:

    1. Open the stream first (so the API begins buffering events).
    2. List historical events; populate ``seen_ids``.
    3. Iterate the stream, ``yield`` ing only events whose IDs are new.

    Caller is responsible for breaking the loop (e.g. on
    ``session.status_idle`` or ``session.error``).
    """
    expect_kind(session_id, ResourceKind.SESSION)
    client = get_client()

    stream = client.beta.sessions.events.stream(session_id)
    emit(
        domain="cma.api.events",
        action="stream_open",
        extra={"session_id": session_id},
    )

    seen_ids: set[str] = set()
    # SDK note: list() returns the full data page. For very long sessions
    # this could OOM; but the use case is "tail this running session right
    # now," so the history is usually short.
    for hist in list_events(session_id):
        if hasattr(hist, "id"):
            seen_ids.add(hist.id)

    try:
        for event in stream:
            ev_id = getattr(event, "id", None)
            if ev_id is not None and ev_id in seen_ids:
                continue
            if ev_id is not None:
                seen_ids.add(ev_id)
            yield event
    finally:
        # Close the stream if the SDK supports it explicitly. Some SDKs use
        # context managers; this attribute path covers both.
        close = getattr(stream, "close", None)
        if callable(close):
            close()
        emit(
            domain="cma.api.events",
            action="stream_close",
            extra={"session_id": session_id, "seen_ids": len(seen_ids)},
        )
