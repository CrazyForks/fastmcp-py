"""Background task execution for FastMCP via the SEP-2663 tasks extension."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fastmcp-tasks")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]
