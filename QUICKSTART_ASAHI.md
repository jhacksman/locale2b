# Quick Start: locale2b on Apple Silicon Mac Mini (Asahi Linux)

**TL;DR:** It works! Follow these steps to get running in ~2 hours.

## Prerequisites

- Apple Silicon Mac Mini (M1/M2/M3)
- macOS Ventura 13.5+ or Sonoma 14.2+
- 70GB+ free disk space

## Step 1: Install Fedora Asahi Remix (30-45 minutes)

```bash
# From macOS Terminal:
curl https://alx.sh | sh

# Follow prompts:
# - Select "Fedora Asahi Remix"
# - Allocate 100GB+ for Linux partition
# - Wait for installation
# - Reboot and select Fedora from boot menu
```

## Step 2: Verify KVM Support (5 minutes)

```bash
# After booting into Fedora Asahi Remix:

# Check architecture
uname -m  # Should show: aarch64

# Check KVM
ls -la /dev/kvm  # Should exist

# Add yourself to kvm group
sudo usermod -a -G kvm $USER

# Load vhost_vsock module
sudo modprobe vhost_vsock
echo "vhost_vsock" | sudo tee /etc/modules-load.d/vhost_vsock.conf

# Log out and back in for group changes
```

## Step 3: Install Firecracker (10 minutes)

```bash
# Install dependencies
sudo dnf install -y curl wget git python3 python3-pip python3-virtualenv \
    gcc make flex bison ncurses-devel openssl-devel bc elfutils-libelf-devel e2fsprogs

# Download Firecracker for aarch64
RELEASE_URL="https://github.com/firecracker-microvm/firecracker/releases"
LATEST=$(basename $(curl -fsSLI -o /dev/null -w %{url_effective} ${RELEASE_URL}/latest))
curl -L ${RELEASE_URL}/download/${LATEST}/firecracker-${LATEST}-aarch64.tgz | tar -xz
sudo mv release-${LATEST}-aarch64/firecracker-${LATEST}-aarch64 /usr/local/bin/firecracker
sudo mv release-${LATEST}-aarch64/jailer-${LATEST}-aarch64 /usr/local/bin/jailer
sudo chmod +x /usr/local/bin/firecracker /usr/local/bin/jailer
rm -rf release-${LATEST}-aarch64

# Verify
firecracker --version
```

## Step 4: Set Up locale2b (30 minutes)

```bash
# Clone repository
cd ~
git clone https://github.com/jhacksman/locale2b.git
cd locale2b

# Create directory structure
sudo mkdir -p /var/lib/firecracker-workspaces/{kernels,rootfs,sandboxes,snapshots}
sudo chown -R ${USER}:${USER} /var/lib/firecracker-workspaces

# Download ARM64 kernel
curl -fsSL -o /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin \
    https://s3.amazonaws.com/spec.ccfc.min/img/quickstart_guide/aarch64/kernels/vmlinux.bin

# Create rootfs (takes ~10 minutes)
sudo ./scripts/create-rootfs.sh

# Set up Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Step 5: Test It! (10 minutes)

```bash
# Start the service
cd ~/locale2b
source .venv/bin/activate
uv run python -m workspace_service.main

# In a new terminal, test:
# 1. Create sandbox
curl -X POST http://localhost:8080/sandboxes \
  -H "Content-Type: application/json" \
  -d '{"memory_mb": 512, "vcpu_count": 1}'

# Note the sandbox_id from response
SANDBOX_ID="paste-id-here"

# 2. Run command (should show "aarch64")
curl -X POST http://localhost:8080/sandboxes/${SANDBOX_ID}/exec \
  -H "Content-Type: application/json" \
  -d '{"command": "uname -a"}'

# 3. Clean up
curl -X DELETE http://localhost:8080/sandboxes/${SANDBOX_ID}
```

## Expected Output

Your `uname -a` command should return something like:
```
Linux localhost 6.6.x-asahi-xxx aarch64 GNU/Linux
```

The key is `aarch64` - that confirms you're running ARM64 Linux inside the VM!

## Common Issues

### `/dev/kvm` permission denied
```bash
sudo usermod -a -G kvm $USER
# Log out and back in
```

### `vhost_vsock` module not found
```bash
sudo modprobe vhost_vsock
```

### Firecracker binary not found
```bash
# Check PATH
which firecracker
# If not found, use full path:
/usr/local/bin/firecracker --version
```

## What's Next?

You now have a working Firecracker setup! But your VMs have **no network access** yet.

For networking (pip install, git clone, etc.):
- See **ASAHI_SETUP_GUIDE.md** → "What's Next: Networking" section
- Or implement Phase 8.1 from **DEVELOPMENT_PHASES.md**

## Full Documentation

- **ASAHI_SETUP_GUIDE.md** - Complete setup guide with troubleshooting
- **ASAHI_SCRIPT_ADAPTATIONS.md** - Script compatibility notes
- **DEVELOPMENT_PHASES.md** - Full project roadmap

## Resources

- [Firecracker on GitHub](https://github.com/firecracker-microvm/firecracker)
- [Asahi Linux Official Site](https://asahilinux.org/)
- [Fedora Asahi Remix Docs](https://docs.fedoraproject.org/en-US/fedora-asahi-remix/)

## Success? Share Your Results!

If this works for you, please:
1. Star the [locale2b repository](https://github.com/jhacksman/locale2b)
2. Open an issue to share your Mac Mini model and any issues encountered
3. Contribute improvements to this guide

---

**Time to first working VM:** ~2 hours
**Difficulty:** Medium (mostly waiting for installations)
**Success Rate:** High (all components are proven and stable)
