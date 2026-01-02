#!/bin/bash
# Firecracker Workspace Service - Setup Script
# Run this script on your NUC to set up the environment

set -e

echo "=== Firecracker Workspace Service Setup ==="
echo ""

# Check if running as root for certain operations
if [ "$EUID" -eq 0 ]; then
    echo "Warning: Running as root. Some operations will be performed directly."
    SUDO=""
else
    SUDO="sudo"
fi

# 1. Check KVM availability
echo "1. Checking KVM availability..."
if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
    echo "   KVM is accessible."
else
    echo "   KVM not accessible. Setting up ACL..."
    $SUDO apt-get install -y acl
    $SUDO setfacl -m u:${USER}:rw /dev/kvm
    echo "   ACL set. Please log out and back in if KVM still not accessible."
fi

# 2. Install dependencies
echo ""
echo "2. Installing system dependencies..."
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

# 3. Install Firecracker
echo ""
echo "3. Installing Firecracker..."
ARCH="$(uname -m)"
RELEASE_URL="https://github.com/firecracker-microvm/firecracker/releases"
LATEST=$(basename $(curl -fsSLI -o /dev/null -w %{url_effective} ${RELEASE_URL}/latest))

if [ ! -f /usr/bin/firecracker ]; then
    echo "   Downloading Firecracker ${LATEST}..."
    curl -L ${RELEASE_URL}/download/${LATEST}/firecracker-${LATEST}-${ARCH}.tgz | tar -xz
    $SUDO mv release-${LATEST}-${ARCH}/firecracker-${LATEST}-${ARCH} /usr/bin/firecracker
    $SUDO mv release-${LATEST}-${ARCH}/jailer-${LATEST}-${ARCH} /usr/bin/jailer
    $SUDO chmod +x /usr/bin/firecracker /usr/bin/jailer
    rm -rf release-${LATEST}-${ARCH}
    echo "   Firecracker installed."
else
    echo "   Firecracker already installed."
fi

# Verify installation
firecracker --version

# 4. Create directory structure
echo ""
echo "4. Creating directory structure..."
$SUDO mkdir -p /var/lib/firecracker-workspaces/{kernels,rootfs,sandboxes,snapshots}
$SUDO chown -R ${USER}:${USER} /var/lib/firecracker-workspaces
echo "   Directories created."

# 5. Download kernel
echo ""
echo "5. Downloading kernel..."
KERNEL_PATH="/var/lib/firecracker-workspaces/kernels/default-vmlinux.bin"
if [ ! -f "$KERNEL_PATH" ]; then
    echo "   Downloading pre-built kernel..."
    curl -fsSL -o "$KERNEL_PATH" \
        https://s3.amazonaws.com/spec.ccfc.min/img/quickstart_guide/${ARCH}/kernels/vmlinux.bin
    echo "   Kernel downloaded."
else
    echo "   Kernel already exists."
fi

# 6. Create Python virtual environment
echo ""
echo "6. Setting up Python environment..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
echo "   Python environment ready."

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Create a rootfs image (see scripts/create-rootfs.sh)"
echo "2. Start the service: source .venv/bin/activate && uvicorn workspace_service.main:app --host 0.0.0.0 --port 8080"
echo ""
