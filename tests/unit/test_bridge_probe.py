"""Tests for cma.executor.bridge_probe — deep MCP-side validation.

Tests come in two flavors:

* **Drift computation tests** — pure unit tests for ``_compute_drift``.
* **Stdio probe integration test** — spawns a real cma executor stdio
  subprocess (via job specs + adapter the test fixtures provide) and
  verifies probe_bridge returns the right tool list + no drift.

We don't write a real HTTP probe integration test (would need an actual
HTTP MCP server fixture); HTTP path is covered by unit tests that
monkeypatch httpx to return canned responses.
"""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from cma.executor.bridge_config import (
    BridgeConfig,
    BridgeEntry,
    HttpTransport,
    StdioTransport,
)
from cma.executor.bridge_probe import (
    EntryProbeResult,
    _compute_drift,
    probe_bridge,
)

# ---------------------------------------------------------------------------
# Drift computation
# ---------------------------------------------------------------------------


class TestComputeDrift:
    def test_expose_all_clean(self) -> None:
        entry = BridgeEntry(mcp="x", expose="all")
        absent, unlisted = _compute_drift(entry, ["a", "b", "c"])
        assert absent == []
        assert unlisted == []

    def test_expose_all_with_stale_deny(self) -> None:
        # 'a' is denied but doesn't exist on server → reported as absent.
        entry = BridgeEntry(mcp="x", expose="all", deny=["a", "real_tool"])
        absent, unlisted = _compute_drift(entry, ["real_tool", "b"])
        assert absent == ["a"]
        assert unlisted == []

    def test_expose_list_match(self) -> None:
        entry = BridgeEntry(mcp="x", expose=["a", "b"])
        absent, unlisted = _compute_drift(entry, ["a", "b"])
        assert absent == []
        assert unlisted == []

    def test_listed_but_absent(self) -> None:
        entry = BridgeEntry(mcp="x", expose=["a", "missing_tool"])
        absent, unlisted = _compute_drift(entry, ["a", "b"])
        assert absent == ["missing_tool"]
        assert unlisted == ["b"]

    def test_present_but_unlisted(self) -> None:
        entry = BridgeEntry(mcp="x", expose=["a"])
        absent, unlisted = _compute_drift(entry, ["a", "extra1", "extra2"])
        assert absent == []
        assert sorted(unlisted) == ["extra1", "extra2"]

    def test_present_but_unlisted_respects_deny(self) -> None:
        # Tool 'denied_tool' is announced AND explicitly denied → not flagged
        # as unlisted (operator already considered it).
        entry = BridgeEntry(mcp="x", expose=["a"], deny=["denied_tool"])
        absent, unlisted = _compute_drift(entry, ["a", "denied_tool", "really_extra"])
        assert absent == []
        assert unlisted == ["really_extra"]


# ---------------------------------------------------------------------------
# probe_bridge — entry-level dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_bridge_no_transport_entries() -> None:
    """Entries without transport are skipped with no_transport outcome."""
    config = BridgeConfig(
        bridge=[
            BridgeEntry(mcp="declared-only-a", expose="all"),
            BridgeEntry(mcp="declared-only-b", expose=["foo"]),
        ]
    )
    report = await probe_bridge(config)
    assert len(report.entries) == 2
    assert all(r.outcome == "no_transport" for r in report.entries)
    assert report.overall_ok  # no_transport doesn't fail


@pytest.mark.asyncio
async def test_probe_bridge_unreachable_command() -> None:
    """A bogus stdio command surfaces as 'unreachable'."""
    config = BridgeConfig(
        bridge=[
            BridgeEntry(
                mcp="bogus",
                expose="all",
                transport=StdioTransport(
                    type="stdio",
                    command="this-command-does-not-exist-anywhere-12345",
                ),
            ),
        ]
    )
    report = await probe_bridge(config)
    assert len(report.entries) == 1
    assert report.entries[0].outcome == "unreachable"
    assert not report.overall_ok


# ---------------------------------------------------------------------------
# Stdio integration — probe a real cma executor stdio subprocess
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path) -> Path:
    """Same fixture shape as test_executor_stdio: a job spec + adapter."""
    specs_dir = tmp_path / ".managed-agents" / "job_specs"
    specs_dir.mkdir(parents=True)
    (specs_dir / "noop.yaml").write_text(
        dedent(
            """
            spec_ref: noop
            job_type: test
            handler: noop_handler
            timeout_seconds: 30
            """
        ).strip(),
        encoding="utf-8",
    )
    adapters_dir = tmp_path / ".managed-agents" / "adapters"
    adapters_dir.mkdir(parents=True)
    (adapters_dir / "local_executor.py").write_text(
        dedent(
            """
            async def noop_handler(**kwargs):
                return {"ok": True}
            JOB_HANDLERS = {"noop_handler": noop_handler}
            """
        ),
        encoding="utf-8",
    )
    return tmp_path


_CMA_EXECUTOR_TOOLS = {
    "submit_job",
    "get_job_status",
    "get_job_result",
    "list_jobs",
    "cancel_job",
}


@pytest.mark.asyncio
async def test_probe_real_stdio_executor(tmp_path: Path) -> None:
    """Spawn cma executor stdio for real; verify probe lists its 5 tools."""
    workspace = _make_project(tmp_path)
    config = BridgeConfig(
        bridge=[
            BridgeEntry(
                mcp="cma-local-executor",
                expose=sorted(_CMA_EXECUTOR_TOOLS),  # explicit list
                transport=StdioTransport(
                    type="stdio",
                    command=sys.executable,
                    args=[
                        "-m",
                        "cma.cli",
                        "executor",
                        "stdio",
                        "--workspace-root",
                        str(workspace),
                        "--project-name",
                        "probe-test",
                    ],
                ),
            ),
        ]
    )
    report = await probe_bridge(config)
    assert len(report.entries) == 1
    result = report.entries[0]
    assert result.outcome == "ok", f"detail: {result.detail}"
    assert set(result.tools_announced) == _CMA_EXECUTOR_TOOLS
    # No drift since expose includes exactly the 5 tools.
    assert not result.has_drift
    assert report.overall_ok


@pytest.mark.asyncio
async def test_probe_real_stdio_executor_detects_listed_but_absent(
    tmp_path: Path,
) -> None:
    """Expose includes a tool the executor doesn't have — drift flagged."""
    workspace = _make_project(tmp_path)
    config = BridgeConfig(
        bridge=[
            BridgeEntry(
                mcp="cma-local-executor",
                expose=["submit_job", "this_tool_does_not_exist"],
                transport=StdioTransport(
                    type="stdio",
                    command=sys.executable,
                    args=[
                        "-m",
                        "cma.cli",
                        "executor",
                        "stdio",
                        "--workspace-root",
                        str(workspace),
                        "--project-name",
                        "probe-test",
                    ],
                ),
            ),
        ]
    )
    report = await probe_bridge(config)
    result = report.entries[0]
    assert result.outcome == "ok"
    assert result.listed_but_absent == ["this_tool_does_not_exist"]
    # The other 4 real executor tools are 'present_but_unlisted' here.
    assert set(result.present_but_unlisted) == _CMA_EXECUTOR_TOOLS - {"submit_job"}
    assert result.has_drift
    assert not report.overall_ok


# ---------------------------------------------------------------------------
# HTTP probe path — mocked httpx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_probe_lists_tools() -> None:
    """The HTTP probe path uses httpx; we mock it to return canned tools."""
    from cma.executor import bridge_probe as bp

    fake_init_response = type(
        "R",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"serverInfo": {"name": "fake"}},
            },
        },
    )()
    fake_tools_response = type(
        "R",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": [{"name": "alpha"}, {"name": "beta"}]},
            },
        },
    )()

    # AsyncClient.post returns different fake responses in sequence.
    post_responses = iter([fake_init_response, None, fake_tools_response])

    class FakeAsyncClient:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *a) -> None:
            pass

        async def post(self, url: str, json: dict) -> object:
            r = next(post_responses)
            if r is None:  # initialized notification — caller doesn't read it
                return type("Empty", (), {"raise_for_status": lambda self: None})()
            return r

    with patch.object(bp, "_list_tools_http", wraps=bp._list_tools_http):
        # Patch httpx.AsyncClient inside the module's runtime import.
        import httpx

        with patch.object(httpx, "AsyncClient", FakeAsyncClient):
            tools = await bp._list_tools_http(
                HttpTransport(type="http", url="https://fake.example.com/mcp")
            )
    assert tools == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# overall_ok property semantics
# ---------------------------------------------------------------------------


class TestOverallOk:
    def test_empty_report_ok(self) -> None:
        from cma.executor.bridge_probe import BridgeProbeReport

        assert BridgeProbeReport().overall_ok

    def test_all_no_transport_ok(self) -> None:
        from cma.executor.bridge_probe import BridgeProbeReport

        report = BridgeProbeReport(
            entries=[
                EntryProbeResult(mcp="a", outcome="no_transport"),
                EntryProbeResult(mcp="b", outcome="no_transport"),
            ]
        )
        assert report.overall_ok

    def test_any_unreachable_not_ok(self) -> None:
        from cma.executor.bridge_probe import BridgeProbeReport

        report = BridgeProbeReport(
            entries=[
                EntryProbeResult(mcp="a", outcome="ok"),
                EntryProbeResult(mcp="b", outcome="unreachable"),
            ]
        )
        assert not report.overall_ok

    def test_any_drift_not_ok(self) -> None:
        from cma.executor.bridge_probe import BridgeProbeReport

        report = BridgeProbeReport(
            entries=[
                EntryProbeResult(
                    mcp="a",
                    outcome="ok",
                    listed_but_absent=["missing_tool"],
                ),
            ]
        )
        assert not report.overall_ok
