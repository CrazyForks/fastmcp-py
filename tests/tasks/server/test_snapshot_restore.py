"""Tests for ``restore_task_snapshot`` — the worker-level Docket dependency
that restores the task-context snapshot into the ``_task_snapshot``
ContextVar before each task runs.

With the snapshot restored up front, sync helpers (``get_access_token``,
``get_http_request``, etc.) never need to hit Redis themselves.  These
tests exercise the restore path end-to-end (via in-memory Docket) and
the edge cases around non-fastmcp keys and failed restores.
"""

from __future__ import annotations

from unittest.mock import patch

from fastmcp_tasks.context import (
    TaskContextSnapshot,
    _recall_snapshot,
    get_task_context,
    restore_task_snapshot,
)
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    running_task_server,
    submit_task,
    wait_for_task,
)


async def test_snapshot_restored_before_user_code_runs():
    """A tool with no declared deps finds the snapshot already cached."""
    mcp = FastMCP("snapshot-restore-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def bare_tool() -> bool:
        info = get_task_context()
        assert info is not None
        return _recall_snapshot(info.task_id) is not None

    async with running_task_server(mcp):
        created = await submit_task(mcp, "bare_tool", {})
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result["structuredContent"] == {"result": True}


async def test_get_access_token_in_bg_task_without_context_dep():
    """Issue #3897 repro: get_access_token() works in a bg task that does
    not declare Context as a dependency."""
    mcp = FastMCP("access-token-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def bare_tool() -> str:
        token = get_access_token()
        return token.token if token else "no-token"

    test_token = AccessToken(
        token="jwt-3897",
        client_id="test-client",
        scopes=["read"],
        claims={"sub": "user-x"},
    )
    auth_context_var.set(AuthenticatedUser(test_token))

    async with running_task_server(mcp):
        created = await submit_task(mcp, "bare_tool", {})
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result["structuredContent"] == {"result": "jwt-3897"}


async def test_restore_failure_is_nonfatal():
    """If deserialization blows up, the task still runs to completion and
    the snapshot cache stays empty."""
    mcp = FastMCP("restore-failure-test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def bare_tool() -> bool:
        info = get_task_context()
        assert info is not None
        return _recall_snapshot(info.task_id) is not None

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated deserialization failure")

    async with running_task_server(mcp):
        with patch.object(TaskContextSnapshot, "from_json", boom):
            created = await submit_task(mcp, "bare_tool", {})
            final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result["structuredContent"] == {"result": False}


async def test_restore_skipped_for_non_fastmcp_task_keys():
    """The restore dep returns cleanly for keys it doesn't recognize and
    writes nothing to the snapshot cache."""
    # Direct calls bypass the worker, so Redis/Docket never gets involved
    # — any attempt to touch them would raise.
    await restore_task_snapshot(key="not-a-fastmcp-key")
    await restore_task_snapshot(key="weird:client-a:task-1:tool:my_tool")
    await restore_task_snapshot(key="")
