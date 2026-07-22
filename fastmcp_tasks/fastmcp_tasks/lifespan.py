"""Docket lifecycle for FastMCP background tasks.

Extracted from ``fastmcp.server.mixins.lifespan.LifespanMixin._docket_lifespan``
during the SEP-1686 -> SEP-2663 migration. The logic — start Docket and a Worker
at the runtime-tree root when there are task-enabled components, register those
components' callables, and run the worker with the snapshot-restore dependency —
is preserved verbatim so Phase 3 can drive it from ``TasksExtension.lifespan()``.

Nothing in core calls this after Phase 2; it is engine code parked here for the
Phase 3 adapter.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)


@asynccontextmanager
async def docket_lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Manage the Docket instance and Worker for background task execution.

    Docket infrastructure is only initialized if:
    1. pydocket is installed (fastmcp[tasks] extra)
    2. There are task-enabled components (task_config.mode != 'forbidden')

    Sets ``server._docket`` / ``server._worker`` for the duration and registers
    each task-enabled component's callable with the Docket, then runs the worker
    until the context exits.
    """
    from docket import Depends, Docket, Worker

    import fastmcp
    from fastmcp.server.dependencies import _current_server
    from fastmcp_tasks.components import register_component_with_docket
    from fastmcp_tasks.context import restore_task_snapshot
    from fastmcp_tasks.dependencies import (
        _current_docket,
        _current_worker,
        is_docket_available,
    )
    from fastmcp_tasks.settings import DocketSettings

    docket_settings = DocketSettings()

    # Set FastMCP server in ContextVar so CurrentFastMCP can access it
    # (use weakref to avoid reference cycles)
    server_token = _current_server.set(weakref.ref(server))

    try:
        if not is_docket_available():
            yield
            return

        # Collect task-enabled components at startup with all transforms applied.
        # Components must be available now to be registered with Docket workers;
        # dynamically added components after startup won't be registered.
        try:
            task_components = list(await server.get_tasks())
        except Exception as e:
            logger.warning(f"Failed to get tasks: {e}")
            if fastmcp.settings.mounted_components_raise_on_load_error:
                raise
            task_components = []

        if not task_components:
            yield
            return

        async with Docket(
            name=docket_settings.name,
            url=docket_settings.url,
        ) as docket:
            server._docket = docket

            for component in task_components:
                register_component_with_docket(component, docket)

            docket_token = _current_docket.set(docket)
            try:
                worker_kwargs: dict[str, Any] = {
                    "concurrency": docket_settings.concurrency,
                    "redelivery_timeout": docket_settings.redelivery_timeout,
                    "reconnection_delay": docket_settings.reconnection_delay,
                    "minimum_check_interval": docket_settings.minimum_check_interval,
                }
                if docket_settings.worker_name:
                    worker_kwargs["name"] = docket_settings.worker_name

                # Create and start Worker. The restore_task_snapshot worker-level
                # dependency runs before every task so the per-task snapshot
                # ContextVar is populated before user code or task-scoped
                # dependencies observe it.
                async with Worker(
                    docket,
                    dependencies=[Depends(restore_task_snapshot)],
                    **worker_kwargs,
                ) as worker:
                    server._worker = worker
                    worker_token = _current_worker.set(worker)
                    try:
                        worker_task = asyncio.create_task(worker.run_forever())
                        try:
                            yield
                        finally:
                            worker_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await worker_task
                    finally:
                        _current_worker.reset(worker_token)
                        server._worker = None
            finally:
                _current_docket.reset(docket_token)
                server._docket = None
    finally:
        _current_server.reset(server_token)
