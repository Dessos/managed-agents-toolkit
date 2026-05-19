"""``cma-local-executor`` MCP daemon and supporting code.

The executor runs locally on the operator's machine, exposed via Cloudflare
Tunnel as a remote MCP endpoint. Cloud agents connect to it and submit jobs
that the cloud is NOT allowed to run itself (anything touching the consumer's
proprietary code or data — implementation, parameters, raw inputs).

Architecture (per the operator-confirmed plan):

* Subprocess per job (strong isolation; child process can be OS-killed)
* Hybrid YAML spec + Python adapter (spec declares structure, adapter
  provides the callable)
* Both polling AND notifications as result-delivery paths (defensive)
* Static bearer auth (rotated via ``cma bridge rotate-token``)

This package is OPT-IN — installed via the ``[executor]`` extra:

.. code-block:: bash

    pip install claude-managed-agents[executor]

The :mod:`mcp` package is only imported lazily where needed so that the
core toolkit (config, doctor, agents, etc.) doesn't pull it in.
"""

from __future__ import annotations
