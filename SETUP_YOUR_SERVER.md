# Setup for Your x86_64 Server (3x 3090s)

Dead simple commands to get locale2b running on your server.

## 1. Clone the Repo

```bash
ssh your-server
cd ~
git clone https://github.com/jhacksman/locale2b.git
cd locale2b
```

**That's it.** Everything you need is in the repo. No external downloads.

## 2. Run Setup

```bash
# Install system deps and configure KVM
./scripts/setup-secure.sh

# Log out and back in (for KVM group membership)
exit
ssh your-server
cd ~/locale2b
```

## 3. Create Root Filesystem

```bash
# Build the guest VM rootfs (takes ~10 min)
sudo ./scripts/create-rootfs-secure.sh
```

## 4. Start the Service

```bash
# Start locale2b
uv run python -m workspace_service.main
```

Service runs on `http://localhost:8080`

## 5. Test It

Open another terminal:

```bash
# Create a sandbox
curl -X POST http://localhost:8080/sandboxes \
  -H "Content-Type: application/json" \
  -d '{"memory_mb": 512, "vcpu_count": 1}'

# Note the sandbox_id
SANDBOX_ID="paste-id-here"

# Run a command
curl -X POST http://localhost:8080/sandboxes/${SANDBOX_ID}/exec \
  -H "Content-Type: application/json" \
  -d '{"command": "uname -a"}' | jq

# Clean up
curl -X DELETE http://localhost:8080/sandboxes/${SANDBOX_ID}
```

## That's It

- ✅ All binaries from YOUR repo
- ✅ No random internet downloads
- ✅ No trust in external CDNs
- ✅ Reproducible setup

## If Something Breaks

### KVM Permission Issues
```bash
sudo usermod -a -G kvm $USER
# Log out and back in
```

### vhost_vsock Not Loaded
```bash
sudo modprobe vhost_vsock
```

### Want to See What's Vendored?
```bash
ls -lh vendor/firecracker/
ls -lh vendor/kernels/
ls -lh vendor/rootfs/
cat vendor/README.md
```

## Security Model

**Old way (what you hated):**
- Download random binaries from internet
- Hope they're not compromised
- No control over versions

**New way (what you demanded):**
- Everything in git
- You control exactly what runs
- Audit once, use forever

Done.
