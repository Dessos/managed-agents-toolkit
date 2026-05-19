"""Thin SDK wrappers around the Anthropic Managed Agents beta endpoints.

Every wrapper:

* Adds the canonical beta headers (``managed-agents-2026-04-01`` and
  ``cache-diagnosis-2026-04-07``) via :func:`cma.api.client.get_client`.
* Emits a JSONL telemetry line before and after each call.
* Translates between toolkit-native dataclasses and the SDK's parameter types.
"""

from __future__ import annotations

from cma.api.client import get_client

__all__ = ["get_client"]
