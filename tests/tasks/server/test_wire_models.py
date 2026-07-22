"""Validate the SEP-2663 wire models against the vendored draft JSON schema.

The models in `fastmcp_tasks.models` serialize to the `io.modelcontextprotocol/tasks`
extension shapes. This suite validates a serialized instance of each result shape
against the corresponding `$defs` entry in the vendored draft schema
(`tests/fixtures/ext-tasks-schema-draft.json`), so wire drift is caught here.

The vendored schema composes results as `allOf[Result, Task]` where the Task arm
carries `additionalProperties: false`; a stray `_meta` therefore fails
validation. The models omit `_meta` and the runner's `exclude_none` dump keeps it
out, which is exactly what these assertions check.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp_tasks.models import (
    CancelTaskResult,
    CreateTaskResult,
    GetTaskResult,
    TaskStatus,
    UpdateTaskResult,
)
from jsonschema import Draft202012Validator

_SCHEMA = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "ext-tasks-schema-draft.json").read_text()
)
_DEFS = _SCHEMA["$defs"]

_ISO = "2026-07-21T12:00:00+00:00"


def _validate(def_name: str, instance: dict[str, Any]) -> None:
    schema = {"$defs": _DEFS, **_DEFS[def_name]}
    Draft202012Validator(schema).validate(instance)


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(by_alias=True, mode="json", exclude_none=True)


def test_create_task_result_matches_schema():
    result = CreateTaskResult(
        task_id="t1",
        status="working",
        created_at=_ISO,
        last_updated_at=_ISO,
        ttl_ms=900000,
        poll_interval_ms=5000,
    )
    _validate("CreateTaskResult", _dump(result))


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        ("working", {}),
        ("completed", {"result": {"content": [], "isError": False}}),
        ("failed", {"error": {"code": -32603, "message": "boom"}}),
        (
            "input_required",
            {
                "input_requests": {
                    "k1": {"method": "elicitation/create", "params": {"message": "?"}}
                }
            },
        ),
        ("cancelled", {}),
    ],
)
def test_get_task_result_matches_schema(status: TaskStatus, payload: dict[str, Any]):
    result = GetTaskResult(
        task_id="t1",
        status=status,
        created_at=_ISO,
        last_updated_at=_ISO,
        ttl_ms=900000,
        poll_interval_ms=5000,
        **payload,
    )
    _validate("GetTaskResult", _dump(result))


def test_get_task_result_completed_omits_error_and_inputs():
    """A completed result carries only `result` (the union arm forbids the rest)."""
    result = GetTaskResult(
        task_id="t1",
        status="completed",
        created_at=_ISO,
        last_updated_at=_ISO,
        ttl_ms=900000,
        result={"content": [], "isError": False},
    )
    dumped = _dump(result)
    assert "error" not in dumped
    assert "inputRequests" not in dumped


def test_null_ttl_is_permitted_by_schema():
    """`ttlMs` is required-but-nullable; a null TTL still validates."""
    result = CreateTaskResult(
        task_id="t1",
        status="working",
        created_at=_ISO,
        last_updated_at=_ISO,
        ttl_ms=None,
    )
    dumped = result.model_dump(by_alias=True, mode="json", exclude_none=False)
    # Drop the other None optionals the runner would also drop, keeping ttlMs=null.
    dumped = {k: v for k, v in dumped.items() if v is not None or k == "ttlMs"}
    _validate("CreateTaskResult", dumped)


@pytest.mark.parametrize("model", [UpdateTaskResult(), CancelTaskResult()])
def test_ack_results_match_schema(model: Any):
    def_name = type(model).__name__
    _validate(def_name, _dump(model))
