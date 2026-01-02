# Firecracker Workspace Service

A self-hosted "local E2B-like" service that provides sandboxed workspace environments using Firecracker microVMs. This follows the same architectural pattern as Manus (which uses E2B) but runs entirely on bare metal without cloud dependencies.

## Overview

This service provides:
- **Isolated sandbox environments** using Firecracker microVMs
- **Fast startup** (<125ms VM boot time)
- **Low overhead** (<5MB memory per VM process)
- **Pause/Resume** capability with full state persistence
- **REST API** for easy integration with AI agents
- **File operations** (read, write, upload, download)
- **Command execution** with timeout support

## Hardware Requirements

- **CPU**: x86_64 or aarch64 with virtualization support (Intel VT-x or AMD-V)
- **RAM**: Minimum 4GB, recommended 16GB+ for multiple sandboxes
- **Storage**: SSD recommended for fast rootfs operations
- **OS**: Linux with KVM support (kernel 4.14+)

### Capacity Planning (16GB RAM host)

| Guest RAM | Max Concurrent VMs | Notes |
|-----------|-------------------|-------|
| 256MB | ~40 | Minimal workloads |
| 512MB | ~20 | Light development |
| 1GB | ~10 | Standard development |
| 2GB | ~5 | Heavy workloads |

## Quick Start

### 1. Setup

```bash
# Clone or extract this package
cd firecracker-workspace-service

# Run setup script (installs Firecracker, creates directories)
chmod +x scripts/setup.sh
./scripts/setup.sh
```

### 2. Create Rootfs Image

```bash
# Create the base rootfs with guest agent (requires root)
sudo ./scripts/create-rootfs.sh
```

### 3. Start the Service

```bash
# Activate virtual environment
source .venv/bin/activate

# Start the service
uvicorn workspace_service.main:app --host 0.0.0.0 --port 8080
```

### 4. Test

```bash
# Run test script
./scripts/test-sandbox.sh
```

## API Reference

### Health Check
```http
GET /health
```

### Create Sandbox
```http
POST /sandboxes
Content-Type: application/json

{
  "template": "default",
  "memory_mb": 512,
  "vcpu_count": 1,
  "workspace_id": "optional-id-for-persistence"
}
```

### Execute Command
```http
POST /sandboxes/{sandbox_id}/exec
Content-Type: application/json

{
  "command": "python3 script.py",
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
  "content": "print('Hello!')"
}
```

### Read File
```http
GET /sandboxes/{sandbox_id}/files/read?path=/workspace/hello.py
```

### List Files
```http
GET /sandboxes/{sandbox_id}/files/list?path=/workspace
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

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        AI Agent (CompyMac)                       │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ HTTP/REST API
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Workspace Service (Python/FastAPI)             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ REST API    │  │ Sandbox     │  │ Persistence Manager     │  │
│  │ (FastAPI)   │  │ Manager     │  │ (pause/resume/snapshot) │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ vsock (virtio socket)
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Firecracker MicroVM                           │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                      Guest (Alpine Linux)                   ││
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌───────────────┐  ││
│  │  │ Kernel  │  │ Root FS │  │ Guest   │  │ Workspace Dir │  ││
│  │  │ (6.x)   │  │ (ext4)  │  │ Agent   │  │ (/workspace)  │  ││
│  │  └─────────┘  └─────────┘  └─────────┘  └───────────────┘  ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
/var/lib/firecracker-workspaces/
├── kernels/                    # Shared kernel images
│   └── default-vmlinux.bin
├── rootfs/                     # Base rootfs templates
│   └── default-rootfs.ext4
├── sandboxes/                  # Per-sandbox state
│   └── {sandbox_id}/
│       ├── rootfs.ext4         # Copy-on-write overlay
│       ├── state.json          # Sandbox metadata
│       ├── firecracker.sock    # Firecracker API socket
│       └── vsock.sock          # Guest communication socket
└── snapshots/                  # Paused sandbox snapshots
    └── {sandbox_id}/
        ├── snapshot            # VM state snapshot
        └── memory              # Memory snapshot
```

## CompyMac Integration

See `compymac_integration/workspace_provider.py` for a ready-to-use provider class that wraps the REST API for CompyMac.

```python
from compymac_integration.workspace_provider import FirecrackerWorkspaceProvider

provider = FirecrackerWorkspaceProvider("http://localhost:8080")

# Create workspace
sandbox_id = await provider.create_workspace()

# Run commands
result = await provider.run_command("pip install requests")

# Write files
await provider.write_file("/workspace/main.py", "print('hello')")

# Pause for later
await provider.pause_workspace()

# Resume
await provider.resume_workspace(sandbox_id)

# Cleanup
await provider.destroy_workspace()
```

## Security Considerations

1. **Use the Jailer** in production for additional isolation
2. **Limit network access** per sandbox as needed
3. **Set resource limits** (CPU, memory, disk I/O)
4. **Rotate/clean up old sandboxes** to prevent resource exhaustion
5. **Don't run as root** - use ACLs for /dev/kvm access

## Troubleshooting

### KVM not accessible
```bash
# Check if KVM is available
ls -la /dev/kvm

# Set ACL for your user
sudo setfacl -m u:${USER}:rw /dev/kvm
```

### Firecracker fails to start
```bash
# Check if virtualization is enabled in BIOS
grep -E 'vmx|svm' /proc/cpuinfo

# Check kernel support
lsmod | grep kvm
```

### Guest agent not responding
```bash
# Check if vsock module is loaded
lsmod | grep vsock

# Load vsock modules
sudo modprobe vhost_vsock
```

## License

MIT License
