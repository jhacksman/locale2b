# locale2b Development Phases

A comprehensive phased development plan for building a production-ready local E2B-like service using Firecracker microVMs.

## Overview

This document outlines the development phases for locale2b, a self-hosted sandbox service that provides isolated workspace environments for AI agent code execution. The service follows the same architectural pattern as cloud-based solutions like E2B but runs entirely on bare metal without cloud dependencies.

**Target Hardware:** Intel NUC with 16GB RAM, Linux with KVM support  
**Primary Integration:** CompyMac AI Agent  
**Architecture:** FastAPI REST API + Firecracker microVMs + vsock guest agent

---

## CompyMac Integration Analysis

This section documents the integration requirements between locale2b and CompyMac, identifying gaps and priorities based on CompyMac's existing execution model.

### CompyMac's Existing Capabilities

CompyMac's `LocalHarness` (in `src/compymac/local_harness.py`) already provides rich execution capabilities that run directly on the host machine:

| Capability | CompyMac Implementation |
|------------|------------------------|
| Shell execution | `bash` tool with `subprocess.run()`, supports `exec_dir`, `bash_id`, `timeout`, `run_in_background` |
| Background processes | PTY-based sessions with `bash_output` for polling, `write_to_shell` for input, `kill_shell` for termination |
| File operations | `Read`, `Write`, `Edit` tools with truncation and envelope handling |
| Search | `grep` (ripgrep-based), `glob` for file patterns |
| Browser | Full Playwright integration: `browser_navigate`, `browser_view`, `browser_click`, `browser_type`, etc. |
| Git operations | `git_view_pr`, `git_create_pr`, `git_pr_checks`, local git commands |
| Web search | `web_search`, `web_get_contents` via Exa API |
| LSP | `lsp_tool` for code intelligence |
| Metacognition | `think` tool, temptation awareness, evidence-based gating |

### locale2b's Value Proposition

locale2b provides **isolation** that CompyMac's local execution cannot:

1. **Security isolation** - Untrusted code runs in a VM and cannot escape to the host
2. **Clean environments** - Fresh VM per task, no state pollution between runs
3. **Resource isolation** - VM cannot consume all host resources
4. **Reproducibility** - Identical environment for every execution

### Tool Contract Compatibility Gap

For CompyMac to use locale2b as an execution backend, locale2b must match CompyMac's harness tool contract. Current status:

| CompyMac Tool | locale2b Status | Gap |
|---------------|-----------------|-----|
| `bash` (foreground) | Partial | Missing `bash_id` tracking, no streaming |
| `bash` (background with `run_in_background=True`) | **Missing** | No background process support |
| `bash_output` (poll background shell) | **Missing** | No streaming/polling |
| `write_to_shell` (send input to process) | **Missing** | No interactive input |
| `kill_shell` (terminate background process) | **Missing** | No process management |
| `Read` / `Write` / file ops | Present | Via guest agent |
| `grep` / `glob` | **Missing** | Could run via exec, but no native support |
| Browser tools | N/A | Runs on host, not in VM |

### Critical Gaps (Blocking Integration)

#### 1. Process Model Parity (HIGHEST PRIORITY)

CompyMac's execution model relies on:
- **Background shell sessions** keyed by `bash_id`
- **Streaming/polling output** via `bash_output`
- **Interactive input** via `write_to_shell`
- **Process lifecycle management** via `kill_shell`

locale2b's current guest agent uses `subprocess.run(..., capture_output=True)` which:
- Blocks until command completes
- Cannot stream output incrementally
- Cannot accept input after start
- Cannot be cancelled mid-execution

**Required Changes:**
- Guest agent must support PTY-based execution
- New action types: `exec_start`, `exec_poll`, `exec_input`, `exec_kill`
- Session tracking by `bash_id` in guest agent
- WebSocket or long-polling for streaming output to host

#### 2. Controlled Networking (BLOCKING for real tasks)

CompyMac locally has full network access for:
- Package installation (`pip install`, `npm install`, `apt-get`)
- Repository cloning (`git clone`)
- API calls during tests

locale2b currently has **no network access** (Firecracker default). This makes the VM unusable for most real development tasks.

**Required Changes:**
- Configure Firecracker tap device for network
- Implement egress allowlist (apt/pip/npm registries, git hosts)
- DNS controls and optional transparent proxy
- Network policy configuration per sandbox

#### 3. Environment Templates + Fast Start

CompyMac's local harness runs in whatever environment exists on the host. locale2b needs:
- **Pre-built templates** (Python, Node.js, Go, Rust toolchains)
- **Package caches** (pip, npm) baked into templates
- **Warm pool** or fork-from-snapshot for <2s sandbox creation
- **Template versioning** and registry

Without this, every task wastes time bootstrapping the environment.

### Secondary Gaps (Important but not blocking)

#### 4. Observability for Trace Integration

CompyMac has sophisticated tracing (`TraceStore`, `TraceContext`, evidence-based gating). locale2b should expose:
- Structured events (command start/stop, exit codes)
- Streaming output chunks with timestamps
- Resource usage snapshots (CPU, memory, disk)
- Stable workspace ID for trace correlation across pause/resume

#### 5. TTL and Cleanup Policies

- Per-sandbox TTL (time-to-live)
- Idle timeout for unused sandboxes
- Automatic cleanup on expiration
- Per-tenant quotas (future)

### Architecture Decision: Integration Approach

Two options for CompyMac integration:

**Option A: New Harness Implementation**
- Create `RemoteHarness` or `FirecrackerHarness` in CompyMac
- Implements same tool interface as `LocalHarness`
- Translates tool calls to locale2b REST API
- Handles streaming via WebSocket

**Option B: Adapter in locale2b**
- Keep `compymac_integration/workspace_provider.py`
- Expose CompyMac-compatible tool names directly
- locale2b REST API mirrors CompyMac tool schemas

**Recommendation:** Option A (new harness in CompyMac) is cleaner separation of concerns. locale2b provides a general-purpose sandbox API; CompyMac adapts it to its tool contract.

### Revised Phase Priorities

Based on this analysis, the implementation priority should be:

1. **Phase 4.1 (NEW):** Process Model Parity - Background shells, streaming, interactive input
2. **Phase 8.1 (NEW):** Controlled Networking - Egress allowlist, DNS controls
3. **Phase 2.1 (NEW):** Template Pipeline - Pre-built images, warm pool, fast start
4. **Phase 7.1 (NEW):** Observability - Structured events, trace integration
5. **Phase 9:** CompyMac Integration - RemoteHarness implementation

### What locale2b Does NOT Need

Given CompyMac's existing capabilities:
- **Browser in VM** - Not needed unless full "computer use" isolation is required. Browser can remain on host.
- **LSP in VM** - Can run via exec if needed
- **Web search** - Handled by CompyMac directly

---

## Implementation Status

### Completed Phases
- [x] Phase 1: Requirements & Interface Design (API contracts defined)
- [x] Phase 2: Host Environment & Artifacts (scripts exist, need KVM testing)
- [x] Phase 3: Core MicroVM Lifecycle (create/destroy implemented)
- [x] Phase 4: Host-Guest Communication (vsock + guest agent working)
- [x] Phase 5: REST API Surface (FastAPI with all endpoints)
- [x] Phase 6: Persistence & Pause/Resume (snapshots implemented)
- [x] Phase 7: Resource Governance (capacity tracking, env config)
- [x] Phase 8: Security Hardening (API auth, rate limiting, path traversal protection)

### Remaining Work
- [ ] Phase 4.1: Process Model Parity (background shells, streaming)
- [ ] Phase 8.1: Controlled Networking (egress allowlist)
- [ ] Phase 2.1: Template Pipeline (pre-built images, warm pool)
- [ ] Phase 7.1: Observability (structured events, trace integration)
- [ ] Phase 9: CompyMac Integration (RemoteHarness)

### Critical Blocker
All code is written but **untested on actual KVM hardware**. The vsock communication model was fixed based on Firecracker docs but needs real-world validation on the Intel NUC.

---

## Phase 4.1: Process Model Parity (NEW - HIGHEST PRIORITY)

**Duration:** 2-3 weeks  
**Goal:** Enable background shell sessions with streaming output and interactive input to match CompyMac's execution model.

### Deliverables

1. **Guest Agent PTY Support** (`guest_agent/agent.py`)
   - Replace `subprocess.run()` with PTY-based execution
   - New action types:
     - `exec_start`: Start a command, return session_id immediately
     - `exec_poll`: Get new output from a running session
     - `exec_input`: Send input to a running session
     - `exec_kill`: Terminate a running session
     - `exec_status`: Get session status (running/exited/exit_code)
   - Session tracking by `bash_id` (maps to CompyMac's `bash_id`)
   - Output buffering with configurable max size
   - Graceful cleanup of orphaned sessions

2. **Host-Side Streaming** (`workspace_service/sandbox_manager.py`)
   - New methods:
     - `start_command()`: Start background command, return session_id
     - `poll_output()`: Get buffered output from session
     - `send_input()`: Send input to session
     - `kill_session()`: Terminate session
   - Connection multiplexing for concurrent sessions
   - Timeout handling for long-running processes

3. **REST API Extensions** (`workspace_service/main.py`)
   - New endpoints:
     - `POST /sandboxes/{id}/exec/start` - Start background command
     - `GET /sandboxes/{id}/exec/{session_id}/output` - Poll output
     - `POST /sandboxes/{id}/exec/{session_id}/input` - Send input
     - `DELETE /sandboxes/{id}/exec/{session_id}` - Kill session
     - `GET /sandboxes/{id}/exec/{session_id}/status` - Get status
   - Optional: WebSocket endpoint for real-time streaming

4. **Backward Compatibility**
   - Keep existing `POST /sandboxes/{id}/exec` for simple blocking execution
   - Add `background: true` parameter to use new streaming mode

### Testing
- Start long-running command, poll output incrementally
- Send input to interactive process (e.g., Python REPL)
- Kill running process, verify cleanup
- Multiple concurrent sessions per sandbox
- Session cleanup on sandbox destroy

### Exit Criteria
- CompyMac's `bash`, `bash_output`, `write_to_shell`, `kill_shell` patterns work via locale2b
- No output loss during streaming
- Proper cleanup on all termination paths

---

## Phase 8.1: Controlled Networking (NEW - BLOCKING)

**Duration:** 2-3 weeks  
**Goal:** Enable network access with security controls for package installation and git operations.

### Deliverables

1. **Firecracker Network Configuration**
   - Configure tap device for VM network
   - NAT setup on host for outbound traffic
   - DHCP or static IP assignment in guest
   - DNS configuration in guest

2. **Egress Allowlist**
   - Configurable allowlist of permitted destinations:
     - Package registries: `pypi.org`, `registry.npmjs.org`, `dl-cdn.alpinelinux.org`
     - Git hosts: `github.com`, `gitlab.com`, `bitbucket.org`
     - Custom allowlist via environment variable
   - iptables rules on host to enforce allowlist
   - Default deny for non-allowlisted destinations

3. **DNS Controls**
   - DNS server configuration in guest
   - Optional: DNS-based allowlist enforcement
   - Logging of DNS queries for audit

4. **Network Policy Configuration**
   - Per-sandbox network policy:
     - `none`: No network (current default)
     - `allowlist`: Egress to allowlisted destinations only
     - `full`: Full network access (for trusted workloads)
   - API parameter: `network_policy` in create_sandbox

5. **Optional: Transparent Proxy**
   - HTTP/HTTPS proxy for logging and inspection
   - Certificate injection for HTTPS inspection
   - Request/response logging for audit

### Testing
- `pip install requests` works with allowlist policy
- `git clone` from GitHub works
- Arbitrary outbound connections blocked
- DNS resolution works for allowed domains
- Network policy enforcement per sandbox

### Exit Criteria
- Package installation and git clone work reliably
- Unauthorized egress blocked
- Network policy configurable per sandbox

---

## Phase 2.1: Template Pipeline (NEW)

**Duration:** 2-3 weeks  
**Goal:** Pre-built environment templates with fast sandbox creation.

### Deliverables

1. **Template Registry**
   - Template manifest format (JSON):
     ```json
     {
       "name": "python-3.11",
       "version": "1.0.0",
       "base": "alpine-3.18",
       "packages": ["python3", "pip", "git", "curl"],
       "pip_packages": ["pytest", "requests"],
       "rootfs_sha256": "...",
       "created_at": "..."
     }
     ```
   - Template storage in `/var/lib/firecracker-workspaces/templates/`
   - Template listing and selection via API

2. **Pre-built Templates**
   - `default`: Alpine + Python 3.11 + common tools
   - `python-ml`: Default + numpy, pandas, scikit-learn
   - `nodejs-18`: Alpine + Node.js 18 + npm
   - `golang-1.21`: Alpine + Go 1.21
   - `rust-1.75`: Alpine + Rust toolchain

3. **Template Build Pipeline**
   - Script to build template from manifest
   - Package cache baking (pip, npm)
   - Reproducible builds with checksums
   - CI integration for template updates

4. **Fast Start via Warm Pool**
   - Pre-booted VMs waiting for assignment
   - Configurable pool size per template
   - Pool replenishment on sandbox destroy
   - Target: <2s sandbox creation from warm pool

5. **Fork-from-Snapshot Alternative**
   - Boot template once, snapshot immediately
   - New sandboxes fork from snapshot
   - Faster than warm pool, less memory overhead

### Testing
- Create sandbox with specific template
- Verify pre-installed packages available
- Warm pool reduces creation time to <2s
- Template versioning and updates work

### Exit Criteria
- Multiple templates available
- Sandbox creation <2s from warm pool
- Template build pipeline automated

---

## Phase 7.1: Observability (NEW)

**Duration:** 1-2 weeks  
**Goal:** Structured telemetry for CompyMac trace integration.

### Deliverables

1. **Structured Event Logging**
   - Event types:
     - `sandbox.created`, `sandbox.destroyed`
     - `exec.started`, `exec.completed`, `exec.failed`
     - `file.read`, `file.written`
     - `network.request` (if networking enabled)
   - Event format (JSON):
     ```json
     {
       "event_type": "exec.completed",
       "sandbox_id": "...",
       "session_id": "...",
       "timestamp": "...",
       "duration_ms": 1234,
       "exit_code": 0,
       "command": "pytest tests/"
     }
     ```

2. **Event Streaming**
   - WebSocket endpoint for real-time events
   - Event filtering by sandbox_id, event_type
   - Backpressure handling

3. **Resource Metrics**
   - Per-sandbox metrics:
     - CPU usage (from cgroups)
     - Memory usage
     - Disk I/O
     - Network I/O (if enabled)
   - Metrics endpoint: `GET /sandboxes/{id}/metrics`

4. **Audit Log**
   - Persistent log of all operations
   - Configurable retention
   - Export format for analysis

### Testing
- Events emitted for all operations
- WebSocket streaming works
- Metrics accurate vs actual resource usage
- Audit log captures all operations

### Exit Criteria
- CompyMac can integrate locale2b events into TraceStore
- Resource metrics available for capacity planning
- Audit trail for security review

---

## Phase 1: Requirements, Constraints, and Interface Design

**Duration:** 1-2 weeks  
**Goal:** Establish clear API contracts, resource budgets, and acceptance criteria before writing implementation code.

### Deliverables

1. **API Specification Document**
   - Finalize REST API endpoints with OpenAPI/Swagger documentation
   - Define request/response schemas with Pydantic models
   - Establish error model (HTTP status codes, error response format)
   - Document idempotency expectations for each endpoint
   - Define timeout semantics for all operations

2. **Resource Budget Model**
   - Calculate maximum concurrent VMs based on 16GB host RAM
   - Define default resource allocations:
     - Default: 512MB RAM, 1 vCPU per sandbox
     - Minimum: 256MB RAM (for lightweight workloads)
     - Maximum: 2GB RAM (for heavy workloads)
   - Reserve host headroom (2-4GB for host OS and service)
   - Document capacity planning table:
     | Guest RAM | Max Concurrent | Use Case |
     |-----------|----------------|----------|
     | 256MB | ~40 | Minimal scripts |
     | 512MB | ~20 | Standard dev |
     | 1GB | ~10 | Heavy workloads |
     | 2GB | ~5 | ML/data processing |

3. **Protocol Specification**
   - Document vsock communication protocol between host and guest
   - Define message framing (4-byte length prefix, JSON payload)
   - Establish maximum payload sizes (10MB default)
   - Document all action types: exec, read_file, write_file, list_files, mkdir, stat, ping, delete_file

4. **Acceptance Criteria**
   - VM boot to guest agent ready: <5 seconds
   - Command execution round-trip: <100ms overhead
   - File operations: <50ms for small files (<1MB)
   - Pause/resume cycle: <2 seconds
   - API response time: <200ms for management operations

### Testing
- Paper tests: Review API contracts with example requests/responses
- Validate error scenarios and edge cases documented
- Review resource calculations with actual hardware specs

### Exit Criteria
- API specification reviewed and approved
- Resource budget validated against target hardware
- All team members aligned on interfaces and constraints

---

## Phase 2: Host Environment and VM Artifact Pipeline

**Duration:** 2-3 weeks  
**Goal:** Create reproducible, deterministic VM artifacts and host setup automation.

### Deliverables

1. **Host Setup Automation** (`scripts/setup.sh`)
   - Install Firecracker binary (version-pinned)
   - Configure /dev/kvm access (ACLs for non-root operation)
   - Create directory structure under /var/lib/firecracker-workspaces/
   - Install required host dependencies (curl, Python 3.10+)
   - Validate KVM availability and CPU virtualization support
   - Load required kernel modules (vhost_vsock)

2. **Kernel Build Pipeline**
   - Document kernel configuration for Firecracker
   - Build minimal Linux kernel (6.x series) with:
     - vsock support enabled
     - virtio drivers
     - Minimal footprint (<10MB)
   - Version and checksum all kernel artifacts
   - Store in /var/lib/firecracker-workspaces/kernels/

3. **Rootfs Build Pipeline** (`scripts/create-rootfs.sh`)
   - Create Alpine Linux-based rootfs with:
     - Python 3.10+ runtime
     - Guest agent pre-installed
     - Common development tools (git, curl, etc.)
     - Minimal size (<500MB)
   - Bake guest agent into /usr/local/bin/guest-agent
   - Configure init system to start guest agent on boot
   - Create sparse ext4 image for copy-on-write efficiency
   - Version and document rootfs contents

4. **Artifact Versioning**
   - Implement artifact manifest (JSON) with:
     - Kernel version and SHA256
     - Rootfs version and SHA256
     - Guest agent version
     - Build timestamp
   - Support multiple templates (default, python-ml, nodejs, etc.)

### Testing
- Fresh host setup from clean Ubuntu 22.04 LTS
- Verify all artifacts build reproducibly
- Smoke test: Boot VM manually and verify guest agent responds to ping
- Validate artifact checksums match across builds

### Exit Criteria
- One-command host setup on fresh NUC
- Reproducible kernel and rootfs builds
- Manual VM boot succeeds with guest agent responding

---

## Phase 3: Core MicroVM Lifecycle Management

**Duration:** 2-3 weeks  
**Goal:** Implement reliable VM creation, startup, and destruction without REST API layer.

### Deliverables

1. **SandboxManager Core** (`workspace_service/sandbox_manager.py`)
   - Implement create_sandbox():
     - Generate unique sandbox ID
     - Create overlay rootfs (copy-on-write)
     - Allocate vsock CID
     - Start Firecracker process
     - Configure VM via Firecracker API socket
     - Wait for socket readiness with timeout
   - Implement destroy_sandbox():
     - Graceful shutdown (SendCtrlAltDel)
     - Force kill if needed
     - Clean up all sandbox files
     - Release vsock CID
   - Implement get_sandbox() for status queries

2. **Process Management**
   - Track Firecracker PIDs per sandbox
   - Handle orphaned processes on service restart
   - Implement process health monitoring
   - Clean up stale sockets on startup

3. **Directory Layout**
   ```
   /var/lib/firecracker-workspaces/
   ├── sandboxes/{sandbox_id}/
   │   ├── rootfs.ext4      # Overlay copy
   │   ├── state.json       # Sandbox metadata
   │   ├── firecracker.sock # API socket
   │   └── vsock.sock       # Guest communication
   ```

4. **State Persistence**
   - Save sandbox state to state.json on creation
   - Load existing sandboxes on service startup
   - Handle state recovery after crashes

### Testing
- Lifecycle stress test: 100 create/destroy cycles
- Verify no resource leaks (file descriptors, processes, disk space)
- Test concurrent sandbox creation (up to max capacity)
- Validate cleanup on failed creation
- Test service restart with existing sandboxes

### Exit Criteria
- Reliable create/destroy lifecycle
- No resource leaks after stress testing
- Proper cleanup on all failure paths
- State persists across service restarts

---

## Phase 4: Host-Guest Communication Protocol

**Duration:** 2-3 weeks  
**Goal:** Establish reliable bidirectional communication between host service and guest agent.

### Deliverables

1. **VsockClient Implementation** (`workspace_service/sandbox_manager.py`)
   - Connect to guest agent with retry logic
   - Implement length-prefixed message framing
   - Handle connection timeouts and reconnection
   - Support concurrent requests (connection pooling optional)

2. **Guest Agent** (`guest_agent/agent.py`)
   - Listen on vsock port 5000
   - Handle all action types:
     - `exec`: Command execution with timeout
     - `read_file`: File reading with base64 encoding
     - `write_file`: File writing with optional base64
     - `list_files`: Directory listing (recursive optional)
     - `mkdir`: Directory creation
     - `stat`: File/directory statistics
     - `delete_file`: File/directory deletion
     - `ping`: Health check
   - Graceful shutdown handling
   - Error handling and structured responses

3. **Protocol Hardening**
   - Maximum message size enforcement (10MB)
   - Request timeout handling
   - Connection keepalive
   - Graceful handling of malformed requests

4. **SandboxManager Integration**
   - exec_command(): Execute commands via vsock
   - read_file(): Read files via vsock
   - write_file(): Write files via vsock
   - list_files(): List directory contents via vsock

### Testing
- Protocol fuzzing: Large payloads, malformed JSON, binary data
- Timeout scenarios: Hung commands, slow file operations
- Connection resilience: Reconnection after guest restart
- Concurrent operations: Multiple file operations in parallel
- Edge cases: Empty files, large files (>1MB), special characters in paths

### Exit Criteria
- All vsock operations work reliably
- Proper error handling for all failure modes
- No deadlocks or hangs under load
- Guest agent handles malformed requests gracefully

---

## Phase 5: REST API Surface and Contract Testing

**Duration:** 2-3 weeks  
**Goal:** Expose sandbox operations via FastAPI with comprehensive error handling.

### Deliverables

1. **FastAPI Application** (`workspace_service/main.py`)
   - Health endpoint: GET /health
   - Sandbox lifecycle:
     - POST /sandboxes (create)
     - GET /sandboxes (list all)
     - GET /sandboxes/{id} (get status)
     - DELETE /sandboxes/{id} (destroy)
   - Sandbox operations:
     - POST /sandboxes/{id}/exec (execute command)
     - POST /sandboxes/{id}/files/write
     - GET /sandboxes/{id}/files/read
     - GET /sandboxes/{id}/files/list
     - POST /sandboxes/{id}/files/upload
   - Pause/Resume:
     - POST /sandboxes/{id}/pause
     - POST /sandboxes/{id}/resume

2. **Request/Response Models**
   - Pydantic models for all requests and responses
   - Consistent error response format
   - Input validation with helpful error messages

3. **Error Handling**
   - 400: Bad request (validation errors)
   - 404: Sandbox not found
   - 409: Conflict (sandbox in wrong state)
   - 500: Internal server error
   - 503: Service unavailable (at capacity)

4. **OpenAPI Documentation**
   - Auto-generated Swagger UI at /docs
   - ReDoc at /redoc
   - Downloadable OpenAPI spec

### Testing
- Unit tests for request validation (can run in CI without KVM)
- Contract tests against live service (requires KVM host)
- Error scenario testing (invalid inputs, missing sandboxes)
- Load testing with concurrent API requests

### Exit Criteria
- All API endpoints functional
- Comprehensive error handling
- OpenAPI documentation complete
- Contract tests passing on KVM host

---

## Phase 6: Persistence, Pause/Resume, and State Management

**Duration:** 3-4 weeks  
**Goal:** Implement VM snapshotting for pause/resume and robust state management.

### Deliverables

1. **Snapshot Management**
   - pause_sandbox():
     - Pause VM via Firecracker API
     - Create full snapshot (VM state + memory)
     - Store in /var/lib/firecracker-workspaces/snapshots/{id}/
     - Update sandbox state to "paused"
   - resume_sandbox():
     - Start new Firecracker process
     - Load snapshot
     - Resume VM execution
     - Reconnect vsock client

2. **Snapshot Storage**
   ```
   /var/lib/firecracker-workspaces/snapshots/{sandbox_id}/
   ├── snapshot    # VM state
   └── memory      # Memory contents
   ```

3. **State Recovery**
   - Recover sandbox state after service restart
   - Handle corrupted snapshots gracefully
   - Reconcile state.json with actual process state
   - Clean up orphaned snapshots

4. **Workspace Identity**
   - Distinguish sandbox_id (ephemeral) from workspace_id (persistent)
   - Support resuming workspace with different sandbox_id
   - Track workspace history and lineage

### Limitations to Document
- Network connections do not survive pause/resume
- In-flight operations are lost on pause
- Snapshot size equals allocated memory
- Resume requires same kernel/rootfs versions

### Testing
- Pause/resume cycle with running process
- Resume after service restart
- Corrupted snapshot handling
- Multiple pause/resume cycles
- Verify process state preserved across resume

### Exit Criteria
- Reliable pause/resume functionality
- State recovery after service restart
- Graceful handling of corrupted snapshots
- Documented limitations

---

## Phase 7: Resource Governance and Capacity Management

**Duration:** 2-3 weeks  
**Goal:** Implement resource limits and capacity management for 16GB hosts.

### Deliverables

1. **Capacity Limits**
   - Maximum concurrent sandboxes (configurable)
   - Per-sandbox resource limits:
     - Memory: min 256MB, max 2GB, default 512MB
     - vCPU: min 1, max 4, default 1
   - Total memory budget tracking
   - Graceful rejection when at capacity (503 response)

2. **Resource Tracking**
   - Track allocated vs available resources
   - Expose capacity in /health endpoint:
     ```json
     {
       "status": "healthy",
       "active_sandboxes": 5,
       "max_sandboxes": 20,
       "memory_used_mb": 2560,
       "memory_available_mb": 7680
     }
     ```

3. **Cleanup Policies**
   - Configurable sandbox TTL (time-to-live)
   - Idle timeout for unused sandboxes
   - LRU eviction when approaching capacity
   - Orphan reaper for leaked resources

4. **Configuration**
   - Environment variables for all limits
   - Configuration file support (optional)
   - Runtime configuration validation

### Testing
- Stress test: Create sandboxes until capacity limit
- Verify graceful rejection at capacity
- TTL expiration testing
- Memory accounting accuracy
- Soak test: Extended operation under load

### Exit Criteria
- Capacity limits enforced
- No resource exhaustion under load
- Cleanup policies working
- Configuration documented

---

## Phase 8: Security Hardening and Production Posture

**Duration:** 3-4 weeks  
**Goal:** Harden the service for production deployment with security best practices.

### Deliverables

1. **Firecracker Jailer Integration** (Optional but Recommended)
   - Run Firecracker in jailer for additional isolation
   - Chroot environment per sandbox
   - Seccomp filtering
   - Resource cgroups

2. **Filesystem Security**
   - Restrict permissions on /var/lib/firecracker-workspaces/
   - Prevent path traversal in file operations
   - Validate all file paths against sandbox boundaries
   - No symlink following outside sandbox

3. **Network Security**
   - Default: No network access for sandboxes
   - Optional: Controlled network with firewall rules
   - Rate limiting on API endpoints
   - Request size limits

4. **Authentication and Authorization**
   - API key authentication (minimum)
   - Optional: Per-agent tokens
   - Request logging and audit trail

5. **Threat Model Documentation**
   - VM escape risks and mitigations
   - DoS attack vectors and protections
   - Data exfiltration prevention
   - Secrets handling

6. **Hardened Deployment Guide**
   - Non-root operation
   - Systemd service configuration
   - Log rotation
   - Monitoring recommendations

### Testing
- Negative tests: Unauthenticated requests rejected
- Path traversal attempts blocked
- Oversized request rejection
- Rate limit enforcement
- Security audit/review

### Exit Criteria
- Security hardening complete
- Threat model documented
- Deployment guide written
- Security tests passing

---

## Phase 9: CompyMac Integration and Developer Experience

**Duration:** 2-3 weeks  
**Goal:** Integrate with CompyMac agent via a new RemoteHarness implementation.

**Prerequisites:** Phase 4.1 (Process Model Parity) must be complete for full integration.

### Deliverables

1. **RemoteHarness Implementation** (in CompyMac repo: `src/compymac/remote_harness.py`)
   - Implements same interface as `LocalHarness`
   - Translates CompyMac tool calls to locale2b REST API:
     - `bash` → `POST /sandboxes/{id}/exec/start` or `/exec`
     - `bash_output` → `GET /sandboxes/{id}/exec/{session_id}/output`
     - `write_to_shell` → `POST /sandboxes/{id}/exec/{session_id}/input`
     - `kill_shell` → `DELETE /sandboxes/{id}/exec/{session_id}`
     - `Read` → `GET /sandboxes/{id}/files/read`
     - `Write` → `POST /sandboxes/{id}/files/write`
   - WebSocket connection for streaming output
   - Automatic sandbox lifecycle management
   - Fallback to LocalHarness for browser/web tools

2. **locale2b Client Library** (`compymac_integration/client.py`)
   - Python client for locale2b REST API
   - Async support with httpx
   - Connection pooling and retry logic
   - Streaming output via WebSocket or long-polling

3. **Configuration**
   - Environment variables for locale2b connection:
     - `LOCALE2B_URL`: Service URL (default: `http://localhost:8080`)
     - `LOCALE2B_API_KEY`: Authentication key
     - `LOCALE2B_TEMPLATE`: Default template (default: `python-3.11`)
   - Harness selection in CompyMac config:
     - `COMPYMAC_HARNESS=local` (default)
     - `COMPYMAC_HARNESS=remote` (use locale2b)

4. **Integration Testing**
   - End-to-end tests with CompyMac agent loop
   - Tool parity tests: Same commands produce same results in local vs remote
   - Performance comparison: Local vs remote execution overhead
   - Error handling: Network failures, sandbox crashes

5. **Developer Documentation**
   - Quick start guide for locale2b + CompyMac
   - Architecture diagram showing integration
   - Troubleshooting guide for common issues
   - Performance tuning recommendations

### Testing
- Run SWE-bench task with RemoteHarness
- Verify tool contract parity with LocalHarness
- Stress test: Multiple concurrent agent sessions
- Failover: Graceful handling of locale2b unavailability

### Exit Criteria
- CompyMac can run tasks using locale2b as execution backend
- Tool behavior matches LocalHarness semantics
- Documentation complete
- Performance overhead <200ms per tool call

---

## CI/CD Considerations

### What Can Run in Standard CI
- Unit tests for request validation
- Pydantic model tests
- Pure Python logic tests
- Linting and type checking
- Documentation builds

### What Requires KVM Host
- Integration tests with actual VMs
- End-to-end API tests
- Performance benchmarks
- Stress tests
- Pause/resume tests

### Recommended CI Setup
1. Standard CI pipeline for unit tests (GitHub Actions)
2. Self-hosted runner on KVM-capable hardware for integration tests
3. Nightly full test suite on dedicated hardware

---

## Risk Mitigation

### Technical Risks

| Risk | Mitigation |
|------|------------|
| vsock deadlocks | Timeouts on all operations, connection health checks |
| Orphaned processes | PID tracking, cleanup on startup, periodic reaper |
| Disk space exhaustion | Overlay size limits, cleanup policies |
| Memory exhaustion | Strict capacity limits, graceful rejection |
| Snapshot corruption | Validation on load, graceful fallback |

### Operational Risks

| Risk | Mitigation |
|------|------------|
| Host reboot | State recovery, documented procedures |
| Service crash | Systemd restart, state persistence |
| Hardware failure | Documentation, no HA in v1 |

---

## Success Metrics

### Performance
- VM boot to ready: <5 seconds
- Command execution overhead: <100ms
- API response time (p99): <500ms
- Concurrent sandboxes: 20+ on 16GB host

### Reliability
- Service uptime: 99.9%
- Successful sandbox creation rate: 99.9%
- Pause/resume success rate: 99%

### Developer Experience
- Time to first sandbox: <5 minutes
- Documentation completeness: All features documented
- Example coverage: Common use cases covered

---

## Timeline Summary

| Phase | Duration | Dependencies |
|-------|----------|--------------|
| 1. Requirements & Design | 1-2 weeks | None |
| 2. Host & Artifacts | 2-3 weeks | Phase 1 |
| 3. VM Lifecycle | 2-3 weeks | Phase 2 |
| 4. Host-Guest Comms | 2-3 weeks | Phase 3 |
| 5. REST API | 2-3 weeks | Phase 4 |
| 6. Persistence | 3-4 weeks | Phase 5 |
| 7. Resource Governance | 2-3 weeks | Phase 5 |
| 8. Security | 3-4 weeks | Phase 6, 7 |
| 9. Integration | 2-3 weeks | Phase 8 |

**Total Estimated Duration:** 20-28 weeks (5-7 months)

Note: Phases 6 and 7 can run in parallel after Phase 5 completion.

---

## Appendix: Configuration Reference

### Environment Variables

```bash
# Service configuration
LOCALE2B_HOST=0.0.0.0
LOCALE2B_PORT=8080
LOCALE2B_LOG_LEVEL=INFO

# Resource limits
LOCALE2B_MAX_SANDBOXES=20
LOCALE2B_DEFAULT_MEMORY_MB=512
LOCALE2B_MAX_MEMORY_MB=2048
LOCALE2B_DEFAULT_VCPU=1
LOCALE2B_MAX_VCPU=4

# Timeouts
LOCALE2B_BOOT_TIMEOUT_SEC=30
LOCALE2B_EXEC_TIMEOUT_SEC=300
LOCALE2B_IDLE_TIMEOUT_SEC=3600

# Paths
LOCALE2B_BASE_DIR=/var/lib/firecracker-workspaces
LOCALE2B_FIRECRACKER_BIN=/usr/bin/firecracker

# Security
LOCALE2B_API_KEY=your-secret-key
LOCALE2B_USE_JAILER=false
```

### Directory Structure

```
/var/lib/firecracker-workspaces/
├── kernels/
│   └── default-vmlinux.bin
├── rootfs/
│   └── default-rootfs.ext4
├── sandboxes/
│   └── {sandbox_id}/
│       ├── rootfs.ext4
│       ├── state.json
│       ├── firecracker.sock
│       └── vsock.sock
└── snapshots/
    └── {sandbox_id}/
        ├── snapshot
        └── memory
```
