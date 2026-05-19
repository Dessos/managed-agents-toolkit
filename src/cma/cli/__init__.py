"""Typer-based CLI for the ``cma`` command.

Subcommand domains:

* ``cma doctor`` — probe + diagnostics (local + remote)
* ``cma project`` — init + verify consumer-project configs (planned)
* ``cma agent`` — agent CRUD + sync against the local registry (planned)
* ``cma env`` — environment CRUD + probe (planned)
* ``cma session`` — session lifecycle wrapper (planned)
* ``cma audit`` — sanitized MCP-bridge call log report (planned)

Only ``doctor`` is implemented in Phase 1.0; the rest follow in Phase 1.x.
"""

from __future__ import annotations
