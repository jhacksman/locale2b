#!/bin/bash
# Secure setup script - uses ONLY vendored files from repo
# NO external downloads, NO trusting random URLs
# Everything comes from git

set -euo pipefail

echo "=== locale2b Secure Setup (Repo-First) ==="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENDOR_DIR="$PROJECT_DIR/vendor"

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Warning: Running as root."
    SUDO=""
else
    SUDO="sudo"
fi

# 1. Check vendored files exist
echo "1. Checking vendored artifacts..."
if [ ! -f "$VENDOR_DIR/firecracker/firecracker" ]; then
    echo "Error: Firecracker binary not found in vendor/"
    echo "Run: git clone https://github.com/jhacksman/locale2b.git"
    echo "The repo should have everything vendored."
    exit 1
fi

if [ ! -f "$VENDOR_DIR/kernels/vmlinux.bin" ]; then
    echo "Error: Kernel not found in vendor/"
    exit 1
fi

if [ ! -f "$VENDOR_DIR/rootfs/alpine-minirootfs.tar.gz" ]; then
    echo "Error: Alpine rootfs not found in vendor/"
    exit 1
fi

echo "   ✓ All vendored artifacts present"

# 2. Install system dependencies (only thing we trust package manager for)
echo ""
echo "2. Installing system dependencies..."

# Detect distro
if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO=$ID
else
    DISTRO="unknown"
fi

if [ "$DISTRO" = "ubuntu" ] || [ "$DISTRO" = "debian" ]; then
    $SUDO apt-get update
    $SUDO apt-get install -y \
        python3 python3-pip python3-venv \
        e2fsprogs acl jq
elif [ "$DISTRO" = "fedora" ]; then
    $SUDO dnf install -y \
        python3 python3-pip python3-virtualenv \
        e2fsprogs acl jq
else
    echo "Unknown distro: $DISTRO"
    echo "Install manually: python3, e2fsprogs, acl, jq"
fi

# 3. Check KVM
echo ""
echo "3. Checking KVM availability..."
if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
    echo "   ✓ KVM is accessible"
else
    echo "   Setting up KVM access..."
    if [ "$DISTRO" = "ubuntu" ] || [ "$DISTRO" = "debian" ]; then
        $SUDO apt-get install -y acl
    fi
    $SUDO setfacl -m u:${USER}:rw /dev/kvm 2>/dev/null || true
    $SUDO usermod -a -G kvm ${USER} 2>/dev/null || true
    echo "   ACL set. You may need to log out and back in."
fi

# 4. Load vhost_vsock module
echo ""
echo "4. Loading vhost_vsock module..."
$SUDO modprobe vhost_vsock 2>/dev/null || true
echo "vhost_vsock" | $SUDO tee /etc/modules-load.d/vhost_vsock.conf > /dev/null
echo "   ✓ vhost_vsock loaded"

# 5. Install Firecracker from vendored files
echo ""
echo "5. Installing Firecracker from vendor/..."
$SUDO install -o root -g root -m 0755 \
    "$VENDOR_DIR/firecracker/firecracker" /usr/local/bin/firecracker
$SUDO install -o root -g root -m 0755 \
    "$VENDOR_DIR/firecracker/jailer" /usr/local/bin/jailer

FC_VERSION=$(cat "$VENDOR_DIR/firecracker/VERSION")
echo "   ✓ Firecracker $FC_VERSION installed"
firecracker --version

# 6. Create directory structure
echo ""
echo "6. Creating directory structure..."
$SUDO mkdir -p /var/lib/firecracker-workspaces/{kernels,rootfs,sandboxes,snapshots}
$SUDO chown -R ${USER}:${USER} /var/lib/firecracker-workspaces
echo "   ✓ Directories created"

# 7. Install kernel from vendored files
echo ""
echo "7. Installing kernel from vendor/..."
KERNEL_PATH="/var/lib/firecracker-workspaces/kernels/default-vmlinux.bin"
if [ ! -f "$KERNEL_PATH" ]; then
    cp "$VENDOR_DIR/kernels/vmlinux.bin" "$KERNEL_PATH"
    KERNEL_VERSION=$(cat "$VENDOR_DIR/kernels/VERSION")
    echo "   ✓ Kernel $KERNEL_VERSION installed"
else
    echo "   Kernel already exists, skipping"
fi

# 8. Set up Python environment with UV
echo ""
echo "8. Setting up Python environment..."
cd "$PROJECT_DIR"

# Check if UV is installed
if ! command -v uv &> /dev/null; then
    echo "   Installing UV..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

# Install Python dependencies
uv sync
echo "   ✓ Python environment ready"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Create rootfs: sudo $SCRIPT_DIR/create-rootfs-secure.sh"
echo "2. Start service: cd $PROJECT_DIR && uv run python -m workspace_service.main"
echo ""
echo "All binaries came from YOUR repo - no external downloads executed."
