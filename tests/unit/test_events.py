"""Tests for cma.api.events.

The interesting code path is ``stream_with_replay_safety``: it must:

1. Open the stream BEFORE listing history (so events buffered during the
   list call aren't missed).
2. Skip events whose IDs appear in the history list (would otherwise be
   processed twice if the stream replays them).
3. Yield new events with stable IDs added to the seen set.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cma.api.events import (
    iter_events,
    list_events,
    send_events,
    stream_with_replay_safety,
)


def _make_event(event_id: str, type_: str = "agent.message") -> MagicMock:
    """Build a minimal mock event object with .id and .type."""
    e = MagicMock()
    e.id = event_id
    e.type = type_
    return e


# ---------------------------------------------------------------------------
# send_events validation
# ---------------------------------------------------------------------------


class TestSendEvents:
    def test_rejects_empty_list(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            send_events("sesn_01TEST", events=[])

    def test_rejects_wrong_session_prefix(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            send_events("envrn_NOT_A_SESSION", events=[{"type": "user.message"}])

    def test_calls_sdk_with_events(self) -> None:
        client = MagicMock()
        events_payload = [
            {
                "type": "user.message",
                "content": [{"type": "text", "text": "hi"}],
            },
        ]
        with patch("cma.api.events.get_client", return_value=client):
            send_events("sesn_01TEST", events=events_payload)
        client.beta.sessions.events.send.assert_called_once_with(
            "sesn_01TEST", events=events_payload
        )


# ---------------------------------------------------------------------------
# list_events / iter_events
# ---------------------------------------------------------------------------


class TestListEvents:
    def test_returns_data_list(self) -> None:
        client = MagicMock()
        page = MagicMock()
        page.data = [_make_event("sevt_01"), _make_event("sevt_02")]
        client.beta.sessions.events.list.return_value = page
        with patch("cma.api.events.get_client", return_value=client):
            events = list_events("sesn_01TEST")
        assert len(events) == 2
        assert events[0].id == "sevt_01"

    def test_handles_empty_page(self) -> None:
        client = MagicMock()
        page = MagicMock()
        page.data = []
        client.beta.sessions.events.list.return_value = page
        with patch("cma.api.events.get_client", return_value=client):
            events = list_events("sesn_01TEST")
        assert events == []

    def test_types_filter_passes_through(self) -> None:
        client = MagicMock()
        page = MagicMock()
        page.data = []
        client.beta.sessions.events.list.return_value = page
        with patch("cma.api.events.get_client", return_value=client):
            list_events("sesn_01TEST", types=["agent.tool_use"])
        client.beta.sessions.events.list.assert_called_once_with(
            "sesn_01TEST", types=["agent.tool_use"]
        )

    def test_rejects_wrong_prefix(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            list_events("envrn_NOT_A_SESSION")


class TestIterEvents:
    def test_yields_from_sdk(self) -> None:
        client = MagicMock()
        client.beta.sessions.events.list.return_value = iter(
            [_make_event("sevt_01"), _make_event("sevt_02")]
        )
        with patch("cma.api.events.get_client", return_value=client):
            collected = list(iter_events("sesn_01TEST"))
        assert [e.id for e in collected] == ["sevt_01", "sevt_02"]


# ---------------------------------------------------------------------------
# stream_with_replay_safety — the interesting one
# ---------------------------------------------------------------------------


class TestStreamReplaySafety:
    def test_dedupes_against_history(self) -> None:
        """Events seen in the list call must NOT be re-yielded by the stream."""
        client = MagicMock()
        # History returns events 01, 02.
        history_page = MagicMock()
        history_page.data = [_make_event("sevt_01"), _make_event("sevt_02")]
        client.beta.sessions.events.list.return_value = history_page
        # Stream replays 01, 02, 03 — first two should be dropped.
        client.beta.sessions.events.stream.return_value = iter(
            [_make_event("sevt_01"), _make_event("sevt_02"), _make_event("sevt_03")]
        )

        with patch("cma.api.events.get_client", return_value=client):
            collected = list(stream_with_replay_safety("sesn_01TEST"))

        assert [e.id for e in collected] == ["sevt_03"]

    def test_yields_all_when_history_empty(self) -> None:
        client = MagicMock()
        history_page = MagicMock()
        history_page.data = []
        client.beta.sessions.events.list.return_value = history_page
        client.beta.sessions.events.stream.return_value = iter(
            [_make_event("sevt_a"), _make_event("sevt_b")]
        )

        with patch("cma.api.events.get_client", return_value=client):
            collected = list(stream_with_replay_safety("sesn_01TEST"))

        assert [e.id for e in collected] == ["sevt_a", "sevt_b"]

    def test_dedupes_within_stream(self) -> None:
        """If the stream itself re-yields, the second yield is dropped."""
        client = MagicMock()
        history_page = MagicMock()
        history_page.data = []
        client.beta.sessions.events.list.return_value = history_page
        # Same event ID appears twice in the stream — second should be skipped.
        client.beta.sessions.events.stream.return_value = iter(
            [_make_event("sevt_a"), _make_event("sevt_a"), _make_event("sevt_b")]
        )

        with patch("cma.api.events.get_client", return_value=client):
            collected = list(stream_with_replay_safety("sesn_01TEST"))

        assert [e.id for e in collected] == ["sevt_a", "sevt_b"]

    def test_closes_stream_on_exhaustion(self) -> None:
        client = MagicMock()
        history_page = MagicMock()
        history_page.data = []
        client.beta.sessions.events.list.return_value = history_page

        stream_mock = MagicMock()
        stream_mock.__iter__ = lambda self: iter([_make_event("sevt_a")])
        client.beta.sessions.events.stream.return_value = stream_mock

        with patch("cma.api.events.get_client", return_value=client):
            list(stream_with_replay_safety("sesn_01TEST"))

        stream_mock.close.assert_called_once()

    def test_rejects_wrong_prefix(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            list(stream_with_replay_safety("envrn_NOT_A_SESSION"))
