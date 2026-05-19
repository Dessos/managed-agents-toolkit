"""Tests for cma.cli.audit — audit-log reader CLI."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma.cli.audit import (
    _parse_ts,
    _summarize_args,
    filter_entries,
    parse_since,
)
from cma.cli.audit import app as audit_app
from cma.executor.audit import audit_log_path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    *,
    ts: str | None = None,
    tool: str = "submit_job",
    client_id: str = "abcd1234",
    job_id: str | None = "job_x",
    result_status: str | None = "completed",
    args: dict | None = None,
    error: str | None = None,
    elapsed_ms: float | None = 12.5,
) -> dict:
    """Build a synthetic audit entry."""
    if ts is None:
        ts = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    entry: dict = {
        "ts": ts,
        "project": "test",
        "domain": "cma.executor.audit",
        "action": tool,
        "tool": tool,
        "client_id": client_id,
    }
    if job_id is not None:
        entry["job_id"] = job_id
    if result_status is not None:
        entry["result_status"] = result_status
    if args is not None:
        entry["args"] = args
    if error is not None:
        entry["error"] = error
    if elapsed_ms is not None:
        entry["elapsed_ms"] = elapsed_ms
    return entry


def _write_audit(workspace_root: Path, entries: list[dict]) -> Path:
    """Materialise an audit JSONL at the canonical location."""
    path = audit_log_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    return path


# ---------------------------------------------------------------------------
# parse_since
# ---------------------------------------------------------------------------


class TestParseSince:
    def test_hours(self) -> None:
        before = datetime.now(UTC)
        parsed = parse_since("24h")
        delta = before - parsed
        assert timedelta(hours=23, minutes=59) < delta < timedelta(hours=24, minutes=1)

    def test_days(self) -> None:
        before = datetime.now(UTC)
        parsed = parse_since("7d")
        delta = before - parsed
        assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)

    def test_minutes(self) -> None:
        parsed = parse_since("30m")
        delta = datetime.now(UTC) - parsed
        assert timedelta(minutes=29) < delta < timedelta(minutes=31)

    def test_weeks(self) -> None:
        parsed = parse_since("1w")
        delta = datetime.now(UTC) - parsed
        assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)

    def test_seconds(self) -> None:
        parsed = parse_since("120s")
        delta = datetime.now(UTC) - parsed
        assert timedelta(seconds=119) < delta < timedelta(seconds=121)

    def test_iso_date(self) -> None:
        parsed = parse_since("2026-05-18")
        assert parsed == datetime(2026, 5, 18, tzinfo=UTC)

    def test_iso_datetime_z(self) -> None:
        parsed = parse_since("2026-05-18T03:14:22Z")
        assert parsed == datetime(2026, 5, 18, 3, 14, 22, tzinfo=UTC)

    def test_iso_datetime_naive_treated_as_utc(self) -> None:
        parsed = parse_since("2026-05-18T03:14:22")
        assert parsed == datetime(2026, 5, 18, 3, 14, 22, tzinfo=UTC)

    def test_all_returns_epoch_ish(self) -> None:
        parsed = parse_since("all")
        assert parsed.year == 1970

    def test_garbage_raises(self) -> None:
        with pytest.raises(ValueError, match="duration"):
            parse_since("yesterday")

    def test_case_insensitive_unit(self) -> None:
        # ``24H`` should be the same as ``24h``.
        parsed_lower = parse_since("24h")
        parsed_upper = parse_since("24H")
        # Within 1s of each other (call latency).
        assert abs((parsed_lower - parsed_upper).total_seconds()) < 1


# ---------------------------------------------------------------------------
# filter_entries
# ---------------------------------------------------------------------------


class TestFilterEntries:
    def test_since_filters_old_entries(self) -> None:
        now = datetime.now(UTC)
        old = _entry(ts=(now - timedelta(days=2)).isoformat().replace("+00:00", "Z"))
        recent = _entry(ts=(now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"))
        out = filter_entries([old, recent], since=now - timedelta(days=1))
        assert out == [recent]

    def test_tool_filter(self) -> None:
        a = _entry(tool="submit_job")
        b = _entry(tool="get_job_status")
        out = filter_entries([a, b], tool="submit_job")
        assert out == [a]

    def test_client_id_filter(self) -> None:
        a = _entry(client_id="aaa11111")
        b = _entry(client_id="bbb22222")
        out = filter_entries([a, b], client_id="aaa11111")
        assert out == [a]

    def test_error_only(self) -> None:
        clean = _entry(error=None)
        bad = _entry(error="boom")
        out = filter_entries([clean, bad], error_only=True)
        assert out == [bad]

    def test_filters_compose(self) -> None:
        a = _entry(tool="submit_job", client_id="aaa11111", error=None)
        b = _entry(tool="submit_job", client_id="bbb22222", error="x")
        c = _entry(tool="get_job_status", client_id="aaa11111", error="y")
        out = filter_entries([a, b, c], tool="submit_job", error_only=True)
        assert out == [b]

    def test_missing_ts_excluded_when_since_set(self) -> None:
        # Defensive: an entry with no ts can never satisfy a since filter.
        broken = _entry()
        broken.pop("ts")
        out = filter_entries([broken], since=datetime.now(UTC) - timedelta(days=365))
        assert out == []

    def test_unparseable_ts_excluded(self) -> None:
        broken = _entry(ts="not-a-timestamp")
        out = filter_entries([broken], since=datetime.now(UTC) - timedelta(days=365))
        assert out == []


# ---------------------------------------------------------------------------
# _parse_ts + _summarize_args
# ---------------------------------------------------------------------------


class TestParseTs:
    def test_z_suffix(self) -> None:
        parsed = _parse_ts({"ts": "2026-05-18T03:14:22Z"})
        assert parsed == datetime(2026, 5, 18, 3, 14, 22, tzinfo=UTC)

    def test_naive_treated_as_utc(self) -> None:
        parsed = _parse_ts({"ts": "2026-05-18T03:14:22"})
        assert parsed == datetime(2026, 5, 18, 3, 14, 22, tzinfo=UTC)

    def test_missing_returns_none(self) -> None:
        assert _parse_ts({}) is None

    def test_non_string_returns_none(self) -> None:
        assert _parse_ts({"ts": 12345}) is None

    def test_garbage_returns_none(self) -> None:
        assert _parse_ts({"ts": "not-a-date"}) is None


class TestSummarizeArgs:
    def test_empty(self) -> None:
        assert _summarize_args(None) == ""
        assert _summarize_args({}) == ""

    def test_short_dict(self) -> None:
        assert _summarize_args({"x": 1}) == "x=1"

    def test_long_value_truncated(self) -> None:
        out = _summarize_args({"x": "a" * 100})
        assert "..." in out

    def test_overall_length_capped(self) -> None:
        out = _summarize_args({f"k{i}": "v" * 10 for i in range(20)}, max_len=40)
        assert len(out) <= 40


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestCli:
    def test_missing_log_friendly_message(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(audit_app, ["--workspace-root", str(tmp_path)])
        assert result.exit_code == 0
        assert "No audit log" in result.output

    def test_empty_log_friendly_message(self, tmp_path: Path) -> None:
        _write_audit(tmp_path, [])
        runner = CliRunner()
        result = runner.invoke(audit_app, ["--workspace-root", str(tmp_path)])
        assert result.exit_code == 0
        assert "empty" in result.output.lower()

    def test_table_render(self, tmp_path: Path) -> None:
        _write_audit(tmp_path, [_entry(tool="submit_job")])
        runner = CliRunner()
        result = runner.invoke(audit_app, ["--workspace-root", str(tmp_path), "--since", "all"])
        assert result.exit_code == 0
        assert "submit_job" in result.output

    def test_tool_filter_zero_matches(self, tmp_path: Path) -> None:
        _write_audit(tmp_path, [_entry(tool="submit_job")])
        runner = CliRunner()
        result = runner.invoke(
            audit_app,
            ["--workspace-root", str(tmp_path), "--tool", "cancel_job", "--since", "all"],
        )
        assert result.exit_code == 0
        assert "No audit entries matched" in result.output

    def test_error_only(self, tmp_path: Path) -> None:
        _write_audit(tmp_path, [
            _entry(tool="submit_job"),
            _entry(tool="submit_job", error="boom"),
        ])
        runner = CliRunner()
        result = runner.invoke(
            audit_app,
            ["--workspace-root", str(tmp_path), "--error-only", "--since", "all"],
        )
        assert result.exit_code == 0
        assert "boom" in result.output

    def test_json_mode_emits_jsonl(self, tmp_path: Path) -> None:
        e = _entry(tool="submit_job")
        _write_audit(tmp_path, [e])
        runner = CliRunner()
        result = runner.invoke(
            audit_app,
            ["--workspace-root", str(tmp_path), "--since", "all", "--json"],
        )
        assert result.exit_code == 0
        # One JSON object per line, parseable.
        non_empty = [line for line in result.output.splitlines() if line.strip().startswith("{")]
        assert non_empty
        parsed = json.loads(non_empty[0])
        assert parsed["tool"] == "submit_job"

    def test_bad_since_exits_two(self, tmp_path: Path) -> None:
        _write_audit(tmp_path, [_entry()])
        runner = CliRunner()
        result = runner.invoke(audit_app, ["--workspace-root", str(tmp_path), "--since", "yesterday"])
        assert result.exit_code == 2

    def test_malformed_line_tolerated(self, tmp_path: Path) -> None:
        # Write one good line + one garbage line; reader should keep going.
        path = audit_log_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(_entry(tool="submit_job")) + "\n")
            fh.write("not-json-at-all\n")
        runner = CliRunner()
        result = runner.invoke(audit_app, ["--workspace-root", str(tmp_path), "--since", "all"])
        assert result.exit_code == 0
        assert "submit_job" in result.output
        assert "malformed" in result.output.lower() or "skipped" in result.output.lower()

    def test_since_filters_old_entries_via_cli(self, tmp_path: Path) -> None:
        # Old entry should be excluded by default 24h window.
        old_ts = (datetime.now(UTC) - timedelta(days=10)).isoformat().replace("+00:00", "Z")
        _write_audit(tmp_path, [_entry(ts=old_ts, tool="submit_job")])
        runner = CliRunner()
        result = runner.invoke(audit_app, ["--workspace-root", str(tmp_path)])
        assert result.exit_code == 0
        assert "No audit entries matched" in result.output
