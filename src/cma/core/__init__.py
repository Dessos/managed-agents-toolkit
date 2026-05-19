"""Pure-logic primitives: config loaders, budget arithmetic, pricing constants.

Nothing in this package may import :mod:`cma.api` or perform I/O — keeps the
core unit-testable without network access. Side-effectful code lives in
:mod:`cma.api`, :mod:`cma.telemetry`, and :mod:`cma.workflows`.
"""

from __future__ import annotations
