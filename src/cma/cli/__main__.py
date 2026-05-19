"""CLI entry point for ``cma``.

Wires Typer subcommand groups. Heavy logic lives in per-subcommand modules.

Run directly:

.. code-block:: bash

    cma doctor --probe-beta
    cma --version
"""

from __future__ import annotations

import sys

import typer

from cma import __version__
from cma.cli import agent as agent_cmd
from cma.cli import audit as audit_cmd
from cma.cli import bridge as bridge_cmd
from cma.cli import doctor as doctor_cmd
from cma.cli import executor as executor_cmd
from cma.cli import project as project_cmd
from cma.cli import webhook as webhook_cmd

app = typer.Typer(
    name="cma",
    help="Claude Managed Agents — multi-project toolkit.",
    no_args_is_help=True,
    add_completion=False,
)

# Register subcommands. Each subcommand module defines its own Typer instance
# and we mount it as a group.
app.add_typer(doctor_cmd.app, name="doctor")
app.add_typer(project_cmd.app, name="project")
app.add_typer(executor_cmd.app, name="executor")
app.add_typer(bridge_cmd.app, name="bridge")
app.add_typer(agent_cmd.app, name="agent")
app.add_typer(audit_cmd.app, name="audit")
app.add_typer(webhook_cmd.app, name="webhook")


@app.callback(invoke_without_command=False)
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show CMA version and exit.",
        is_eager=True,
    ),
) -> None:
    """Claude Managed Agents — multi-project toolkit."""
    if version:
        typer.echo(f"cma {__version__}")
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    app()
