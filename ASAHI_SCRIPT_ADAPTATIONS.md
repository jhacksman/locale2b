# Script Adaptations for Fedora Asahi Remix (aarch64)

## Summary

The existing locale2b scripts are **mostly compatible** with aarch64, but need minor modifications for Fedora Asahi Remix (which uses `dnf` instead of `apt-get`).

## Architecture Detection: ✅ Already Works

Both `scripts/setup.sh` and `scripts/create-rootfs.sh` correctly detect and handle aarch64:

- **setup.sh line 51:** `ARCH="$(uname -m)"` - Auto-detects architecture
- **create-rootfs.sh lines 59-67:** Properly maps `aarch64` to Alpine Linux aarch64 packages

**No changes needed for architecture detection.**

---

## Required Changes for Fedora Asahi Remix

### 1. Package Manager: apt-get → dnf

The `scripts/setup.sh` script currently uses Debian/Ubuntu's `apt-get`, but Fedora Asahi Remix uses `dnf`.

**Location:** `scripts/setup.sh` lines 24-46

**Current code (Debian/Ubuntu):**
```bash
$SUDO apt-get update
$SUDO apt-get install -y \
    curl \
    wget \
    git \
    python3 \
    python3-pip \
    python3-venv \
    build-essential \
    flex \
    bison \
    libncurses5-dev \
    libssl-dev \
    bc \
    libelf-dev
```

**Needed for Fedora:**
```bash
$SUDO dnf update -y
$SUDO dnf install -y \
    curl \
    wget \
    git \
    python3 \
    python3-pip \
    python3-virtualenv \
    gcc \
    make \
    flex \
    bison \
    ncurses-devel \
    openssl-devel \
    bc \
    elfutils-libelf-devel
```

**Package name mappings:**
| Debian/Ubuntu | Fedora |
|---------------|--------|
| `build-essential` | `gcc make` |
| `python3-venv` | `python3-virtualenv` |
| `libncurses5-dev` | `ncurses-devel` |
| `libssl-dev` | `openssl-devel` |
| `libelf-dev` | `elfutils-libelf-devel` |

### 2. ACL Installation

**Location:** `scripts/setup.sh` line 24

**Current:**
```bash
$SUDO apt-get install -y acl
```

**For Fedora:**
```bash
$SUDO dnf install -y acl
```

---

## Recommended Approach: Detect Distro and Use Appropriate Package Manager

### Option 1: Quick Fix (Manual Edit)

Manually edit `scripts/setup.sh` and replace `apt-get` with `dnf` and package names as shown above.

### Option 2: Auto-Detect Distribution (Recommended)

Modify `scripts/setup.sh` to detect the distribution and use the appropriate package manager:

```bash
# Detect distribution
if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO=$ID
else
    DISTRO=$(uname -s)
fi

# Install dependencies based on distro
if [ "$DISTRO" = "fedora" ]; then
    echo "   Detected Fedora, using dnf..."
    $SUDO dnf update -y
    $SUDO dnf install -y \
        curl wget git python3 python3-pip python3-virtualenv \
        gcc make flex bison ncurses-devel openssl-devel bc elfutils-libelf-devel
elif [ "$DISTRO" = "ubuntu" ] || [ "$DISTRO" = "debian" ]; then
    echo "   Detected Debian/Ubuntu, using apt-get..."
    $SUDO apt-get update
    $SUDO apt-get install -y \
        curl wget git python3 python3-pip python3-venv \
        build-essential flex bison libncurses5-dev libssl-dev bc libelf-dev
else
    echo "   Unknown distribution: $DISTRO"
    echo "   Please install dependencies manually."
fi
```

---

## Additional Fedora-Specific Considerations

### 1. Firewall Configuration

Fedora uses `firewalld` by default. If you want to access the service from another machine:

```bash
# Allow port 8080 through firewall
sudo firewall-cmd --permanent --add-port=8080/tcp
sudo firewall-cmd --reload

# Or allow the entire service
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --reload
```

### 2. SELinux Considerations

Fedora has SELinux enabled by default. If you encounter permission issues:

```bash
# Check SELinux status
getenforce

# View SELinux denials
sudo ausearch -m avc -ts recent

# Temporarily set to permissive mode (for debugging only)
sudo setenforce 0

# To make permanent (NOT recommended, fix SELinux policies instead)
# sudo sed -i 's/^SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config
```

For production, create proper SELinux policies rather than disabling it.

### 3. systemd Service File (Optional)

To run locale2b as a systemd service on Fedora:

```bash
# Create service file
sudo tee /etc/systemd/system/locale2b.service << 'EOF'
[Unit]
Description=locale2b Firecracker Workspace Service
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/locale2b
Environment="PATH=/home/YOUR_USERNAME/locale2b/.venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/home/YOUR_USERNAME/locale2b/.venv/bin/uvicorn workspace_service.main:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
sudo systemctl daemon-reload

# Enable and start service
sudo systemctl enable locale2b
sudo systemctl start locale2b

# Check status
sudo systemctl status locale2b
```

---

## Testing on Fedora Asahi Remix

### Pre-Test Checklist

Before running the setup script on Fedora Asahi Remix:

```bash
# 1. Verify you're on Fedora
cat /etc/os-release | grep "Fedora Asahi Remix"

# 2. Verify architecture
uname -m  # Should output: aarch64

# 3. Verify KVM is available
ls -la /dev/kvm

# 4. Verify vhost_vsock module exists
modinfo vhost_vsock
```

### Manual Setup Steps (If Script Fails)

If the automated script doesn't work, follow these manual steps:

```bash
# 1. Install dependencies
sudo dnf install -y \
    curl wget git \
    python3 python3-pip python3-virtualenv \
    gcc make flex bison \
    ncurses-devel openssl-devel bc elfutils-libelf-devel \
    e2fsprogs acl

# 2. Set KVM permissions
sudo usermod -a -G kvm $USER
# Log out and log back in

# 3. Load vhost_vsock
sudo modprobe vhost_vsock
echo "vhost_vsock" | sudo tee /etc/modules-load.d/vhost_vsock.conf

# 4. Download Firecracker
ARCH="aarch64"
RELEASE_URL="https://github.com/firecracker-microvm/firecracker/releases"
LATEST=$(basename $(curl -fsSLI -o /dev/null -w %{url_effective} ${RELEASE_URL}/latest))
curl -L ${RELEASE_URL}/download/${LATEST}/firecracker-${LATEST}-${ARCH}.tgz | tar -xz
sudo mv release-${LATEST}-${ARCH}/firecracker-${LATEST}-${ARCH} /usr/local/bin/firecracker
sudo mv release-${LATEST}-${ARCH}/jailer-${LATEST}-${ARCH} /usr/local/bin/jailer
sudo chmod +x /usr/local/bin/firecracker /usr/local/bin/jailer
rm -rf release-${LATEST}-${ARCH}

# 5. Create directory structure
sudo mkdir -p /var/lib/firecracker-workspaces/{kernels,rootfs,sandboxes,snapshots}
sudo chown -R ${USER}:${USER} /var/lib/firecracker-workspaces

# 6. Download kernel
curl -fsSL -o /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin \
    https://s3.amazonaws.com/spec.ccfc.min/img/quickstart_guide/aarch64/kernels/vmlinux.bin

# 7. Create rootfs
cd ~/locale2b
sudo ./scripts/create-rootfs.sh

# 8. Set up Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## Summary of Changes Needed

| Component | Status | Action Required |
|-----------|--------|-----------------|
| Architecture detection | ✅ Works | None - already correct |
| Kernel download | ✅ Works | None - already correct |
| Alpine rootfs creation | ✅ Works | None - already correct |
| Package manager | ❌ Needs update | Change `apt-get` to `dnf` |
| Package names | ❌ Needs update | Map Debian → Fedora package names |
| Firecracker binary path | ⚠️ Minor issue | Script uses `/usr/bin`, guide uses `/usr/local/bin` |

**Recommendation:**
1. For quick testing: Manually run commands from "Manual Setup Steps" above
2. For long-term: Create a PR to make scripts distribution-agnostic

---

## Validation Script

After setup, run this validation script to ensure everything is configured correctly:

```bash
#!/bin/bash
# validate-asahi-setup.sh

echo "=== Validating locale2b Setup on Fedora Asahi Remix ==="
echo ""

# Check OS
echo -n "1. Checking OS: "
if grep -q "Fedora" /etc/os-release; then
    echo "✓ Fedora detected"
else
    echo "✗ Not Fedora (unexpected)"
fi

# Check architecture
echo -n "2. Checking architecture: "
if [ "$(uname -m)" = "aarch64" ]; then
    echo "✓ aarch64"
else
    echo "✗ Not aarch64: $(uname -m)"
fi

# Check KVM
echo -n "3. Checking KVM access: "
if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
    echo "✓ /dev/kvm accessible"
else
    echo "✗ /dev/kvm not accessible"
fi

# Check vhost_vsock
echo -n "4. Checking vhost_vsock module: "
if lsmod | grep -q vhost_vsock; then
    echo "✓ vhost_vsock loaded"
else
    echo "✗ vhost_vsock not loaded"
fi

# Check Firecracker
echo -n "5. Checking Firecracker: "
if command -v firecracker &> /dev/null; then
    echo "✓ Firecracker installed ($(firecracker --version | head -n1))"
else
    echo "✗ Firecracker not found"
fi

# Check kernel
echo -n "6. Checking kernel image: "
if [ -f /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin ]; then
    KERNEL_ARCH=$(file /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin | grep -o 'ARM aarch64')
    if [ "$KERNEL_ARCH" = "ARM aarch64" ]; then
        echo "✓ aarch64 kernel present"
    else
        echo "✗ Wrong architecture kernel"
    fi
else
    echo "✗ Kernel not found"
fi

# Check rootfs
echo -n "7. Checking rootfs image: "
if [ -f /var/lib/firecracker-workspaces/rootfs/default-rootfs.ext4 ]; then
    SIZE=$(du -h /var/lib/firecracker-workspaces/rootfs/default-rootfs.ext4 | cut -f1)
    echo "✓ Rootfs present ($SIZE)"
else
    echo "✗ Rootfs not found"
fi

# Check Python environment
echo -n "8. Checking Python environment: "
if [ -d ~/locale2b/.venv ]; then
    echo "✓ Virtual environment exists"
else
    echo "✗ Virtual environment not found"
fi

echo ""
echo "=== Validation Complete ==="
```

Save this as `scripts/validate-asahi-setup.sh`, make it executable, and run it:

```bash
chmod +x scripts/validate-asahi-setup.sh
./scripts/validate-asahi-setup.sh
```

---

## Next Steps

1. **Immediate:** Use manual setup commands from this document
2. **Short-term:** Test the setup and document any additional issues
3. **Long-term:** Create a PR to update `scripts/setup.sh` with distribution detection
4. **Future:** Add Fedora/Asahi-specific documentation to main README
