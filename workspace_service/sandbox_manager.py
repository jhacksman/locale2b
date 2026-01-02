"""
Sandbox Manager - Handles Firecracker VM lifecycle management.

This module manages the creation, destruction, pausing, and resuming of
Firecracker microVMs, as well as communication with the guest agent.
"""

import os
import json
import subprocess
import socket
import time
import shutil
import uuid
import asyncio
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict
from datetime import datetime
import logging

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
    """Client for communicating with guest agent via vsock."""
    
    VSOCK_PORT = 5000
    
    def __init__(self, guest_cid: int):
        self.guest_cid = guest_cid
        self.sock = None
    
    def connect(self, timeout: float = 30.0):
        """Connect to the guest agent with retry."""
        self.sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        
        # Retry connection as guest may still be booting
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                self.sock.connect((self.guest_cid, self.VSOCK_PORT))
                return
            except (ConnectionRefusedError, OSError):
                time.sleep(0.1)
        
        raise ConnectionError(f"Failed to connect to guest CID {self.guest_cid}")
    
    def disconnect(self):
        """Disconnect from the guest agent."""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None
    
    def _send_request(self, request: dict, timeout: float = 300.0) -> dict:
        """Send a request and receive response."""
        if not self.sock:
            self.connect()
        
        self.sock.settimeout(timeout)
        data = json.dumps(request).encode()
        
        # Send length-prefixed message
        self.sock.send(len(data).to_bytes(4, "big"))
        self.sock.send(data)
        
        # Receive length-prefixed response
        length_bytes = self._recv_exact(4)
        length = int.from_bytes(length_bytes, "big")
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
    
    def exec_command(self, command: str, timeout: int = 300, 
                     working_dir: str = "/workspace") -> dict:
        """Execute a command in the guest."""
        return self._send_request({
            "action": "exec",
            "command": command,
            "timeout": timeout,
            "working_dir": working_dir
        }, timeout=timeout + 5)
    
    def read_file(self, path: str) -> dict:
        """Read a file from the guest."""
        return self._send_request({
            "action": "read_file",
            "path": path
        })
    
    def write_file(self, path: str, content: str, is_base64: bool = False) -> dict:
        """Write a file to the guest."""
        return self._send_request({
            "action": "write_file",
            "path": path,
            "content": content,
            "is_base64": is_base64
        })
    
    def list_files(self, path: str = "/workspace") -> dict:
        """List files in a directory."""
        return self._send_request({
            "action": "list_files",
            "path": path
        })


class SandboxManager:
    """Manages Firecracker sandbox lifecycle."""
    
    BASE_DIR = Path("/var/lib/firecracker-workspaces")
    KERNELS_DIR = BASE_DIR / "kernels"
    ROOTFS_DIR = BASE_DIR / "rootfs"
    SANDBOXES_DIR = BASE_DIR / "sandboxes"
    SNAPSHOTS_DIR = BASE_DIR / "snapshots"
    
    FIRECRACKER_BIN = "/usr/bin/firecracker"
    JAILER_BIN = "/usr/bin/jailer"
    
    def __init__(self):
        self._ensure_directories()
        self._active_sandboxes: Dict[str, SandboxConfig] = {}
        self._vsock_clients: Dict[str, VsockClient] = {}
        self._next_vsock_cid = 3  # CID 0, 1, 2 are reserved
        self._load_existing_sandboxes()
    
    def _ensure_directories(self):
        """Create required directories if they don't exist."""
        for dir_path in [self.KERNELS_DIR, self.ROOTFS_DIR, 
                         self.SANDBOXES_DIR, self.SNAPSHOTS_DIR]:
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
        subprocess.run([
            "cp", "--reflink=auto", "--sparse=always",
            str(base_rootfs), str(overlay_rootfs)
        ], check=True)
        
        return overlay_rootfs
    
    def _allocate_vsock_cid(self) -> int:
        """Allocate a unique vsock CID for the sandbox."""
        cid = self._next_vsock_cid
        self._next_vsock_cid += 1
        return cid
    
    def _call_firecracker_api(self, sandbox_id: str, method: str, 
                               endpoint: str, data: dict = None) -> dict:
        """Call the Firecracker API via unix socket using curl."""
        socket_path = self._get_socket_path(sandbox_id)
        
        cmd = ["curl", "-s", "--unix-socket", str(socket_path)]
        
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
            raise Exception(f"Firecracker API error: {result.stderr}")
        
        if result.stdout:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {}
        return {}
    
    async def create_sandbox(self, template: str, memory_mb: int, 
                            vcpu_count: int, workspace_id: Optional[str] = None) -> SandboxConfig:
        """Create and start a new sandbox."""
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
            cwd=str(sandbox_dir)
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
            self._call_firecracker_api(sandbox_id, "PUT", "/machine-config", {
                "vcpu_count": vcpu_count,
                "mem_size_mib": memory_mb,
                "smt": False
            })
            
            # 2. Set boot source
            self._call_firecracker_api(sandbox_id, "PUT", "/boot-source", {
                "kernel_image_path": str(kernel_path),
                "boot_args": "console=ttyS0 reboot=k panic=1 pci=off init=/sbin/init"
            })
            
            # 3. Set root drive
            self._call_firecracker_api(sandbox_id, "PUT", "/drives/rootfs", {
                "drive_id": "rootfs",
                "path_on_host": str(rootfs_path),
                "is_root_device": True,
                "is_read_only": False
            })
            
            # 4. Set vsock device for host-guest communication
            self._call_firecracker_api(sandbox_id, "PUT", "/vsock", {
                "vsock_id": "vsock0",
                "guest_cid": vsock_cid,
                "uds_path": str(vsock_path)
            })
            
            # 5. Start the VM
            self._call_firecracker_api(sandbox_id, "PUT", "/actions", {
                "action_type": "InstanceStart"
            })
            
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
            firecracker_pid=firecracker_proc.pid
        )
        
        # Save state
        state_file = sandbox_dir / "state.json"
        state_file.write_text(json.dumps(asdict(config)))
        
        self._active_sandboxes[sandbox_id] = config
        
        # Create vsock client
        client = VsockClient(vsock_cid)
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
            self._call_firecracker_api(sandbox_id, "PUT", "/actions", {
                "action_type": "SendCtrlAltDel"
            })
            await asyncio.sleep(1)
        except:
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
        self._call_firecracker_api(sandbox_id, "PATCH", "/vm", {
            "state": "Paused"
        })
        
        # Create snapshot via Firecracker API
        self._call_firecracker_api(sandbox_id, "PUT", "/snapshot/create", {
            "snapshot_type": "Full",
            "snapshot_path": str(snapshot_dir / "snapshot"),
            "mem_file_path": str(snapshot_dir / "memory")
        })
        
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
            cwd=str(sandbox_dir)
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
        self._call_firecracker_api(sandbox_id, "PUT", "/snapshot/load", {
            "snapshot_path": str(snapshot_dir / "snapshot"),
            "mem_backend": {
                "backend_type": "File",
                "backend_path": str(snapshot_dir / "memory")
            },
            "enable_diff_snapshots": False,
            "resume_vm": True
        })
        
        # Update state
        config.status = "running"
        config.firecracker_pid = firecracker_proc.pid
        state_file = sandbox_dir / "state.json"
        state_file.write_text(json.dumps(asdict(config)))
        
        # Reconnect vsock client
        if config.vsock_cid:
            client = VsockClient(config.vsock_cid)
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
            if not config or not config.vsock_cid:
                raise ValueError(f"Sandbox not found or no vsock CID: {sandbox_id}")
            client = VsockClient(config.vsock_cid)
            client.connect()
            self._vsock_clients[sandbox_id] = client
        return self._vsock_clients[sandbox_id]
    
    async def exec_command(self, sandbox_id: str, command: str, 
                          timeout: int = 300, working_dir: str = "/workspace") -> dict:
        """Execute a command in the sandbox."""
        client = self._get_vsock_client(sandbox_id)
        return client.exec_command(command, timeout, working_dir)
    
    async def read_file(self, sandbox_id: str, path: str) -> dict:
        """Read a file from the sandbox."""
        client = self._get_vsock_client(sandbox_id)
        return client.read_file(path)
    
    async def write_file(self, sandbox_id: str, path: str, 
                        content: str, is_base64: bool = False) -> dict:
        """Write a file to the sandbox."""
        client = self._get_vsock_client(sandbox_id)
        return client.write_file(path, content, is_base64)
    
    async def list_files(self, sandbox_id: str, path: str = "/workspace") -> dict:
        """List files in a directory."""
        client = self._get_vsock_client(sandbox_id)
        return client.list_files(path)
