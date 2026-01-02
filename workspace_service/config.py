"""
Configuration management for the Firecracker Workspace Service.

All configuration values can be set via environment variables.
This module provides centralized configuration with sensible defaults.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ServiceConfig:
    """Configuration for the workspace service."""

    # Directory paths
    base_dir: Path
    kernels_dir: Path
    rootfs_dir: Path
    sandboxes_dir: Path
    snapshots_dir: Path

    # Binary paths
    firecracker_bin: str
    jailer_bin: str

    # Resource limits
    default_memory_mb: int
    min_memory_mb: int
    max_memory_mb: int
    default_vcpu_count: int
    min_vcpu_count: int
    max_vcpu_count: int

    # Capacity limits
    max_sandboxes: int
    host_reserved_memory_mb: int  # Memory reserved for host OS

    # Timeouts (in seconds)
    vm_boot_timeout: float
    guest_agent_timeout: float
    command_default_timeout: int
    api_socket_timeout: float

    # Protocol settings
    vsock_port: int
    max_message_size: int

    # API settings
    host: str
    port: int
    cors_origins: str

    @property
    def total_memory_budget_mb(self) -> int:
        """Calculate total memory available for sandboxes."""
        # Try to get actual system memory, fall back to 16GB default
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        # Convert from KB to MB
                        total_kb = int(line.split()[1])
                        return (total_kb // 1024) - self.host_reserved_memory_mb
        except (FileNotFoundError, ValueError, IndexError):
            pass
        # Default to 16GB - reserved
        return 16384 - self.host_reserved_memory_mb

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        """Load configuration from environment variables."""
        base_dir = Path(os.environ.get("WORKSPACE_BASE_DIR", "/var/lib/firecracker-workspaces"))

        return cls(
            # Directory paths
            base_dir=base_dir,
            kernels_dir=Path(os.environ.get("WORKSPACE_KERNELS_DIR", str(base_dir / "kernels"))),
            rootfs_dir=Path(os.environ.get("WORKSPACE_ROOTFS_DIR", str(base_dir / "rootfs"))),
            sandboxes_dir=Path(
                os.environ.get("WORKSPACE_SANDBOXES_DIR", str(base_dir / "sandboxes"))
            ),
            snapshots_dir=Path(
                os.environ.get("WORKSPACE_SNAPSHOTS_DIR", str(base_dir / "snapshots"))
            ),
            # Binary paths
            firecracker_bin=os.environ.get("FIRECRACKER_BIN", "/usr/bin/firecracker"),
            jailer_bin=os.environ.get("JAILER_BIN", "/usr/bin/jailer"),
            # Resource limits
            default_memory_mb=int(os.environ.get("DEFAULT_MEMORY_MB", "512")),
            min_memory_mb=int(os.environ.get("MIN_MEMORY_MB", "256")),
            max_memory_mb=int(os.environ.get("MAX_MEMORY_MB", "2048")),
            default_vcpu_count=int(os.environ.get("DEFAULT_VCPU_COUNT", "1")),
            min_vcpu_count=int(os.environ.get("MIN_VCPU_COUNT", "1")),
            max_vcpu_count=int(os.environ.get("MAX_VCPU_COUNT", "4")),
            # Capacity limits
            max_sandboxes=int(os.environ.get("MAX_SANDBOXES", "20")),
            host_reserved_memory_mb=int(
                os.environ.get(
                    "HOST_RESERVED_MEMORY_MB",
                    "4096",  # 4GB reserved for host
                )
            ),
            # Timeouts
            vm_boot_timeout=float(os.environ.get("VM_BOOT_TIMEOUT", "5.0")),
            guest_agent_timeout=float(os.environ.get("GUEST_AGENT_TIMEOUT", "30.0")),
            command_default_timeout=int(os.environ.get("COMMAND_DEFAULT_TIMEOUT", "300")),
            api_socket_timeout=float(os.environ.get("API_SOCKET_TIMEOUT", "5.0")),
            # Protocol settings
            vsock_port=int(os.environ.get("VSOCK_PORT", "5000")),
            max_message_size=int(
                os.environ.get(
                    "MAX_MESSAGE_SIZE",
                    str(10 * 1024 * 1024),  # 10MB
                )
            ),
            # API settings
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8080")),
            cors_origins=os.environ.get("CORS_ORIGINS", "*"),
        )

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []

        # Validate memory limits
        if self.min_memory_mb > self.max_memory_mb:
            errors.append(
                f"MIN_MEMORY_MB ({self.min_memory_mb}) > MAX_MEMORY_MB ({self.max_memory_mb})"
            )
        if self.default_memory_mb < self.min_memory_mb:
            errors.append(
                f"DEFAULT_MEMORY_MB ({self.default_memory_mb}) < "
                f"MIN_MEMORY_MB ({self.min_memory_mb})"
            )
        if self.default_memory_mb > self.max_memory_mb:
            errors.append(
                f"DEFAULT_MEMORY_MB ({self.default_memory_mb}) > "
                f"MAX_MEMORY_MB ({self.max_memory_mb})"
            )

        # Validate vCPU limits
        if self.min_vcpu_count > self.max_vcpu_count:
            errors.append(
                f"MIN_VCPU_COUNT ({self.min_vcpu_count}) > MAX_VCPU_COUNT ({self.max_vcpu_count})"
            )
        if self.default_vcpu_count < self.min_vcpu_count:
            errors.append(
                f"DEFAULT_VCPU_COUNT ({self.default_vcpu_count}) < "
                f"MIN_VCPU_COUNT ({self.min_vcpu_count})"
            )
        if self.default_vcpu_count > self.max_vcpu_count:
            errors.append(
                f"DEFAULT_VCPU_COUNT ({self.default_vcpu_count}) > "
                f"MAX_VCPU_COUNT ({self.max_vcpu_count})"
            )

        # Validate capacity
        if self.max_sandboxes < 1:
            errors.append(f"MAX_SANDBOXES ({self.max_sandboxes}) must be >= 1")

        # Check if binaries exist (warning only, not error)
        if not Path(self.firecracker_bin).exists():
            errors.append(f"Firecracker binary not found: {self.firecracker_bin}")

        return errors


# Global configuration instance
_config: Optional[ServiceConfig] = None


def get_config() -> ServiceConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = ServiceConfig.from_env()
    return _config


def reset_config() -> None:
    """Reset the global configuration (useful for testing)."""
    global _config
    _config = None
