"""
Unit tests for the configuration module.

These tests can run without KVM hardware.
"""

import os
from pathlib import Path

# Set test environment variables before importing config
os.environ["WORKSPACE_BASE_DIR"] = "/tmp/test-workspaces"
os.environ["MAX_SANDBOXES"] = "10"
os.environ["DEFAULT_MEMORY_MB"] = "256"
os.environ["MIN_MEMORY_MB"] = "128"
os.environ["MAX_MEMORY_MB"] = "1024"

from workspace_service.config import ServiceConfig, get_config, reset_config


class TestServiceConfig:
    """Tests for ServiceConfig class."""

    def setup_method(self):
        """Reset config before each test."""
        reset_config()

    def test_from_env_defaults(self):
        """Test that config loads with defaults."""
        # Clear test env vars to test defaults
        for key in list(os.environ.keys()):
            if key.startswith("WORKSPACE_") or key in [
                "MAX_SANDBOXES",
                "DEFAULT_MEMORY_MB",
                "MIN_MEMORY_MB",
                "MAX_MEMORY_MB",
                "FIRECRACKER_BIN",
            ]:
                del os.environ[key]

        reset_config()
        config = ServiceConfig.from_env()

        assert config.base_dir == Path("/var/lib/firecracker-workspaces")
        assert config.default_memory_mb == 512
        assert config.min_memory_mb == 256
        assert config.max_memory_mb == 2048
        assert config.max_sandboxes == 20
        assert config.vsock_port == 5000

    def test_from_env_custom_values(self):
        """Test that config loads custom values from environment."""
        os.environ["WORKSPACE_BASE_DIR"] = "/custom/path"
        os.environ["MAX_SANDBOXES"] = "50"
        os.environ["DEFAULT_MEMORY_MB"] = "1024"
        os.environ["VSOCK_PORT"] = "6000"

        reset_config()
        config = ServiceConfig.from_env()

        assert config.base_dir == Path("/custom/path")
        assert config.max_sandboxes == 50
        assert config.default_memory_mb == 1024
        assert config.vsock_port == 6000

    def test_validate_valid_config(self):
        """Test validation passes for valid config."""
        os.environ["WORKSPACE_BASE_DIR"] = "/tmp/test"
        os.environ["MIN_MEMORY_MB"] = "256"
        os.environ["MAX_MEMORY_MB"] = "2048"
        os.environ["DEFAULT_MEMORY_MB"] = "512"
        os.environ["MIN_VCPU_COUNT"] = "1"
        os.environ["MAX_VCPU_COUNT"] = "4"
        os.environ["DEFAULT_VCPU_COUNT"] = "1"
        os.environ["MAX_SANDBOXES"] = "20"
        # Skip firecracker binary check for this test
        os.environ["FIRECRACKER_BIN"] = "/bin/true"

        reset_config()
        config = ServiceConfig.from_env()
        errors = config.validate()

        # Should only have firecracker binary error (since /bin/true exists)
        assert len([e for e in errors if "Firecracker binary" in e]) == 0

    def test_validate_invalid_memory_range(self):
        """Test validation fails when min > max memory."""
        os.environ["MIN_MEMORY_MB"] = "2048"
        os.environ["MAX_MEMORY_MB"] = "256"

        reset_config()
        config = ServiceConfig.from_env()
        errors = config.validate()

        assert any("MIN_MEMORY_MB" in e and "MAX_MEMORY_MB" in e for e in errors)

    def test_validate_default_out_of_range(self):
        """Test validation fails when default is out of range."""
        os.environ["MIN_MEMORY_MB"] = "512"
        os.environ["MAX_MEMORY_MB"] = "1024"
        os.environ["DEFAULT_MEMORY_MB"] = "256"  # Below min

        reset_config()
        config = ServiceConfig.from_env()
        errors = config.validate()

        assert any("DEFAULT_MEMORY_MB" in e for e in errors)

    def test_get_config_singleton(self):
        """Test that get_config returns the same instance."""
        reset_config()
        config1 = get_config()
        config2 = get_config()

        assert config1 is config2

    def test_reset_config(self):
        """Test that reset_config clears the singleton."""
        config1 = get_config()
        reset_config()
        config2 = get_config()

        # After reset, should be a new instance
        # (though values may be same if env unchanged)
        assert config1 is not config2


class TestMemoryBudget:
    """Tests for memory budget calculation."""

    def setup_method(self):
        """Reset config before each test."""
        reset_config()

    def test_total_memory_budget(self):
        """Test memory budget calculation."""
        os.environ["HOST_RESERVED_MEMORY_MB"] = "4096"

        reset_config()
        config = ServiceConfig.from_env()

        # Should return system memory minus reserved
        # On most systems this will be > 0
        budget = config.total_memory_budget_mb
        assert budget > 0
