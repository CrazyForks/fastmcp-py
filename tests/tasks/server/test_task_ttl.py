"""TTL handling for SEP-2663 tasks.

Servers report `ttlMs` in the create result and in every `tasks/get` response —
while the task is working and after it completes — using Docket's default
execution TTL (900000 ms) when none is configured.
"""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    get_task,
    running_task_server,
    submit_task,
    wait_for_task,
)

# Docket's default execution_ttl is 900 seconds.
DEFAULT_TTL_MS = 900000


def _ttl_server() -> FastMCP:
    mcp = FastMCP("keepalive-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def quick_task(value: int) -> int:
        return value * 2

    @mcp.tool(task=True)
    async def slow_task() -> str:
        # Never completes during the test; the test only checks status/TTL while
        # the task is still working, so a suspended coroutine is enough.
        await asyncio.Event().wait()
        return "done"

    return mcp


async def test_ttl_returned_while_working():
    """ttlMs is present in the create result and in tasks/get while working."""
    mcp = _ttl_server()
    async with running_task_server(mcp):
        created = await submit_task(mcp, "slow_task", {})
        assert created.ttl_ms == DEFAULT_TTL_MS
        got = await get_task(mcp, created.task_id)
        assert got.status == "working"
        assert got.ttl_ms == DEFAULT_TTL_MS


async def test_ttl_returned_after_completion():
    """ttlMs is present in tasks/get after the task completes."""
    mcp = _ttl_server()
    async with running_task_server(mcp):
        created = await submit_task(mcp, "quick_task", {"value": 5})
        final = await wait_for_task(mcp, created.task_id)
        assert final.status == "completed"
        assert final.ttl_ms == DEFAULT_TTL_MS


async def test_default_ttl_when_unspecified():
    """The server applies Docket's default TTL when none is configured."""
    mcp = _ttl_server()
    async with running_task_server(mcp):
        created = await submit_task(mcp, "quick_task", {"value": 3})
        assert created.ttl_ms == DEFAULT_TTL_MS
        got = await get_task(mcp, created.task_id)
        assert got.ttl_ms == DEFAULT_TTL_MS
