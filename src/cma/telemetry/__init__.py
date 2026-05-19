"""Observability — JSONL telemetry + optional Prometheus push.

JSONL is the always-on local sink; one line per event. Mirrors the
``O:/Temp/hf-hook.jsonl`` pattern in the operator's primary project so that
log-tail workflows are interchangeable.

Prometheus emission is opt-in via the ``[prometheus]`` extra; off by default
to keep the core dependency footprint tight.
"""

from __future__ import annotations

from cma.telemetry.jsonl import TelemetryEmitter, emit, redact

__all__ = ["TelemetryEmitter", "emit", "redact"]
