"""Background task execution for FastMCP via the SEP-2663 tasks extension."""

from importlib.metadata import PackageNotFoundError, version

from fastmcp_tasks.extension import TasksExtension

try:
    __version__ = version("fastmcp-tasks")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["TasksExtension", "__version__"]
