"""
Sandbox Manager - Handles Firecracker VM lifecycle management.

This module manages the creation, destruction, pausing, and resuming of
Firecracker microVMs, as well as communication with the guest agent.
"""

import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from .config import ServiceConfig, get_config

logger = logging.getLogger(__name__)


@dataclass
class SandboxConfig:
    """Configuration and state for a sandbox."""

    sandbox_id: str
    template: str
    memory_mb: int
    vcpu_count: int
    workspace_id: str
    status: str  # "running", "paused", "stopped"
    created_at: str
    ip_address: Optional[str] = None
    vsock_cid: Optional[int] = None
    firecracker_pid: Optional[int] = None


class VsockClient:
    """Client for communicating with guest agent via vsock.

    Firecracker exposes vsock via a Unix domain socket (uds_path).
    To connect to the guest, the host:
    1. Connects to the Unix socket
    2. Sends "CONNECT <port>\n" to initiate connection to guest
    3. Receives "OK <local_port>\n" on success
    4. Then communicates using the length-prefixed JSON protocol
    """

    VSOCK_PORT = 5000
    MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10MB limit

    def __init__(self, uds_path: str):
        self.uds_path = uds_path
        self.sock = None

    def connect(self, timeout: float = 30.0):
        """Connect to the guest agent via Firecracker's vsock UDS."""
        start_time = time.time()
        last_error = None

        while time.time() - start_time < timeout:
            try:
                # Connect to Firecracker's vsock Unix socket
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(timeout)
                self.sock.connect(self.uds_path)

                # Send CONNECT command to establish connection to guest port
                connect_cmd = f"CONNECT {self.VSOCK_PORT}\n"
                self.sock.sendall(connect_cmd.encode())

                # Read response - should be "OK <local_port>\n"
                response = b""
                while b"\n" not in response:
                    chunk = self.sock.recv(256)
                    if not chunk:
                        raise ConnectionError("Connection closed waiting for CONNECT response")
                    response += chunk

                response_str = response.decode().strip()
                if response_str.startswith("OK"):
                    return  # Successfully connected
                else:
                    raise ConnectionError(f"CONNECT failed: {response_str}")

            except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
                last_error = e
                if self.sock:
                    try:
                        self.sock.close()
                    except OSError:
                        pass
                    self.sock = None
                time.sleep(0.1)

        raise ConnectionError(f"Failed to connect to guest via {self.uds_path}: {last_error}")

    def disconnect(self):
        """Disconnect from the guest agent."""
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _send_request(self, request: dict, timeout: float = 300.0) -> dict:
        """Send a request and receive response using length-prefixed JSON protocol."""
        if not self.sock:
            self.connect()

        self.sock.settimeout(timeout)
        data = json.dumps(request).encode()

        # Validate message size
        if len(data) > self.MAX_MESSAGE_SIZE:
            raise ValueError(f"Message too large: {len(data)} bytes (max {self.MAX_MESSAGE_SIZE})")

        # Send length-prefixed message (use sendall for reliability)
        self.sock.sendall(len(data).to_bytes(4, "big"))
        self.sock.sendall(data)

        # Receive length-prefixed response
        length_bytes = self._recv_exact(4)
        length = int.from_bytes(length_bytes, "big")

        # Validate response size
        if length > self.MAX_MESSAGE_SIZE:
            raise ValueError(f"Response too large: {length} bytes (max {self.MAX_MESSAGE_SIZE})")

        response_data = self._recv_exact(length)

        return json.loads(response_data.decode())

    def _recv_exact(self, n: int) -> bytes:
        """Receive exactly n bytes."""
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Connection closed")
            data += chunk
        return data

    def exec_command(
        self, command: str, timeout: int = 300, working_dir: str = "/workspace"
    ) -> dict:
        """Execute a command in the guest."""
        return self._send_request(
            {"action": "exec", "command": command, "timeout": timeout, "working_dir": working_dir},
            timeout=timeout + 5,
        )

    def read_file(self, path: str) -> dict:
        """Read a file from the guest."""
        return self._send_request({"action": "read_file", "path": path})

    def write_file(self, path: str, content: str, is_base64: bool = False) -> dict:
        """Write a file to the guest."""
        return self._send_request(
            {"action": "write_file", "path": path, "content": content, "is_base64": is_base64}
        )

    def list_files(self, path: str = "/workspace") -> dict:
        """List files in a directory."""
        return self._send_request({"action": "list_files", "path": path})


class SandboxManager:
    """Manages Firecracker sandbox lifecycle with capacity tracking."""

    def __init__(self, config: Optional[ServiceConfig] = None):
        self.config = config or get_config()

        # Directory paths from config
        self.BASE_DIR = self.config.base_dir
        self.KERNELS_DIR = self.config.kernels_dir
        self.ROOTFS_DIR = self.config.rootfs_dir
        self.SANDBOXES_DIR = self.config.sandboxes_dir
        self.SNAPSHOTS_DIR = self.config.snapshots_dir

        # Binary paths from config
        self.FIRECRACKER_BIN = self.config.firecracker_bin
        self.JAILER_BIN = self.config.jailer_bin

        self._ensure_directories()
        self._active_sandboxes: Dict[str, SandboxConfig] = {}
        self._vsock_clients: Dict[str, VsockClient] = {}
        self._next_vsock_cid = 3  # CID 0, 1, 2 are reserved
        self._load_existing_sandboxes()

    @property
    def active_sandbox_count(self) -> int:
        """Return the number of active sandboxes."""
        return len(self._active_sandboxes)

    @property
    def memory_used_mb(self) -> int:
        """Return total memory used by active sandboxes."""
        return sum(s.memory_mb for s in self._active_sandboxes.values() if s.status == "running")

    @property
    def memory_available_mb(self) -> int:
        """Return memory available for new sandboxes."""
        return self.config.total_memory_budget_mb - self.memory_used_mb

    def can_create_sandbox(self, memory_mb: int) -> tuple[bool, str]:
        """Check if a new sandbox can be created with the given resources."""
        # Check sandbox count limit
        if self.active_sandbox_count >= self.config.max_sandboxes:
            return False, f"Maximum sandbox limit reached ({self.config.max_sandboxes})"

        # Check memory limit
        if memory_mb > self.memory_available_mb:
            return False, (
                f"Insufficient memory: requested {memory_mb}MB, "
                f"available {self.memory_available_mb}MB"
            )

        # Check per-sandbox memory limits
        if memory_mb < self.config.min_memory_mb:
            return False, (f"Memory too low: minimum is {self.config.min_memory_mb}MB")
        if memory_mb > self.config.max_memory_mb:
            return False, (f"Memory too high: maximum is {self.config.max_memory_mb}MB")

        return True, ""

    def get_capacity_info(self) -> dict:
        """Return capacity information for the health endpoint."""
        return {
            "active_sandboxes": self.active_sandbox_count,
            "max_sandboxes": self.config.max_sandboxes,
            "memory_used_mb": self.memory_used_mb,
            "memory_available_mb": self.memory_available_mb,
            "memory_budget_mb": self.config.total_memory_budget_mb,
        }

    def _ensure_directories(self):
        """Create required directories if they don't exist."""
        for dir_path in [self.KERNELS_DIR, self.ROOTFS_DIR, self.SANDBOXES_DIR, self.SNAPSHOTS_DIR]:
            dir_path.mkdir(parents=True, exist_ok=True)

    def _load_existing_sandboxes(self):
        """Load state of existing sandboxes from disk."""
        for sandbox_dir in self.SANDBOXES_DIR.iterdir():
            if sandbox_dir.is_dir():
                state_file = sandbox_dir / "state.json"
                if state_file.exists():
                    try:
                        state = json.loads(state_file.read_text())
                        config = SandboxConfig(**state)
                        # Mark as stopped since we just started
                        config.status = "stopped"
                        self._active_sandboxes[config.sandbox_id] = config
                        # Update next CID
                        if config.vsock_cid and config.vsock_cid >= self._next_vsock_cid:
                            self._next_vsock_cid = config.vsock_cid + 1
                    except Exception as e:
                        logger.warning(f"Failed to load sandbox state from {state_file}: {e}")

    def _get_sandbox_dir(self, sandbox_id: str) -> Path:
        return self.SANDBOXES_DIR / sandbox_id

    def _get_socket_path(self, sandbox_id: str) -> Path:
        return self._get_sandbox_dir(sandbox_id) / "firecracker.sock"

    def _get_vsock_path(self, sandbox_id: str) -> Path:
        return self._get_sandbox_dir(sandbox_id) / "vsock.sock"

    def _get_kernel_path(self, template: str = "default") -> Path:
        return self.KERNELS_DIR / f"{template}-vmlinux.bin"

    def _get_base_rootfs_path(self, template: str = "default") -> Path:
        return self.ROOTFS_DIR / f"{template}-rootfs.ext4"

    def _create_overlay_rootfs(self, sandbox_id: str, template: str) -> Path:
        """Create a copy-on-write overlay of the base rootfs."""
        sandbox_dir = self._get_sandbox_dir(sandbox_id)
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        base_rootfs = self._get_base_rootfs_path(template)
        if not base_rootfs.exists():
            raise FileNotFoundError(f"Base rootfs not found: {base_rootfs}")

        overlay_rootfs = sandbox_dir / "rootfs.ext4"

        # Create a sparse copy (copy-on-write via reflink if supported)
        subprocess.run(
            ["cp", "--reflink=auto", "--sparse=always", str(base_rootfs), str(overlay_rootfs)],
            check=True,
        )

        return overlay_rootfs

    def _allocate_vsock_cid(self) -> int:
        """Allocate a unique vsock CID for the sandbox."""
        cid = self._next_vsock_cid
        self._next_vsock_cid += 1
        return cid

    def _call_firecracker_api(
        self, sandbox_id: str, method: str, endpoint: str, data: dict = None
    ) -> dict:
        """Call the Firecracker API via unix socket using curl.

        Uses --fail-with-body to ensure HTTP errors are properly detected
        while still capturing the response body for error messages.
        """
        socket_path = self._get_socket_path(sandbox_id)

        # Use --fail-with-body to detect HTTP errors (4xx, 5xx) while keeping response body
        cmd = ["curl", "-s", "--fail-with-body", "--unix-socket", str(socket_path)]

        if method == "PUT":
            cmd.extend(["-X", "PUT"])
            if data:
                cmd.extend(["-H", "Content-Type: application/json"])
                cmd.extend(["-d", json.dumps(data)])
        elif method == "GET":
            cmd.extend(["-X", "GET"])
        elif method == "PATCH":
            cmd.extend(["-X", "PATCH"])
            if data:
                cmd.extend(["-H", "Content-Type: application/json"])
                cmd.extend(["-d", json.dumps(data)])

        cmd.append(f"http://localhost{endpoint}")

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            # Try to parse error response for better error messages
            error_msg = result.stderr or result.stdout or "Unknown error"
            try:
                error_data = json.loads(result.stdout)
                if "fault_message" in error_data:
                    error_msg = error_data["fault_message"]
            except (json.JSONDecodeError, TypeError):
                pass
            raise Exception(f"Firecracker API error on {endpoint}: {error_msg}")

        if result.stdout:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {}
        return {}

    async def create_sandbox(
        self,
        template: str = "default",
        memory_mb: Optional[int] = None,
        vcpu_count: Optional[int] = None,
        workspace_id: Optional[str] = None,
    ) -> SandboxConfig:
        """Create and start a new sandbox.

        Args:
            template: The rootfs template to use (default: "default")
            memory_mb: Memory allocation in MB (default from config)
            vcpu_count: Number of vCPUs (default from config)
            workspace_id: Optional workspace ID for persistence

        Returns:
            SandboxConfig with the new sandbox details

        Raises:
            ValueError: If resource limits are exceeded
            FileNotFoundError: If kernel or rootfs not found
        """
        # Apply defaults from config
        memory_mb = memory_mb or self.config.default_memory_mb
        vcpu_count = vcpu_count or self.config.default_vcpu_count

        # Validate vCPU count
        if vcpu_count < self.config.min_vcpu_count:
            raise ValueError(f"vCPU count too low: minimum is {self.config.min_vcpu_count}")
        if vcpu_count > self.config.max_vcpu_count:
            raise ValueError(f"vCPU count too high: maximum is {self.config.max_vcpu_count}")

        # Check capacity before creating
        can_create, reason = self.can_create_sandbox(memory_mb)
        if not can_create:
            raise ValueError(reason)

        sandbox_id = str(uuid.uuid4())[:8]
        workspace_id = workspace_id or sandbox_id

        sandbox_dir = self._get_sandbox_dir(sandbox_id)
        socket_path = self._get_socket_path(sandbox_id)
        vsock_path = self._get_vsock_path(sandbox_id)

        # Verify kernel exists
        kernel_path = self._get_kernel_path(template)
        if not kernel_path.exists():
            raise FileNotFoundError(f"Kernel not found: {kernel_path}")

        # Create overlay rootfs
        rootfs_path = self._create_overlay_rootfs(sandbox_id, template)

        # Create workspace directory
        workspace_dir = sandbox_dir / "workspace"
        workspace_dir.mkdir(exist_ok=True)

        # Allocate vsock CID for guest communication
        vsock_cid = self._allocate_vsock_cid()

        # Remove old socket if exists
        if socket_path.exists():
            socket_path.unlink()

        # Start Firecracker process
        firecracker_proc = subprocess.Popen(
            [self.FIRECRACKER_BIN, "--api-sock", str(socket_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(sandbox_dir),
        )

        # Wait for socket to be ready
        for _ in range(50):  # 5 second timeout
            if socket_path.exists():
                break
            await asyncio.sleep(0.1)
        else:
            firecracker_proc.kill()
            raise Exception("Firecracker socket not ready after 5 seconds")

        try:
            # Configure the VM via API
            # 1. Set machine config
            self._call_firecracker_api(
                sandbox_id,
                "PUT",
                "/machine-config",
                {"vcpu_count": vcpu_count, "mem_size_mib": memory_mb, "smt": False},
            )

            # 2. Set boot source
            self._call_firecracker_api(
                sandbox_id,
                "PUT",
                "/boot-source",
                {
                    "kernel_image_path": str(kernel_path),
                    "boot_args": "console=ttyS0 reboot=k panic=1 pci=off init=/sbin/init",
                },
            )

            # 3. Set root drive
            self._call_firecracker_api(
                sandbox_id,
                "PUT",
                "/drives/rootfs",
                {
                    "drive_id": "rootfs",
                    "path_on_host": str(rootfs_path),
                    "is_root_device": True,
                    "is_read_only": False,
                },
            )

            # 4. Set vsock device for host-guest communication
            self._call_firecracker_api(
                sandbox_id,
                "PUT",
                "/vsock",
                {"vsock_id": "vsock0", "guest_cid": vsock_cid, "uds_path": str(vsock_path)},
            )

            # 5. Start the VM
            self._call_firecracker_api(
                sandbox_id, "PUT", "/actions", {"action_type": "InstanceStart"}
            )

        except Exception as e:
            firecracker_proc.kill()
            shutil.rmtree(sandbox_dir, ignore_errors=True)
            raise Exception(f"Failed to configure VM: {e}")

        config = SandboxConfig(
            sandbox_id=sandbox_id,
            template=template,
            memory_mb=memory_mb,
            vcpu_count=vcpu_count,
            workspace_id=workspace_id,
            status="running",
            created_at=datetime.utcnow().isoformat(),
            vsock_cid=vsock_cid,
            firecracker_pid=firecracker_proc.pid,
        )

        # Save state
        state_file = sandbox_dir / "state.json"
        state_file.write_text(json.dumps(asdict(config)))

        self._active_sandboxes[sandbox_id] = config

        # Create vsock client using the vsock UDS path
        client = VsockClient(str(vsock_path))
        self._vsock_clients[sandbox_id] = client

        # Wait for guest agent to be ready
        try:
            client.connect(timeout=30.0)
        except Exception as e:
            logger.warning(f"Guest agent not ready: {e}")

        return config

    async def destroy_sandbox(self, sandbox_id: str):
        """Stop and clean up a sandbox."""
        sandbox_dir = self._get_sandbox_dir(sandbox_id)
        config = self._active_sandboxes.get(sandbox_id)

        # Disconnect vsock client
        if sandbox_id in self._vsock_clients:
            self._vsock_clients[sandbox_id].disconnect()
            del self._vsock_clients[sandbox_id]

        # Send shutdown action
        try:
            self._call_firecracker_api(
                sandbox_id, "PUT", "/actions", {"action_type": "SendCtrlAltDel"}
            )
            await asyncio.sleep(1)
        except Exception:
            pass

        # Kill firecracker process if still running
        if config and config.firecracker_pid:
            try:
                os.kill(config.firecracker_pid, 9)
            except ProcessLookupError:
                pass

        # Clean up files
        if sandbox_dir.exists():
            shutil.rmtree(sandbox_dir)

        if sandbox_id in self._active_sandboxes:
            del self._active_sandboxes[sandbox_id]

    async def pause_sandbox(self, sandbox_id: str):
        """Pause a sandbox by creating a snapshot."""
        config = self._active_sandboxes.get(sandbox_id)
        if not config:
            raise ValueError(f"Sandbox not found: {sandbox_id}")

        sandbox_dir = self._get_sandbox_dir(sandbox_id)
        snapshot_dir = self.SNAPSHOTS_DIR / sandbox_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        # Pause the VM first
        self._call_firecracker_api(sandbox_id, "PATCH", "/vm", {"state": "Paused"})

        # Create snapshot via Firecracker API
        self._call_firecracker_api(
            sandbox_id,
            "PUT",
            "/snapshot/create",
            {
                "snapshot_type": "Full",
                "snapshot_path": str(snapshot_dir / "snapshot"),
                "mem_file_path": str(snapshot_dir / "memory"),
            },
        )

        # Update state
        config.status = "paused"
        state_file = sandbox_dir / "state.json"
        state_file.write_text(json.dumps(asdict(config)))

        # Disconnect vsock client
        if sandbox_id in self._vsock_clients:
            self._vsock_clients[sandbox_id].disconnect()

    async def resume_sandbox(self, sandbox_id: str) -> SandboxConfig:
        """Resume a paused sandbox from snapshot."""
        config = self._active_sandboxes.get(sandbox_id)
        if not config:
            raise ValueError(f"Sandbox not found: {sandbox_id}")

        snapshot_dir = self.SNAPSHOTS_DIR / sandbox_id
        sandbox_dir = self._get_sandbox_dir(sandbox_id)
        socket_path = self._get_socket_path(sandbox_id)

        if not snapshot_dir.exists():
            raise FileNotFoundError(f"Snapshot not found for sandbox: {sandbox_id}")

        # Remove old socket if exists
        if socket_path.exists():
            socket_path.unlink()

        # Start new Firecracker process
        firecracker_proc = subprocess.Popen(
            [self.FIRECRACKER_BIN, "--api-sock", str(socket_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(sandbox_dir),
        )

        # Wait for socket
        for _ in range(50):
            if socket_path.exists():
                break
            await asyncio.sleep(0.1)
        else:
            firecracker_proc.kill()
            raise Exception("Firecracker socket not ready")

        # Load snapshot
        self._call_firecracker_api(
            sandbox_id,
            "PUT",
            "/snapshot/load",
            {
                "snapshot_path": str(snapshot_dir / "snapshot"),
                "mem_backend": {
                    "backend_type": "File",
                    "backend_path": str(snapshot_dir / "memory"),
                },
                "enable_diff_snapshots": False,
                "resume_vm": True,
            },
        )

        # Update state
        config.status = "running"
        config.firecracker_pid = firecracker_proc.pid
        state_file = sandbox_dir / "state.json"
        state_file.write_text(json.dumps(asdict(config)))

        # Reconnect vsock client using the vsock UDS path
        vsock_path = self._get_vsock_path(sandbox_id)
        client = VsockClient(str(vsock_path))
        self._vsock_clients[sandbox_id] = client
        try:
            client.connect(timeout=10.0)
        except Exception as e:
            logger.warning(f"Failed to reconnect to guest agent: {e}")

        return config

    def _get_vsock_client(self, sandbox_id: str) -> VsockClient:
        """Get or create vsock client for a sandbox."""
        if sandbox_id not in self._vsock_clients:
            config = self._active_sandboxes.get(sandbox_id)
            if not config:
                raise ValueError(f"Sandbox not found: {sandbox_id}")
            vsock_path = self._get_vsock_path(sandbox_id)
            client = VsockClient(str(vsock_path))
            client.connect()
            self._vsock_clients[sandbox_id] = client
        return self._vsock_clients[sandbox_id]

    async def exec_command(
        self, sandbox_id: str, command: str, timeout: int = 300, working_dir: str = "/workspace"
    ) -> dict:
        """Execute a command in the sandbox."""
        client = self._get_vsock_client(sandbox_id)
        return client.exec_command(command, timeout, working_dir)

    async def read_file(self, sandbox_id: str, path: str) -> dict:
        """Read a file from the sandbox."""
        client = self._get_vsock_client(sandbox_id)
        return client.read_file(path)

    async def write_file(
        self, sandbox_id: str, path: str, content: str, is_base64: bool = False
    ) -> dict:
        """Write a file to the sandbox."""
        client = self._get_vsock_client(sandbox_id)
        return client.write_file(path, content, is_base64)

    async def list_files(self, sandbox_id: str, path: str = "/workspace") -> dict:
        """List files in a directory."""
        client = self._get_vsock_client(sandbox_id)
        return client.list_files(path)
