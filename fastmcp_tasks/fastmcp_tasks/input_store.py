"""In-task input store for SEP-2663 poll-based elicitation.

When a background task calls ``ctx.elicit()`` it has no live request to carry the
prompt. SEP-2663 handles this by polling: the worker parks an *input request*
here, the task's ``tasks/get`` status flips to ``input_required`` with the
outstanding requests, the client answers via ``tasks/update``, and the parked
worker resumes.

This is the reworked SEP-1686 elicitation module. The Redis request/response
mechanics — a per-request hash the poll surface reads and a per-key list the
worker blocks on with ``BLPOP`` — are preserved. What's gone is the *push
envelope*: the old code sent a ``notifications/tasks/status`` through the
distributed notification queue to wake the client. Under SEP-2663 the client
discovers the outstanding request by polling ``tasks/get``, so no push is needed.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import mcp_types
from redis.exceptions import RedisError

from fastmcp_tasks.context import get_task_context
from fastmcp_tasks.keys import task_redis_prefix

if TYPE_CHECKING:
    from docket import Docket

    from fastmcp.server.context import Context

logger = logging.getLogger(__name__)

# How long a parked input request (and any delivered response) lives before
# expiring. A task blocked on input holds a worker slot, so this doubles as the
# maximum time a worker waits for the client to answer.
INPUT_TTL_SECONDS = 3600


def _requests_key(docket: Docket, task_scope: str | None, task_id: str) -> str:
    """Redis hash of outstanding input requests, keyed by input key."""
    return docket.key(f"{task_redis_prefix(task_scope)}:{task_id}:input:requests")


def _response_key(
    docket: Docket, task_scope: str | None, task_id: str, input_key: str
) -> str:
    """Redis list the worker blocks on for a single input key's response."""
    return docket.key(
        f"{task_redis_prefix(task_scope)}:{task_id}:input:resp:{input_key}"
    )


def _elicitation_input_request(message: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Build the SEP-2663 ``InputRequest`` for an elicitation (an ElicitRequest)."""
    return {
        "method": "elicitation/create",
        "params": {"message": message, "requestedSchema": schema},
    }


async def elicit_in_task(
    context: Context, message: str, schema: dict[str, Any]
) -> mcp_types.ElicitResult:
    """Park an elicitation request and block until the client answers it.

    Installed as core's in-task elicitation handler by ``TasksExtension``. Parks
    an input request keyed by the task's own id (one outstanding elicitation per
    task at a time — the polling model is inherently sequential), flips the
    task's polled status to ``input_required``, and blocks on the response list.
    Returns the client's ``ElicitResult``; on timeout or a missing task context,
    returns a ``cancel`` action so the worker never hangs indefinitely.
    """
    task_context = get_task_context()
    if task_context is None:
        logger.warning("elicit_in_task called outside a task worker; cancelling")
        return mcp_types.ElicitResult(action="cancel", content=None)

    docket = context.fastmcp._docket
    if docket is None:
        from fastmcp_tasks.dependencies import _current_docket

        docket = _current_docket.get()
    if docket is None:
        return mcp_types.ElicitResult(action="cancel", content=None)

    task_scope = task_context.task_scope
    task_id = task_context.task_id
    # One elicitation outstanding per task: key the request by the task id so the
    # inputRequests map surfaced by tasks/get is stable and answerable.
    input_key = task_id

    requests_key = _requests_key(docket, task_scope, task_id)
    response_key = _response_key(docket, task_scope, task_id, input_key)
    request_payload = _elicitation_input_request(message, schema)

    async with docket.redis() as redis:
        await redis.hset(requests_key, input_key, json.dumps(request_payload))
        await redis.expire(requests_key, INPUT_TTL_SECONDS)

    try:
        async with docket.redis() as redis:
            result = await redis.blpop([response_key], timeout=INPUT_TTL_SECONDS)
    except (RedisError, OSError) as exc:
        logger.warning("BLPOP failed for task %s input; cancelling: %s", task_id, exc)
        result = None

    async with docket.redis() as redis:
        await redis.hdel(requests_key, input_key)
        await redis.delete(response_key)

    if not result:
        return mcp_types.ElicitResult(action="cancel", content=None)

    _key, raw = result
    response = json.loads(raw)
    return mcp_types.ElicitResult(
        action=response.get("action", "accept"),
        content=response.get("content"),
    )


async def read_outstanding_inputs(
    docket: Docket, task_scope: str | None, task_id: str
) -> dict[str, Any]:
    """Return the task's outstanding input requests, keyed by input key.

    Empty when the task is not waiting on input. Consumed by ``tasks/get`` to
    build the ``input_required`` status and its ``inputRequests`` snapshot.
    """
    requests_key = _requests_key(docket, task_scope, task_id)
    async with docket.redis() as redis:
        raw = await redis.hgetall(requests_key)
    outstanding: dict[str, Any] = {}
    for key, value in raw.items():
        key_str = key.decode() if isinstance(key, bytes) else key
        value_str = value.decode() if isinstance(value, bytes) else value
        try:
            outstanding[key_str] = json.loads(value_str)
        except json.JSONDecodeError:
            continue
    return outstanding


async def deliver_input_responses(
    docket: Docket,
    task_scope: str | None,
    task_id: str,
    responses: dict[str, Any],
) -> None:
    """Deliver ``tasks/update`` responses to the parked worker(s).

    For each response whose key names an outstanding request, pushes the
    response onto that key's list (waking the worker's ``BLPOP``) and removes the
    request. Responses for unknown or already-satisfied keys are ignored, as the
    spec requires.
    """
    requests_key = _requests_key(docket, task_scope, task_id)
    async with docket.redis() as redis:
        for input_key, response in responses.items():
            outstanding = await redis.hget(requests_key, input_key)
            if outstanding is None:
                continue
            response_key = _response_key(docket, task_scope, task_id, input_key)
            await redis.rpush(response_key, json.dumps(response))
            await redis.expire(response_key, INPUT_TTL_SECONDS)
            await redis.hdel(requests_key, input_key)
