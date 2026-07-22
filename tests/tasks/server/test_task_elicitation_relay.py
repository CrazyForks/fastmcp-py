"""In-task elicitation under SEP-2663 (poll-based input).

A background worker that calls ``ctx.elicit()`` has no live request, so SEP-2663
parks the request and the task's ``tasks/get`` status flips to ``input_required``
with the outstanding ``inputRequests``. The caller answers with ``tasks/update``
and the parked worker resumes. This replaces the SEP-1686 push relay (which sent
``elicitation/create`` over a back-channel); the accept/decline/cancel semantics,
structured round-trips, and sequential elicitations are preserved, driven here
in-process because there is no client task API until Phase 4.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import fastmcp_tasks.input_store as input_store
from pydantic import BaseModel

from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
)
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    get_task,
    running_task_server,
    submit_task,
    update_task,
    wait_for_task,
)


async def _wait_for_input_required(server: FastMCP, task_id: str, timeout: float = 5.0):
    """Poll until the task is waiting on input, returning the GetTaskResult."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        got = await get_task(server, task_id)
        if got.status == "input_required":
            return got
        if got.status in ("completed", "failed", "cancelled"):
            raise AssertionError(
                f"Task {task_id} reached {got.status!r} before requesting input"
            )
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError(f"Task {task_id} never requested input")
        await asyncio.sleep(0.02)


async def _drive(server: FastMCP, name: str, answers: list[dict[str, Any]]) -> str:
    """Submit a task, answer each elicitation in turn, return its result text."""
    created = await submit_task(server, name, {})
    for answer in answers:
        got = await _wait_for_input_required(server, created.task_id)
        key = next(iter(got.input_requests))
        request = got.input_requests[key]
        assert request["method"] == "elicitation/create"
        await update_task(server, created.task_id, {key: answer})
    final = await wait_for_task(server, created.task_id)
    assert final.status == "completed", final.error
    assert final.result is not None
    return final.result["content"][0]["text"]


async def test_accept_answers_the_elicitation():
    mcp = FastMCP("relay-accept")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def ask_name(ctx: Context) -> str:
        result = await ctx.elicit("What is your name?", str)
        if isinstance(result, AcceptedElicitation):
            return f"Hello, {result.data}!"
        return "No name"

    async with running_task_server(mcp):
        text = await _drive(
            mcp, "ask_name", [{"action": "accept", "content": {"value": "Alice"}}]
        )
        assert text == "Hello, Alice!"


async def test_decline_yields_declined_elicitation():
    mcp = FastMCP("relay-decline")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def optional_input(ctx: Context) -> str:
        result = await ctx.elicit("Provide a name?", str)
        if isinstance(result, DeclinedElicitation):
            return "User declined"
        return "Other"

    async with running_task_server(mcp):
        text = await _drive(mcp, "optional_input", [{"action": "decline"}])
        assert text == "User declined"


async def test_cancel_yields_cancelled_elicitation():
    mcp = FastMCP("relay-cancel")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def cancellable(ctx: Context) -> str:
        result = await ctx.elicit("Input?", str)
        if isinstance(result, CancelledElicitation):
            return "Cancelled"
        return "Not cancelled"

    async with running_task_server(mcp):
        text = await _drive(mcp, "cancellable", [{"action": "cancel"}])
        assert text == "Cancelled"


async def test_dataclass_round_trips():
    mcp = FastMCP("relay-dataclass")
    mcp.add_extension(TasksExtension())

    @dataclass
    class UserInfo:
        name: str
        age: int

    @mcp.tool(task=True)
    async def get_user(ctx: Context) -> str:
        result = await ctx.elicit("Provide user info", UserInfo)
        if isinstance(result, AcceptedElicitation):
            assert isinstance(result.data, UserInfo)
            return f"{result.data.name} is {result.data.age}"
        return "No info"

    async with running_task_server(mcp):
        text = await _drive(
            mcp,
            "get_user",
            [{"action": "accept", "content": {"name": "Bob", "age": 30}}],
        )
        assert text == "Bob is 30"


async def test_pydantic_model_round_trips():
    mcp = FastMCP("relay-pydantic")
    mcp.add_extension(TasksExtension())

    class Config(BaseModel):
        host: str
        port: int

    @mcp.tool(task=True)
    async def get_config(ctx: Context) -> str:
        result = await ctx.elicit("Server config?", Config)
        if isinstance(result, AcceptedElicitation):
            assert isinstance(result.data, Config)
            return f"{result.data.host}:{result.data.port}"
        return "No config"

    async with running_task_server(mcp):
        text = await _drive(
            mcp,
            "get_config",
            [{"action": "accept", "content": {"host": "localhost", "port": 8080}}],
        )
        assert text == "localhost:8080"


async def test_multiple_sequential_elicitations():
    mcp = FastMCP("relay-multi")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def two_questions(ctx: Context) -> str:
        r1 = await ctx.elicit("First name?", str)
        r2 = await ctx.elicit("Last name?", str)
        if isinstance(r1, AcceptedElicitation) and isinstance(r2, AcceptedElicitation):
            return f"{r1.data} {r2.data}"
        return "Incomplete"

    async with running_task_server(mcp):
        text = await _drive(
            mcp,
            "two_questions",
            [
                {"action": "accept", "content": {"value": "Jane"}},
                {"action": "accept", "content": {"value": "Doe"}},
            ],
        )
        assert text == "Jane Doe"


async def test_unanswered_input_times_out_to_cancel(monkeypatch):
    """A worker that is never answered eventually resumes with a cancel.

    The poll model has no "no handler" fast path; instead the parked worker's
    blocking wait is bounded by ``INPUT_TTL_SECONDS``. Patched short here so the
    timeout-to-cancel behaviour is testable.
    """
    monkeypatch.setattr(input_store, "INPUT_TTL_SECONDS", 1)

    mcp = FastMCP("relay-timeout")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def needs_input(ctx: Context) -> str:
        result = await ctx.elicit("Input?", str)
        if isinstance(result, CancelledElicitation):
            return "Cancelled as expected"
        return "Other"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "needs_input", {})
        # Never answer; the worker's bounded wait resolves to cancel.
        final = await wait_for_task(mcp, created.task_id, timeout=10.0)
        assert final.status == "completed"
        assert final.result is not None
        assert final.result["content"][0]["text"] == "Cancelled as expected"
