# Firecracker Workspace Service - Design Document

## Overview

This document describes a self-hosted "local E2B-like" service that provides sandboxed workspace environments using Firecracker microVMs. This follows the same architectural pattern as Manus (which uses E2B) but runs entirely on bare metal without cloud dependencies.

**Target Hardware:** Intel NUC with 16GB RAM, Linux with KVM support
**Purpose:** Provide isolated sandbox environments for AI agent code execution (CompyMac integration)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        CompyMac Agent                           │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ HTTP/REST API
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Workspace Service (Python)                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ REST API    │  │ Sandbox     │  │ Persistence Manager     │  │
│  │ (FastAPI)   │  │ Manager     │  │ (pause/resume/snapshot) │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ Unix Socket API
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Firecracker VMM Process                       │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                      MicroVM (Guest)                        ││
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌───────────────┐  ││
│  │  │ Kernel  │  │ Root FS │  │ Agent   │  │ Workspace Dir │  ││
│  │  │ (6.x)   │  │ (Alpine)│  │ (vsock) │  │ (/workspace)  │  ││
│  │  └─────────┘  └─────────┘  └─────────┘  └───────────────┘  ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ virtio-blk / virtio-net
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Host Filesystem                             │
│  /var/lib/firecracker-workspaces/                               │
│  ├── kernels/          (shared kernel images)                   │
│  ├── rootfs/           (base rootfs templates)                  │
│  ├── sandboxes/        (per-sandbox state)                      │
│  │   └── {sandbox_id}/ │
│  │       ├── rootfs.ext4    (copy-on-write overlay)            │
│  │       ├── workspace/     (mounted workspace directory)       │
│  │       ├── state.json     (sandbox metadata)                  │
│  │       └── socket.sock    (firecracker API socket)            │
│  └── snapshots/        (paused sandbox snapshots)               │
└─────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Workspace Service (Python/FastAPI)

The main service that exposes a REST API for sandbox management.

**File:** `workspace_service/main.py`

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import uuid

app = FastAPI(title="Firecracker Workspace Service")

class CreateSandboxRequest(BaseModel):
    template: str = "default"  # base image template
    memory_mb: int = 512
    vcpu_count: int = 1
    workspace_id: Optional[str] = None  # for resuming existing workspace

class SandboxResponse(BaseModel):
    sandbox_id: str
    status: str  # "running", "paused", "stopped"
    ip_address: Optional[str]
    created_at: str

class CommandRequest(BaseModel):
    command: str
    timeout_seconds: int = 300
    working_dir: str = "/workspace"

class CommandResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int

class FileRequest(BaseModel):
    path: str
    content: Optional[str] = None  # for write operations

@app.post("/sandboxes", response_model=SandboxResponse)
async def create_sandbox(request: CreateSandboxRequest):
    """Create a new sandbox or resume an existing one."""
    pass

@app.get("/sandboxes/{sandbox_id}", response_model=SandboxResponse)
async def get_sandbox(sandbox_id: str):
    """Get sandbox status."""
    pass

@app.delete("/sandboxes/{sandbox_id}")
async def destroy_sandbox(sandbox_id: str):
    """Destroy a sandbox and clean up resources."""
    pass

@app.post("/sandboxes/{sandbox_id}/pause")
async def pause_sandbox(sandbox_id: str):
    """Pause a sandbox (snapshot state for later resume)."""
    pass

@app.post("/sandboxes/{sandbox_id}/resume")
async def resume_sandbox(sandbox_id: str):
    """Resume a paused sandbox."""
    pass

@app.post("/sandboxes/{sandbox_id}/exec", response_model=CommandResponse)
async def exec_command(sandbox_id: str, request: CommandRequest):
    """Execute a command in the sandbox."""
    pass

@app.post("/sandboxes/{sandbox_id}/files/write")
async def write_file(sandbox_id: str, request: FileRequest):
    """Write a file to the sandbox filesystem."""
    pass

@app.get("/sandboxes/{sandbox_id}/files/read")
async def read_file(sandbox_id: str, path: str):
    """Read a file from the sandbox filesystem."""
    pass

@app.post("/sandboxes/{sandbox_id}/files/upload")
async def upload_file(sandbox_id: str, path: str, file: bytes):
    """Upload a file to the sandbox."""
    pass

@app.get("/sandboxes/{sandbox_id}/files/download")
async def download_file(sandbox_id: str, path: str):
    """Download a file from the sandbox."""
    pass
```

### 2. Sandbox Manager

Handles Firecracker VM lifecycle management.

**File:** `workspace_service/sandbox_manager.py`

```python
import os
import json
import subprocess
import socket
import time
import shutil
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, Dict
from datetime import datetime
import requests_unixsocket

@dataclass
class SandboxConfig:
    sandbox_id: str
    template: str
    memory_mb: int
    vcpu_count: int
    workspace_id: str
    status: str
    created_at: str
    ip_address: Optional[str] = None
    vsock_cid: Optional[int] = None

class SandboxManager:
    BASE_DIR = Path("/var/lib/firecracker-workspaces")
    KERNELS_DIR = BASE_DIR / "kernels"
    ROOTFS_DIR = BASE_DIR / "rootfs"
    SANDBOXES_DIR = BASE_DIR / "sandboxes"
    SNAPSHOTS_DIR = BASE_DIR / "snapshots"
    
    FIRECRACKER_BIN = "/usr/bin/firecracker"
    JAILER_BIN = "/usr/bin/jailer"  # optional, for production security
    
    def __init__(self):
        self._ensure_directories()
        self._active_sandboxes: Dict[str, SandboxConfig] = {}
        self._next_vsock_cid = 3  # CID 0, 1, 2 are reserved
        
    def _ensure_directories(self):
        for dir_path in [self.KERNELS_DIR, self.ROOTFS_DIR, 
                         self.SANDBOXES_DIR, self.SNAPSHOTS_DIR]:
            dir_path.mkdir(parents=True, exist_ok=True)
    
    def _get_sandbox_dir(self, sandbox_id: str) -> Path:
        return self.SANDBOXES_DIR / sandbox_id
    
    def _get_socket_path(self, sandbox_id: str) -> Path:
        return self._get_sandbox_dir(sandbox_id) / "firecracker.sock"
    
    def _get_kernel_path(self, template: str = "default") -> Path:
        return self.KERNELS_DIR / f"{template}-vmlinux.bin"
    
    def _get_base_rootfs_path(self, template: str = "default") -> Path:
        return self.ROOTFS_DIR / f"{template}-rootfs.ext4"
    
    def _create_overlay_rootfs(self, sandbox_id: str, template: str) -> Path:
        """Create a copy-on-write overlay of the base rootfs."""
        sandbox_dir = self._get_sandbox_dir(sandbox_id)
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        
        base_rootfs = self._get_base_rootfs_path(template)
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
        """Call the Firecracker API via unix socket."""
        socket_path = self._get_socket_path(sandbox_id)
        session = requests_unixsocket.Session()
        url = f"http+unix://{socket_path.as_posix().replace('/', '%2F')}{endpoint}"
        
        if method == "PUT":
            response = session.put(url, json=data)
        elif method == "GET":
            response = session.get(url)
        elif method == "PATCH":
            response = session.patch(url, json=data)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        if response.status_code >= 400:
            raise Exception(f"Firecracker API error: {response.text}")
        
        return response.json() if response.text else {}
    
    async def create_sandbox(self, template: str, memory_mb: int, 
                            vcpu_count: int, workspace_id: Optional[str] = None) -> SandboxConfig:
        """Create and start a new sandbox."""
        sandbox_id = str(uuid.uuid4())[:8]
        workspace_id = workspace_id or sandbox_id
        
        sandbox_dir = self._get_sandbox_dir(sandbox_id)
        socket_path = self._get_socket_path(sandbox_id)
        
        # Create overlay rootfs
        rootfs_path = self._create_overlay_rootfs(sandbox_id, template)
        kernel_path = self._get_kernel_path(template)
        
        # Create workspace directory
        workspace_dir = sandbox_dir / "workspace"
        workspace_dir.mkdir(exist_ok=True)
        
        # Allocate vsock CID for guest communication
        vsock_cid = self._allocate_vsock_cid()
        
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
            time.sleep(0.1)
        else:
            raise Exception("Firecracker socket not ready")
        
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
            "uds_path": str(sandbox_dir / "vsock.sock")
        })
        
        # 5. Start the VM
        self._call_firecracker_api(sandbox_id, "PUT", "/actions", {
            "action_type": "InstanceStart"
        })
        
        config = SandboxConfig(
            sandbox_id=sandbox_id,
            template=template,
            memory_mb=memory_mb,
            vcpu_count=vcpu_count,
            workspace_id=workspace_id,
            status="running",
            created_at=datetime.utcnow().isoformat(),
            vsock_cid=vsock_cid
        )
        
        # Save state
        state_file = sandbox_dir / "state.json"
        state_file.write_text(json.dumps(asdict(config)))
        
        self._active_sandboxes[sandbox_id] = config
        
        return config
    
    async def destroy_sandbox(self, sandbox_id: str):
        """Stop and clean up a sandbox."""
        sandbox_dir = self._get_sandbox_dir(sandbox_id)
        
        # Send shutdown action
        try:
            self._call_firecracker_api(sandbox_id, "PUT", "/actions", {
                "action_type": "SendCtrlAltDel"
            })
            time.sleep(1)
        except:
            pass
        
        # Kill firecracker process if still running
        # (find by socket path or stored PID)
        
        # Clean up files
        if sandbox_dir.exists():
            shutil.rmtree(sandbox_dir)
        
        if sandbox_id in self._active_sandboxes:
            del self._active_sandboxes[sandbox_id]
    
    async def pause_sandbox(self, sandbox_id: str):
        """Pause a sandbox by creating a snapshot."""
        sandbox_dir = self._get_sandbox_dir(sandbox_id)
        snapshot_dir = self.SNAPSHOTS_DIR / sandbox_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        
        # Create snapshot via Firecracker API
        self._call_firecracker_api(sandbox_id, "PUT", "/snapshot/create", {
            "snapshot_type": "Full",
            "snapshot_path": str(snapshot_dir / "snapshot"),
            "mem_file_path": str(snapshot_dir / "memory")
        })
        
        # Update state
        if sandbox_id in self._active_sandboxes:
            self._active_sandboxes[sandbox_id].status = "paused"
    
    async def resume_sandbox(self, sandbox_id: str) -> SandboxConfig:
        """Resume a paused sandbox from snapshot."""
        snapshot_dir = self.SNAPSHOTS_DIR / sandbox_id
        sandbox_dir = self._get_sandbox_dir(sandbox_id)
        socket_path = self._get_socket_path(sandbox_id)
        
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
            time.sleep(0.1)
        
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
        config = self._active_sandboxes.get(sandbox_id)
        if config:
            config.status = "running"
        
        return config
```

### 3. Guest Agent

A lightweight agent running inside the microVM that handles command execution and file operations via vsock.

**File:** `guest_agent/agent.py` (runs inside the microVM)

```python
#!/usr/bin/env python3
"""
Guest agent that runs inside the Firecracker microVM.
Communicates with the host via vsock.
"""

import socket
import json
import subprocess
import os
import sys
import base64
from pathlib import Path

VSOCK_PORT = 5000
WORKSPACE_DIR = "/workspace"

class GuestAgent:
    def __init__(self):
        self.sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        # CID 2 is the host
        self.sock.bind((socket.VMADDR_CID_ANY, VSOCK_PORT))
        self.sock.listen(1)
        print(f"Guest agent listening on vsock port {VSOCK_PORT}")
    
    def handle_command(self, request: dict) -> dict:
        """Execute a shell command."""
        cmd = request.get("command", "")
        timeout = request.get("timeout", 300)
        working_dir = request.get("working_dir", WORKSPACE_DIR)
        
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                timeout=timeout,
                cwd=working_dir,
                text=True
            )
            return {
                "success": True,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Command timed out",
                "exit_code": -1
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "exit_code": -1
            }
    
    def handle_read_file(self, request: dict) -> dict:
        """Read a file from the filesystem."""
        path = request.get("path", "")
        try:
            with open(path, "rb") as f:
                content = base64.b64encode(f.read()).decode()
            return {"success": True, "content": content}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def handle_write_file(self, request: dict) -> dict:
        """Write a file to the filesystem."""
        path = request.get("path", "")
        content = request.get("content", "")
        is_base64 = request.get("is_base64", False)
        
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            mode = "wb" if is_base64 else "w"
            with open(path, mode) as f:
                if is_base64:
                    f.write(base64.b64decode(content))
                else:
                    f.write(content)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def handle_list_files(self, request: dict) -> dict:
        """List files in a directory."""
        path = request.get("path", WORKSPACE_DIR)
        try:
            entries = []
            for entry in Path(path).iterdir():
                entries.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else 0
                })
            return {"success": True, "entries": entries}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def handle_request(self, data: bytes) -> bytes:
        """Route request to appropriate handler."""
        try:
            request = json.loads(data.decode())
            action = request.get("action", "")
            
            handlers = {
                "exec": self.handle_command,
                "read_file": self.handle_read_file,
                "write_file": self.handle_write_file,
                "list_files": self.handle_list_files,
            }
            
            handler = handlers.get(action)
            if handler:
                response = handler(request)
            else:
                response = {"success": False, "error": f"Unknown action: {action}"}
            
            return json.dumps(response).encode()
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}).encode()
    
    def run(self):
        """Main loop accepting connections."""
        while True:
            conn, addr = self.sock.accept()
            print(f"Connection from {addr}")
            try:
                while True:
                    # Read length-prefixed message
                    length_bytes = conn.recv(4)
                    if not length_bytes:
                        break
                    length = int.from_bytes(length_bytes, "big")
                    data = conn.recv(length)
                    
                    response = self.handle_request(data)
                    
                    # Send length-prefixed response
                    conn.send(len(response).to_bytes(4, "big"))
                    conn.send(response)
            except Exception as e:
                print(f"Connection error: {e}")
            finally:
                conn.close()

if __name__ == "__main__":
    agent = GuestAgent()
    agent.run()
```

### 4. Host-side vsock Client

Communicates with the guest agent from the host.

**File:** `workspace_service/vsock_client.py`

```python
import socket
import json
from typing import Optional

class VsockClient:
    """Client for communicating with guest agent via vsock."""
    
    VSOCK_PORT = 5000
    
    def __init__(self, guest_cid: int):
        self.guest_cid = guest_cid
        self.sock = None
    
    def connect(self):
        """Connect to the guest agent."""
        self.sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        self.sock.connect((self.guest_cid, self.VSOCK_PORT))
    
    def disconnect(self):
        """Disconnect from the guest agent."""
        if self.sock:
            self.sock.close()
            self.sock = None
    
    def _send_request(self, request: dict) -> dict:
        """Send a request and receive response."""
        if not self.sock:
            self.connect()
        
        data = json.dumps(request).encode()
        
        # Send length-prefixed message
        self.sock.send(len(data).to_bytes(4, "big"))
        self.sock.send(data)
        
        # Receive length-prefixed response
        length_bytes = self.sock.recv(4)
        length = int.from_bytes(length_bytes, "big")
        response_data = self.sock.recv(length)
        
        return json.loads(response_data.decode())
    
    def exec_command(self, command: str, timeout: int = 300, 
                     working_dir: str = "/workspace") -> dict:
        """Execute a command in the guest."""
        return self._send_request({
            "action": "exec",
            "command": command,
            "timeout": timeout,
            "working_dir": working_dir
        })
    
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
```

## Setup Instructions

### Prerequisites

1. **Linux host with KVM support**
   ```bash
   # Check KVM availability
   [ -r /dev/kvm ] && [ -w /dev/kvm ] && echo "OK" || echo "FAIL"
   
   # If not accessible, set ACL for your user
   sudo apt install acl
   sudo setfacl -m u:${USER}:rw /dev/kvm
   ```

2. **Enable virtualization in BIOS**
   - Intel: Enable VT-x
   - AMD: Enable AMD-V

### Installation

1. **Install Firecracker**
   ```bash
   ARCH="$(uname -m)"
   RELEASE_URL="https://github.com/firecracker-microvm/firecracker/releases"
   LATEST=$(basename $(curl -fsSLI -o /dev/null -w %{url_effective} ${RELEASE_URL}/latest))
   
   curl -L ${RELEASE_URL}/download/${LATEST}/firecracker-${LATEST}-${ARCH}.tgz | tar -xz
   sudo mv release-${LATEST}-${ARCH}/firecracker-${LATEST}-${ARCH} /usr/bin/firecracker
   sudo mv release-${LATEST}-${ARCH}/jailer-${LATEST}-${ARCH} /usr/bin/jailer
   sudo chmod +x /usr/bin/firecracker /usr/bin/jailer
   ```

2. **Create directory structure**
   ```bash
   sudo mkdir -p /var/lib/firecracker-workspaces/{kernels,rootfs,sandboxes,snapshots}
   sudo chown -R $USER:$USER /var/lib/firecracker-workspaces
   ```

3. **Download or build kernel**
   ```bash
   # Option A: Download pre-built kernel
   curl -fsSL -o /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin \
     https://s3.amazonaws.com/spec.ccfc.min/img/quickstart_guide/x86_64/kernels/vmlinux.bin
   
   # Option B: Build custom kernel (see Appendix A)
   ```

4. **Create base rootfs**
   ```bash
   # See Appendix B for creating a custom rootfs with the guest agent
   ```

5. **Install Python dependencies**
   ```bash
   pip install fastapi uvicorn requests-unixsocket pydantic
   ```

6. **Start the service**
   ```bash
   cd workspace_service
   uvicorn main:app --host 0.0.0.0 --port 8080
   ```

## API Reference

### Create Sandbox
```http
POST /sandboxes
Content-Type: application/json

{
  "template": "default",
  "memory_mb": 512,
  "vcpu_count": 1,
  "workspace_id": "optional-existing-workspace-id"
}
```

### Get Sandbox Status
```http
GET /sandboxes/{sandbox_id}
```

### Execute Command
```http
POST /sandboxes/{sandbox_id}/exec
Content-Type: application/json

{
  "command": "ls -la /workspace",
  "timeout_seconds": 300,
  "working_dir": "/workspace"
}
```

### Write File
```http
POST /sandboxes/{sandbox_id}/files/write
Content-Type: application/json

{
  "path": "/workspace/hello.py",
  "content": "print('Hello, World!')"
}
```

### Read File
```http
GET /sandboxes/{sandbox_id}/files/read?path=/workspace/hello.py
```

### Pause Sandbox
```http
POST /sandboxes/{sandbox_id}/pause
```

### Resume Sandbox
```http
POST /sandboxes/{sandbox_id}/resume
```

### Destroy Sandbox
```http
DELETE /sandboxes/{sandbox_id}
```

## CompyMac Integration

To integrate with CompyMac, create a workspace provider that wraps the REST API:

**File:** `compymac_integration/workspace_provider.py`

```python
import httpx
from typing import Optional

class FirecrackerWorkspaceProvider:
    """CompyMac workspace provider using Firecracker sandboxes."""
    
    def __init__(self, service_url: str = "http://localhost:8080"):
        self.service_url = service_url
        self.client = httpx.AsyncClient(base_url=service_url)
        self.current_sandbox_id: Optional[str] = None
    
    async def create_workspace(self, workspace_id: Optional[str] = None) -> str:
        """Create a new workspace sandbox."""
        response = await self.client.post("/sandboxes", json={
            "template": "default",
            "memory_mb": 512,
            "vcpu_count": 1,
            "workspace_id": workspace_id
        })
        response.raise_for_status()
        data = response.json()
        self.current_sandbox_id = data["sandbox_id"]
        return self.current_sandbox_id
    
    async def run_command(self, command: str, timeout: int = 300) -> dict:
        """Execute a command in the current workspace."""
        if not self.current_sandbox_id:
            raise RuntimeError("No active workspace")
        
        response = await self.client.post(
            f"/sandboxes/{self.current_sandbox_id}/exec",
            json={"command": command, "timeout_seconds": timeout}
        )
        response.raise_for_status()
        return response.json()
    
    async def write_file(self, path: str, content: str) -> None:
        """Write a file to the workspace."""
        if not self.current_sandbox_id:
            raise RuntimeError("No active workspace")
        
        response = await self.client.post(
            f"/sandboxes/{self.current_sandbox_id}/files/write",
            json={"path": path, "content": content}
        )
        response.raise_for_status()
    
    async def read_file(self, path: str) -> str:
        """Read a file from the workspace."""
        if not self.current_sandbox_id:
            raise RuntimeError("No active workspace")
        
        response = await self.client.get(
            f"/sandboxes/{self.current_sandbox_id}/files/read",
            params={"path": path}
        )
        response.raise_for_status()
        return response.json()["content"]
    
    async def pause_workspace(self) -> None:
        """Pause the current workspace for later resume."""
        if not self.current_sandbox_id:
            raise RuntimeError("No active workspace")
        
        response = await self.client.post(
            f"/sandboxes/{self.current_sandbox_id}/pause"
        )
        response.raise_for_status()
    
    async def resume_workspace(self, sandbox_id: str) -> None:
        """Resume a paused workspace."""
        response = await self.client.post(
            f"/sandboxes/{sandbox_id}/resume"
        )
        response.raise_for_status()
        self.current_sandbox_id = sandbox_id
    
    async def destroy_workspace(self) -> None:
        """Destroy the current workspace."""
        if not self.current_sandbox_id:
            return
        
        response = await self.client.delete(
            f"/sandboxes/{self.current_sandbox_id}"
        )
        response.raise_for_status()
        self.current_sandbox_id = None
```

## Appendix A: Building a Custom Kernel

```bash
# Install build dependencies
sudo apt install -y git build-essential flex bison libncurses5-dev \
  libssl-dev gcc bc libelf-dev pahole

# Clone kernel source
git clone --depth=1 -b linux-6.6.y \
  git://git.kernel.org/pub/scm/linux/kernel/git/stable/linux-stable.git
cd linux-stable

# Download Firecracker kernel config
curl -fsSL -o .config \
  https://raw.githubusercontent.com/firecracker-microvm/firecracker/main/resources/guest_configs/microvm-kernel-ci-x86_64-6.1.config

# Build kernel
yes '' | make vmlinux -j$(nproc)

# Copy to kernels directory
cp vmlinux /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin
```

## Appendix B: Creating a Custom Rootfs with Guest Agent

```bash
# Create a 2GB sparse file
dd if=/dev/zero of=/tmp/rootfs.ext4 bs=1M count=2048
mkfs.ext4 /tmp/rootfs.ext4

# Mount it
sudo mkdir -p /mnt/rootfs
sudo mount /tmp/rootfs.ext4 /mnt/rootfs

# Install Alpine Linux base
sudo apk -X https://dl-cdn.alpinelinux.org/alpine/v3.19/main \
  -U --allow-untrusted --root /mnt/rootfs --initdb add \
  alpine-base python3 py3-pip openssh bash curl wget git

# Copy guest agent
sudo mkdir -p /mnt/rootfs/opt/agent
sudo cp guest_agent/agent.py /mnt/rootfs/opt/agent/

# Create init script to start agent
sudo tee /mnt/rootfs/etc/init.d/guest-agent << 'EOF'
#!/sbin/openrc-run
command="/usr/bin/python3"
command_args="/opt/agent/agent.py"
command_background=true
pidfile="/run/guest-agent.pid"
EOF
sudo chmod +x /mnt/rootfs/etc/init.d/guest-agent

# Enable agent on boot
sudo ln -s /etc/init.d/guest-agent /mnt/rootfs/etc/runlevels/default/

# Create workspace directory
sudo mkdir -p /mnt/rootfs/workspace

# Set root password (for debugging)
echo 'root:root' | sudo chpasswd -R /mnt/rootfs

# Unmount
sudo umount /mnt/rootfs

# Move to rootfs directory
mv /tmp/rootfs.ext4 /var/lib/firecracker-workspaces/rootfs/default-rootfs.ext4
```

## Appendix C: Network Configuration (Optional)

For sandboxes that need network access:

```bash
# Create a TAP device
sudo ip tuntap add tap0 mode tap
sudo ip addr add 172.16.0.1/24 dev tap0
sudo ip link set tap0 up

# Enable IP forwarding
sudo sysctl -w net.ipv4.ip_forward=1

# NAT for internet access
sudo iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
sudo iptables -A FORWARD -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
sudo iptables -A FORWARD -i tap0 -o eth0 -j ACCEPT
```

Then add network configuration to the Firecracker API call:

```python
self._call_firecracker_api(sandbox_id, "PUT", "/network-interfaces/eth0", {
    "iface_id": "eth0",
    "guest_mac": "AA:FC:00:00:00:01",
    "host_dev_name": "tap0"
})
```

## Performance Expectations

Based on Firecracker specifications and real-world testing:

| Metric | Expected Value | Notes |
|--------|----------------|-------|
| VM boot time | <125ms | Minimal kernel, warm cache |
| First command execution | 200-500ms | Includes boot + agent ready |
| Memory overhead per VM | <5MB | Firecracker process only |
| Guest RAM | Configurable | Default 512MB |
| Concurrent VMs (16GB host) | 10-20 | Depends on guest RAM allocation |

## Security Considerations

1. **Use the Jailer** in production for additional isolation
2. **Limit network access** per sandbox as needed
3. **Set resource limits** (CPU, memory, disk I/O)
4. **Rotate/clean up old sandboxes** to prevent resource exhaustion
5. **Don't run as root** - use ACLs for /dev/kvm access

## Future Enhancements

1. **Template management** - Pre-built images with common tools
2. **Snapshot storage** - S3/MinIO for persistent snapshots
3. **Metrics/monitoring** - Prometheus metrics endpoint
4. **Multi-node support** - Distribute sandboxes across multiple hosts
5. **Browser support** - Chromium in sandbox for web testing
