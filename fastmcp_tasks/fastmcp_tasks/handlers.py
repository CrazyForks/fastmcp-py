"""SEP-2663 task query/management handlers: tasks/get, tasks/update, tasks/cancel.

Adapted from the SEP-1686 ``requests.py``. The three CRUD-ish handlers survive,
reshaped to the new wire:

- ``tasks/get`` merges the old ``tasks/get`` and ``tasks/result``: the finished
  result is *inlined* into the response for a completed task, a JSON-RPC-shaped
  ``error`` for a failed one, and the outstanding ``inputRequests`` for a task
  waiting on input.
- ``tasks/update`` is new: it delivers ``inputResponses`` to the in-task input
  store, resuming a parked worker.
- ``tasks/cancel`` returns an empty ack (SEP-2663) instead of a task snapshot.
- ``tasks/list`` and ``tasks/result`` are gone (removed by SEP-2663).

The auth-scoped compound key is the authorization boundary: a request resolves a
task only under its own scope's Redis prefix, so a scope mismatch is
indistinguishable from a missing task (both raise -32602 "Task not found"),
which avoids leaking task existence across callers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal

import mcp_types
from docket.execution import ExecutionState
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from fastmcp.exceptions import NotFoundError
from fastmcp.tools.base import InputRequiredToolResult, Tool
from fastmcp.utilities.tasks import DEFAULT_POLL_INTERVAL_MS
from fastmcp.utilities.versions import VersionSpec
from fastmcp_tasks.context import get_task_scope
from fastmcp_tasks.input_store import deliver_input_responses, read_outstanding_inputs
from fastmcp_tasks.keys import parse_task_key, task_redis_prefix
from fastmcp_tasks.models import (
    CancelTaskResult,
    GetTaskResult,
    UpdateTaskResult,
)

if TYPE_CHECKING:
    from docket import Docket

    from fastmcp.server.server import FastMCP

# Docket execution state -> SEP-2663 task status. `input_required` is not a
# Docket state; it is derived from the in-task input store (see tasks_get).
DOCKET_TO_MCP_STATE: dict[ExecutionState, str] = {
    ExecutionState.SCHEDULED: "working",
    ExecutionState.QUEUED: "working",
    ExecutionState.RUNNING: "working",
    ExecutionState.COMPLETED: "completed",
    ExecutionState.FAILED: "failed",
    ExecutionState.CANCELLED: "cancelled",
}

_WORKING_STATES = frozenset(
    {ExecutionState.SCHEDULED, ExecutionState.QUEUED, ExecutionState.RUNNING}
)


def _task_not_found(task_id: str) -> MCPError:
    """The single "not found" error for missing, expired, or cross-scope ids.

    Uses one message for all three so a caller cannot probe another scope's task
    ids by distinguishing "not yours" from "does not exist".
    """
    return MCPError(code=INVALID_PARAMS, message=f"Task {task_id} not found")


def _normalize_iso_timestamp(stored: str | None) -> str:
    """Return an ISO 8601 timestamp for createdAt, tolerating a missing value."""
    if stored:
        try:
            return datetime.fromisoformat(stored.replace("Z", "+00:00")).isoformat()
        except (ValueError, AttributeError):
            pass
    return datetime.now(timezone.utc).isoformat()


def _parse_key_version(key_suffix: str) -> tuple[str, str | None]:
    """Split a component key suffix into (name, version) on the last ``@``."""
    if "@" not in key_suffix:
        return key_suffix, None
    name, version = key_suffix.rsplit("@", 1)
    return name, version if version else None


def _ttl_ms(docket: Docket) -> int:
    """The task TTL in milliseconds, from Docket's execution TTL (server-set)."""
    return int(docket.execution_ttl.total_seconds() * 1000)


async def _lookup_task(
    docket: Docket, task_scope: str | None, task_id: str
) -> tuple[Any, str, str | None, int]:
    """Resolve a task's execution and stored metadata within the caller's scope.

    Returns ``(execution, task_key, created_at, poll_interval_ms)``. Raises the
    shared "not found" error when the scope-prefixed metadata is absent or the
    execution has expired.
    """
    prefix = task_redis_prefix(task_scope)
    meta_key = docket.key(f"{prefix}:{task_id}")
    created_at_key = docket.key(f"{prefix}:{task_id}:created_at")
    poll_key = docket.key(f"{prefix}:{task_id}:poll_interval")

    async with docket.redis() as redis:
        # Docket's Redis client mirrors redis-py's variadic ``mget(*keys)`` at
        # runtime; its type stub declares a single ``Sequence`` arg, so the
        # positional form is correct but needs a targeted ignore.
        values = await redis.mget(meta_key, created_at_key, poll_key)  # ty: ignore[too-many-positional-arguments]
    task_key_bytes, created_at_bytes, poll_bytes = values

    task_key = task_key_bytes.decode("utf-8") if task_key_bytes else None
    if not task_key:
        raise _task_not_found(task_id)

    execution = await docket.get_execution(task_key)
    if not execution:
        raise _task_not_found(task_id)

    created_at = created_at_bytes.decode("utf-8") if created_at_bytes else None

    try:
        poll_interval_ms = (
            int(poll_bytes.decode("utf-8")) if poll_bytes else DEFAULT_POLL_INTERVAL_MS
        )
    except (ValueError, UnicodeDecodeError):
        poll_interval_ms = DEFAULT_POLL_INTERVAL_MS

    return execution, task_key, created_at, poll_interval_ms


async def _resolve_tool(server: FastMCP, task_key: str) -> Tool:
    """Resolve the Tool a task ran, from its compound key (tools-only surface)."""
    component_key = parse_task_key(task_key)["component_identifier"]
    if not component_key.startswith("tool:"):
        raise MCPError(
            code=mcp_types.INTERNAL_ERROR,
            message=f"Task component is not a tool: {component_key}",
        )
    name, version_str = _parse_key_version(component_key[len("tool:") :])
    version = VersionSpec(eq=version_str) if version_str else None
    try:
        tool = await server.get_tool(name, version)
    except NotFoundError:
        tool = None
    if tool is None:
        raise MCPError(
            code=mcp_types.INTERNAL_ERROR,
            message=f"Component not found for task: {component_key}",
        )
    return tool


def _inline_result(tool: Tool, raw_value: Any) -> dict[str, Any]:
    """Convert a completed task's raw return into an inlined CallToolResult dict.

    A guard tool that returned an ``InputRequiredResult`` from inside a task is
    rejected: multi-round-trip guards need a live request to answer the prompt
    and cannot complete as a task.
    """
    if isinstance(raw_value, mcp_types.InputRequiredResult | InputRequiredToolResult):
        raise MCPError(
            code=mcp_types.INTERNAL_ERROR,
            message=(
                f"Tool {tool.name!r} requested input while running as a background "
                "task. Input-required (multi-round-trip) tools need a live request "
                "to answer the prompt and cannot run as tasks."
            ),
        )
    mcp_result = tool.convert_result(raw_value).to_mcp_result()
    if isinstance(mcp_result, mcp_types.CallToolResult):
        call_tool_result = mcp_result
    elif isinstance(mcp_result, tuple):
        content, structured_content = mcp_result
        call_tool_result = mcp_types.CallToolResult(
            content=content, structured_content=structured_content
        )
    else:
        call_tool_result = mcp_types.CallToolResult(content=mcp_result)
    return call_tool_result.model_dump(by_alias=True, mode="json", exclude_none=True)


async def tasks_get(server: FastMCP, task_id: str) -> GetTaskResult:
    """Handle ``tasks/get``: the detailed task with its result/error/inputs inlined."""
    docket = server._docket
    if docket is None:
        raise _task_not_found(task_id)

    task_scope = get_task_scope()
    execution, task_key, created_at, poll_interval_ms = await _lookup_task(
        docket, task_scope, task_id
    )
    await execution.sync()

    created_at_iso = _normalize_iso_timestamp(created_at)
    now_iso = datetime.now(timezone.utc).isoformat()
    ttl_ms = _ttl_ms(docket)

    def build(
        status: Literal[
            "working", "input_required", "completed", "failed", "cancelled"
        ],
        **payload: Any,
    ) -> GetTaskResult:
        return GetTaskResult(
            task_id=task_id,
            status=status,
            created_at=created_at_iso,
            last_updated_at=now_iso,
            ttl_ms=ttl_ms,
            poll_interval_ms=poll_interval_ms,
            **payload,
        )

    # An outstanding input request outranks the Docket "running" state: the task
    # is parked in the worker waiting for tasks/update, so it is input_required.
    if execution.state in _WORKING_STATES:
        outstanding = await read_outstanding_inputs(docket, task_scope, task_id)
        if outstanding:
            return build("input_required", input_requests=outstanding)

    if execution.state == ExecutionState.COMPLETED:
        raw_value = await execution.get_result(timeout=timedelta(seconds=0))
        tool = await _resolve_tool(server, task_key)
        return build("completed", result=_inline_result(tool, raw_value))

    if execution.state == ExecutionState.FAILED:
        message = "Task failed"
        try:
            await execution.get_result(timeout=timedelta(seconds=0))
        # On a FAILED execution, get_result re-raises the exception the task
        # itself raised — an arbitrary user-defined type, so no narrower catch
        # exists. Its message becomes the task's error payload.
        except Exception as error:
            message = str(error)
        return build(
            "failed",
            status_message=message,
            error={"code": mcp_types.INTERNAL_ERROR, "message": message},
        )

    if execution.state == ExecutionState.CANCELLED:
        return build("cancelled")

    status_message = None
    if execution.progress and execution.progress.message:
        status_message = execution.progress.message
    return build("working", status_message=status_message)


async def tasks_update(
    server: FastMCP, task_id: str, input_responses: dict[str, Any]
) -> UpdateTaskResult:
    """Handle ``tasks/update``: deliver input responses to the parked worker."""
    docket = server._docket
    if docket is None:
        raise _task_not_found(task_id)

    task_scope = get_task_scope()
    # Resolve within scope so a cross-scope update is a "not found", not a no-op.
    await _lookup_task(docket, task_scope, task_id)
    await deliver_input_responses(docket, task_scope, task_id, input_responses)
    return UpdateTaskResult()


async def tasks_cancel(server: FastMCP, task_id: str) -> CancelTaskResult:
    """Handle ``tasks/cancel``: cooperatively cancel the task, empty ack."""
    docket = server._docket
    if docket is None:
        raise _task_not_found(task_id)

    task_scope = get_task_scope()
    execution, _task_key, _created_at, _poll = await _lookup_task(
        docket, task_scope, task_id
    )
    await docket.cancel(execution.key)
    return CancelTaskResult()
