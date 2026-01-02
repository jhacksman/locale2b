# locale2b Development Phases

A comprehensive phased development plan for building a production-ready local E2B-like service using Firecracker microVMs.

## Overview

This document outlines the development phases for locale2b, a self-hosted sandbox service that provides isolated workspace environments for AI agent code execution. The service follows the same architectural pattern as cloud-based solutions like E2B but runs entirely on bare metal without cloud dependencies.

**Target Hardware:** Intel NUC with 16GB RAM, Linux with KVM support  
**Primary Integration:** CompyMac AI Agent  
**Architecture:** FastAPI REST API + Firecracker microVMs + vsock guest agent

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
**Goal:** Integrate with CompyMac agent and provide excellent developer experience.

### Deliverables

1. **CompyMac Provider** (`compymac_integration/workspace_provider.py`)
   - FirecrackerWorkspaceProvider class
   - Async context manager support
   - All workspace operations:
     - create_workspace()
     - run_command()
     - write_file() / read_file()
     - list_files()
     - pause_workspace() / resume_workspace()
     - destroy_workspace()
   - Convenience methods:
     - install_package()
     - clone_repo()
     - run_python()
     - run_tests()

2. **Integration Testing**
   - End-to-end tests with CompyMac agent loop
   - Validate expected semantics match
   - Test workspace persistence across agent runs
   - Verify file sync behavior

3. **Developer Documentation**
   - Quick start guide
   - API reference
   - Integration examples
   - Troubleshooting guide

4. **Example Applications**
   - Simple "hello world" sandbox usage
   - Python script execution example
   - Git clone and build example
   - Multi-step agent task example

### Testing
- End-to-end: CompyMac -> Service -> Guest Agent
- Regression tests for exact API calls CompyMac uses
- Performance benchmarks
- Error handling in agent context

### Exit Criteria
- CompyMac integration working end-to-end
- Documentation complete
- Examples tested and working
- Performance meets requirements

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
