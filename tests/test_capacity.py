"""
Unit tests for capacity management.

These tests can run without KVM hardware by mocking the sandbox creation.
"""

import os
from unittest.mock import patch

# Set test environment variables before importing
os.environ["WORKSPACE_BASE_DIR"] = "/tmp/test-workspaces"
os.environ["MAX_SANDBOXES"] = "5"
os.environ["DEFAULT_MEMORY_MB"] = "512"
os.environ["MIN_MEMORY_MB"] = "256"
os.environ["MAX_MEMORY_MB"] = "2048"
os.environ["HOST_RESERVED_MEMORY_MB"] = "4096"

from workspace_service.config import ServiceConfig, reset_config


class TestCapacityChecking:
    """Tests for capacity checking logic."""

    def setup_method(self):
        """Reset config before each test."""
        reset_config()

    def test_can_create_sandbox_within_limits(self):
        """Test that sandbox creation is allowed within limits."""
        from workspace_service.sandbox_manager import SandboxManager

        # Create a mock config with known values
        config = ServiceConfig.from_env()

        # Create manager with mocked directories
        with patch.object(SandboxManager, "_ensure_directories"):
            with patch.object(SandboxManager, "_load_existing_sandboxes"):
                manager = SandboxManager(config)

        # Should be able to create with default memory
        can_create, reason = manager.can_create_sandbox(512)
        assert can_create is True
        assert reason == ""

    def test_can_create_sandbox_at_max_count(self):
        """Test that sandbox creation is rejected at max count."""
        from workspace_service.sandbox_manager import SandboxConfig, SandboxManager

        config = ServiceConfig.from_env()

        with patch.object(SandboxManager, "_ensure_directories"):
            with patch.object(SandboxManager, "_load_existing_sandboxes"):
                manager = SandboxManager(config)

        # Add fake sandboxes to reach limit
        for i in range(config.max_sandboxes):
            manager._active_sandboxes[f"sandbox-{i}"] = SandboxConfig(
                sandbox_id=f"sandbox-{i}",
                template="default",
                memory_mb=256,
                vcpu_count=1,
                workspace_id=f"workspace-{i}",
                status="running",
                created_at="2024-01-01T00:00:00",
            )

        can_create, reason = manager.can_create_sandbox(512)
        assert can_create is False
        assert "Maximum sandbox limit" in reason

    def test_can_create_sandbox_memory_too_low(self):
        """Test that sandbox creation is rejected when memory is too low."""
        from workspace_service.sandbox_manager import SandboxManager

        # Ensure min memory is set correctly for this test
        os.environ["MIN_MEMORY_MB"] = "256"
        reset_config()
        config = ServiceConfig.from_env()

        with patch.object(SandboxManager, "_ensure_directories"):
            with patch.object(SandboxManager, "_load_existing_sandboxes"):
                manager = SandboxManager(config)

        # Try to create with memory below minimum (128 < 256)
        can_create, reason = manager.can_create_sandbox(128)
        assert can_create is False, f"Expected False, config min={config.min_memory_mb}"
        assert "Memory too low" in reason

    def test_can_create_sandbox_memory_too_high(self):
        """Test that sandbox creation is rejected when memory is too high."""
        from workspace_service.sandbox_manager import SandboxManager

        config = ServiceConfig.from_env()

        with patch.object(SandboxManager, "_ensure_directories"):
            with patch.object(SandboxManager, "_load_existing_sandboxes"):
                manager = SandboxManager(config)

        # Try to create with memory above maximum
        can_create, reason = manager.can_create_sandbox(4096)  # Above 2048 max
        assert can_create is False
        assert "Memory too high" in reason

    def test_memory_tracking(self):
        """Test that memory usage is tracked correctly."""
        from workspace_service.sandbox_manager import SandboxConfig, SandboxManager

        config = ServiceConfig.from_env()

        with patch.object(SandboxManager, "_ensure_directories"):
            with patch.object(SandboxManager, "_load_existing_sandboxes"):
                manager = SandboxManager(config)

        # Initially no memory used
        assert manager.memory_used_mb == 0

        # Add a running sandbox
        manager._active_sandboxes["sandbox-1"] = SandboxConfig(
            sandbox_id="sandbox-1",
            template="default",
            memory_mb=512,
            vcpu_count=1,
            workspace_id="workspace-1",
            status="running",
            created_at="2024-01-01T00:00:00",
        )

        assert manager.memory_used_mb == 512

        # Add another running sandbox
        manager._active_sandboxes["sandbox-2"] = SandboxConfig(
            sandbox_id="sandbox-2",
            template="default",
            memory_mb=1024,
            vcpu_count=1,
            workspace_id="workspace-2",
            status="running",
            created_at="2024-01-01T00:00:00",
        )

        assert manager.memory_used_mb == 1536

        # Paused sandboxes shouldn't count
        manager._active_sandboxes["sandbox-3"] = SandboxConfig(
            sandbox_id="sandbox-3",
            template="default",
            memory_mb=512,
            vcpu_count=1,
            workspace_id="workspace-3",
            status="paused",
            created_at="2024-01-01T00:00:00",
        )

        assert manager.memory_used_mb == 1536  # Still 1536, paused doesn't count

    def test_capacity_info(self):
        """Test that capacity info is returned correctly."""
        from workspace_service.sandbox_manager import SandboxConfig, SandboxManager

        config = ServiceConfig.from_env()

        with patch.object(SandboxManager, "_ensure_directories"):
            with patch.object(SandboxManager, "_load_existing_sandboxes"):
                manager = SandboxManager(config)

        # Add a sandbox
        manager._active_sandboxes["sandbox-1"] = SandboxConfig(
            sandbox_id="sandbox-1",
            template="default",
            memory_mb=512,
            vcpu_count=1,
            workspace_id="workspace-1",
            status="running",
            created_at="2024-01-01T00:00:00",
        )

        info = manager.get_capacity_info()

        assert info["active_sandboxes"] == 1
        assert info["max_sandboxes"] == config.max_sandboxes
        assert info["memory_used_mb"] == 512
        assert info["memory_budget_mb"] == config.total_memory_budget_mb
        assert info["memory_available_mb"] == config.total_memory_budget_mb - 512
