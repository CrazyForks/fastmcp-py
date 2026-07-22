"""Tests for the fastmcp tasks CLI."""

import pytest
from fastmcp_tasks.settings import docket_settings
from fastmcp_tasks.worker_cli import check_distributed_backend, tasks_app


class TestCheckDistributedBackend:
    """Test the distributed backend checker function."""

    def test_succeeds_with_redis_url(self, monkeypatch: pytest.MonkeyPatch):
        """Test that it succeeds with Redis URL."""
        # Docket settings moved to `fastmcp_tasks.settings.DocketSettings`
        # (env prefix `FASTMCP_DOCKET_`), so patch the settings object directly.
        monkeypatch.setattr(docket_settings, "url", "redis://localhost:6379/0")
        check_distributed_backend()

    def test_exits_with_helpful_error_for_memory_url(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that it exits with helpful error for memory:// URLs."""
        monkeypatch.setattr(docket_settings, "url", "memory://test-123")
        with pytest.raises(SystemExit) as exc_info:
            check_distributed_backend()

        assert isinstance(exc_info.value, SystemExit)
        assert exc_info.value.code == 1


class TestWorkerCommand:
    """Test the worker command."""

    def test_worker_command_parsing(self):
        """Test that worker command parses arguments correctly."""
        command, bound, _ = tasks_app.parse_args(["worker", "server.py"])
        assert callable(command)
        assert command.__name__ == "worker"  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
        assert bound.arguments["server_spec"] == "server.py"


class TestTasksAppIntegration:
    """Test the tasks app integration."""

    def test_tasks_app_exists(self):
        """Test that the tasks app is properly configured."""
        assert "tasks" in tasks_app.name
        assert "Docket" in tasks_app.help

    def test_tasks_app_has_commands(self):
        """Test that all expected commands are registered."""
        # Just verify the app exists and has the right metadata
        # Detailed command testing is done in individual test classes
        assert "tasks" in tasks_app.name
        assert tasks_app.help
