#!/bin/bash
# ONE-TIME script to vendor all external dependencies into the repo
# Run this ONCE, then commit everything. After that, setup.sh uses only repo files.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENDOR_DIR="$PROJECT_DIR/vendor"

echo "=== Vendoring External Dependencies ==="
echo "This script downloads binaries and commits them to the repo."
echo "You run this ONCE, then everything comes from git."
echo ""

# Detect architecture
ARCH=$(uname -m)
echo "Architecture: $ARCH"
echo ""

# Create vendor structure
mkdir -p "$VENDOR_DIR"/{firecracker,kernels,rootfs}

# Firecracker version to vendor
FC_VERSION="v1.9.1"

echo "1. Vendoring Firecracker $FC_VERSION..."
if [ "$ARCH" = "x86_64" ]; then
    FC_URL="https://github.com/firecracker-microvm/firecracker/releases/download/${FC_VERSION}/firecracker-${FC_VERSION}-x86_64.tgz"
elif [ "$ARCH" = "aarch64" ]; then
    FC_URL="https://github.com/firecracker-microvm/firecracker/releases/download/${FC_VERSION}/firecracker-${FC_VERSION}-aarch64.tgz"
else
    echo "Unsupported architecture: $ARCH"
    exit 1
fi

cd "$VENDOR_DIR/firecracker"
curl -L "$FC_URL" | tar -xz
mv release-${FC_VERSION}-${ARCH}/firecracker-${FC_VERSION}-${ARCH} firecracker
mv release-${FC_VERSION}-${ARCH}/jailer-${FC_VERSION}-${ARCH} jailer
rm -rf release-${FC_VERSION}-${ARCH}
chmod +x firecracker jailer
echo "VERSION=${FC_VERSION}" > VERSION
echo "✓ Firecracker vendored"

echo ""
echo "2. Vendoring kernel..."
cd "$VENDOR_DIR/kernels"
if [ "$ARCH" = "x86_64" ]; then
    KERNEL_URL="https://s3.amazonaws.com/spec.ccfc.min/firecracker-ci/v1.9/x86_64/vmlinux-6.1.bin"
elif [ "$ARCH" = "aarch64" ]; then
    KERNEL_URL="https://s3.amazonaws.com/spec.ccfc.min/firecracker-ci/v1.9/aarch64/vmlinux-6.1.bin"
fi
curl -fsSL -o vmlinux.bin "$KERNEL_URL"
echo "VERSION=6.1" > VERSION
echo "✓ Kernel vendored"

echo ""
echo "3. Vendoring Alpine Linux base..."
cd "$VENDOR_DIR/rootfs"
ALPINE_VERSION="3.19"
if [ "$ARCH" = "x86_64" ]; then
    ALPINE_URL="https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION}/releases/x86_64/alpine-minirootfs-${ALPINE_VERSION}.0-x86_64.tar.gz"
elif [ "$ARCH" = "aarch64" ]; then
    ALPINE_URL="https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION}/releases/aarch64/alpine-minirootfs-${ALPINE_VERSION}.0-aarch64.tar.gz"
fi
curl -fsSL -o alpine-minirootfs.tar.gz "$ALPINE_URL"
echo "VERSION=${ALPINE_VERSION}" > VERSION
echo "✓ Alpine Linux vendored"

echo ""
echo "=== Vendoring Complete ==="
echo ""
echo "Vendored files:"
ls -lh "$VENDOR_DIR/firecracker/"
ls -lh "$VENDOR_DIR/kernels/"
ls -lh "$VENDOR_DIR/rootfs/"
echo ""
echo "Next steps:"
echo "1. Review the vendored files"
echo "2. git add vendor/"
echo "3. git commit -m 'Vendor Firecracker, kernel, and Alpine base'"
echo "4. git push"
echo ""
echo "After this, anyone cloning the repo has everything needed - no external downloads."
