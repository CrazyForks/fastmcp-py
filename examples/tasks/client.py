"""
FastMCP Tasks Example Client (SEP-2663)

Demonstrates the two client task surfaces:

- Transparent: `client.call_tool(...)` drives the background task to completion
  under the hood and returns the tool's real result. The caller writes ordinary
  tool-call code and never sees that the server ran the call as a task.
- Explicit handle: `call_tool_task(...)` returns a `ToolTask` immediately, so the
  client can do other work and poll the task itself before collecting the result.

Usage:
    # Make sure environment is configured (source .envrc or use direnv)
    source .envrc

    # Transparent background task (default)
    python client.py --duration 10

    # Return-quickly handle, driven by the client
    python client.py handle --duration 5
"""

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from mcp_types import TextContent
from rich.console import Console

from fastmcp.client import Client
from fastmcp_tasks import call_tool_task

console = Console()
app = cyclopts.App(name="tasks-client", help="FastMCP Tasks Example Client")


def load_server():
    """Load the example server."""
    examples_dir = Path(__file__).parent.parent.parent
    if str(examples_dir) not in sys.path:
        sys.path.insert(0, str(examples_dir))

    import examples.tasks.server as server_module

    return server_module.mcp


@app.default
async def transparent(
    duration: Annotated[
        int,
        cyclopts.Parameter(help="Duration of computation in seconds (1-60)"),
    ] = 10,
):
    """Call the tool transparently: the client drives the task to completion."""
    if duration < 1 or duration > 60:
        console.print("[red]Error: Duration must be between 1 and 60 seconds[/red]")
        sys.exit(1)

    server = load_server()

    console.print(f"\n[bold]Calling slow_computation(duration={duration})[/bold]")
    console.print("Mode: [cyan]Transparent (server may run it as a task)[/cyan]\n")

    # mode="auto" negotiates the modern protocol, so the server may run the call
    # as a background task; the client resolves it transparently.
    async with Client(server, mode="auto") as client:
        result = await client.call_tool(
            "slow_computation",
            arguments={"duration": duration},
        )

        console.print("\n[bold]Result:[/bold]")
        assert isinstance(result.content[0], TextContent)
        console.print(f"  {result.content[0].text}")


@app.command
async def handle(
    duration: Annotated[
        int,
        cyclopts.Parameter(help="Duration of computation in seconds (1-60)"),
    ] = 5,
):
    """Use the explicit handle: return immediately, then drive the task."""
    if duration < 1 or duration > 60:
        console.print("[red]Error: Duration must be between 1 and 60 seconds[/red]")
        sys.exit(1)

    server = load_server()

    console.print(f"\n[bold]Calling slow_computation(duration={duration})[/bold]")
    console.print("Mode: [cyan]Explicit ToolTask handle[/cyan]\n")

    async with Client(server, mode="auto") as client:
        task = await call_tool_task(
            client,
            "slow_computation",
            arguments={"duration": duration},
        )

        console.print(f"Task started: [cyan]{task.task_id}[/cyan]\n")

        # Do other work while the task runs in the background.
        for i in range(3):
            await asyncio.sleep(0.5)
            status = await task.status()
            console.print(
                f"[dim]Client doing other work... ({i + 1}/3) "
                f"— task is {status.status}[/dim]"
            )

        console.print("\n[dim]Waiting for the final result...[/dim]")
        result = await task.result()

        console.print("\n[bold]Result:[/bold]")
        assert isinstance(result.content[0], TextContent)
        console.print(f"  {result.content[0].text}")


if __name__ == "__main__":
    app()
