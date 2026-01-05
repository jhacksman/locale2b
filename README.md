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

- **CPU**: x86_64 or aarch64 with virtualization support (Intel VT-x, AMD-V, or Apple Silicon)
- **RAM**: Minimum 4GB, recommended 16GB+ for multiple sandboxes
- **Storage**: SSD recommended for fast rootfs operations
- **OS**: Linux with KVM support (kernel 4.14+)

### Supported Platforms

| Platform | Status | Quick Start Guide |
|----------|--------|-------------------|
| x86_64 Linux (Intel/AMD) | ✅ Fully Supported | [Standard Setup](#quick-start) |
| aarch64 Linux (ARM64) | ✅ Fully Supported | [Standard Setup](#quick-start) |
| **Apple Silicon Mac Mini** | ✅ **Supported via Asahi Linux** | **[→ QUICKSTART_ASAHI.md](QUICKSTART_ASAHI.md)** |
| Raspberry Pi 4 (ARM64) | ⚠️ Untested | Should work with KVM-enabled kernel |
| AWS Graviton (ARM64) | ✅ Supported | Standard Setup |

**Running on Apple Silicon?** See the dedicated [Asahi Linux Setup Guide](ASAHI_SETUP_GUIDE.md) for detailed instructions.

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

### 2. Create Ultimate All-in-One Rootfs Image

```bash
# Create the comprehensive rootfs with all runtimes and tools (requires root)
# This takes ~15-20 minutes but you only do it once
sudo ./scripts/create-rootfs.sh
```

This creates a 3GB rootfs image with:
- **Languages**: Python 3.11, Node.js 20, Go 1.21, Rust, Ruby 3.2
- **Tools**: Git, Docker CLI, database clients, build tools
- **Network**: DHCP enabled by default for internet access
- **Pre-installed packages**: pip, npm, yarn, cargo, bundler, and common libraries

### 3. Set Up Networking (Optional but Recommended)

```bash
# Set up bridge and NAT for VM internet access (requires root)
sudo ./scripts/setup-network.sh
```

This enables VMs to:
- Get IP addresses via DHCP
- Access the internet (pip install, git clone, npm install, etc.)
- Communicate with each other

**Note**: Without this, VMs will boot but won't have network connectivity.

### 4. Start the Service

**Option A: Run as a systemd service (recommended for production)**

```bash
# Install and start the systemd service
sudo ./systemd/install-service.sh

# View logs
sudo journalctl -u locale2b -f
```

The systemd service uses Linux capabilities (`CAP_NET_ADMIN`) to manage TAP devices without requiring root privileges or sudo configuration.

**Option B: Run manually (for development)**

```bash
# Activate virtual environment
source .venv/bin/activate

# Start the service (requires CAP_NET_ADMIN for networking)
# Either run as root, or use: sudo setcap cap_net_admin+ep .venv/bin/python
uvicorn workspace_service.main:app --host 0.0.0.0 --port 8080
```

### 5. Test

```bash
# Run test script
./scripts/test-sandbox.sh

# Or test manually:
curl -X POST http://localhost:8080/sandboxes \
  -H "Content-Type: application/json" \
  -d '{"memory_mb": 512, "vcpu_count": 1}'

# Note the sandbox_id from response, then test network:
curl -X POST http://localhost:8080/sandboxes/{SANDBOX_ID}/exec \
  -H "Content-Type: application/json" \
  -d '{"command": "ping -c 3 8.8.8.8"}'

# Should show successful pings if networking is configured
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

## Security Configuration

**Important:** By default, the API accepts requests without authentication. For production deployments, you should enable API key authentication.

### Enabling API Key Authentication (Recommended)

The systemd service is the preferred way to run locale2b in production. To enable API key authentication:

**1. Edit the systemd service file:**

```bash
sudo systemctl edit locale2b --full
```

**2. Add these environment variables under `[Service]`:**

```ini
[Service]
# ... existing configuration ...
Environment="API_KEY_ENABLED=true"
Environment="API_KEYS=your-secret-api-key-here"
```

You can specify multiple API keys (comma-separated):
```ini
Environment="API_KEYS=key1,key2,key3"
```

**3. Reload and restart the service:**

```bash
sudo systemctl daemon-reload
sudo systemctl restart locale2b
```

**4. Verify authentication is working:**

```bash
# This should fail with 401 Unauthorized
curl -s http://localhost:8080/sandboxes

# This should succeed
curl -s -X POST http://localhost:8080/sandboxes \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"memory_mb": 512}'
```

### Security Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY_ENABLED` | `false` | Set to `true` to require API key authentication |
| `API_KEYS` | (empty) | Comma-separated list of valid API keys |
| `API_KEY_HEADER` | `X-API-Key` | HTTP header name for the API key |
| `RATE_LIMIT_ENABLED` | `true` | Enable rate limiting (100 req/min default) |
| `RATE_LIMIT_REQUESTS` | `100` | Max requests per time window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit time window |
| `MAX_REQUEST_SIZE_BYTES` | `10485760` | Max request body size (10MB) |

### Additional Security Best Practices

1. **Use the Jailer** in production for additional isolation
2. **Limit network access** per sandbox as needed
3. **Set resource limits** (CPU, memory, disk I/O)
4. **Rotate/clean up old sandboxes** to prevent resource exhaustion
5. **Don't run as root** - use ACLs for /dev/kvm access
6. **Use a firewall** to restrict access to port 8080
7. **Use HTTPS** with a reverse proxy (nginx/caddy) for encrypted connections

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
