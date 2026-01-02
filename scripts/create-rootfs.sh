#!/bin/bash
# Create a rootfs image with the guest agent
# This script creates an Alpine Linux-based rootfs with Python and the guest agent

set -e

echo "=== Creating Rootfs Image ==="
echo ""

# Configuration
ROOTFS_SIZE_MB=2048
ROOTFS_PATH="/var/lib/firecracker-workspaces/rootfs/default-rootfs.ext4"
MOUNT_POINT="/tmp/rootfs-mount"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
GUEST_AGENT_PATH="$PROJECT_DIR/guest_agent/agent.py"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root (for mounting)"
    echo "Usage: sudo $0"
    exit 1
fi

# Check if rootfs already exists
if [ -f "$ROOTFS_PATH" ]; then
    read -p "Rootfs already exists. Overwrite? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
    rm -f "$ROOTFS_PATH"
fi

# 1. Create sparse file
echo "1. Creating ${ROOTFS_SIZE_MB}MB sparse file..."
dd if=/dev/zero of="$ROOTFS_PATH" bs=1M count=0 seek=$ROOTFS_SIZE_MB
mkfs.ext4 -F "$ROOTFS_PATH"

# 2. Mount the image
echo "2. Mounting image..."
mkdir -p "$MOUNT_POINT"
mount -o loop "$ROOTFS_PATH" "$MOUNT_POINT"

# Cleanup function
cleanup() {
    echo "Cleaning up..."
    umount "$MOUNT_POINT" 2>/dev/null || true
    rmdir "$MOUNT_POINT" 2>/dev/null || true
}
trap cleanup EXIT

# 3. Install Alpine Linux base system
echo "3. Installing Alpine Linux base system..."

# Download and extract Alpine minirootfs
ALPINE_VERSION="3.19"
ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ]; then
    ALPINE_ARCH="x86_64"
elif [ "$ARCH" = "aarch64" ]; then
    ALPINE_ARCH="aarch64"
else
    echo "Unsupported architecture: $ARCH"
    exit 1
fi

ALPINE_URL="https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION}/releases/${ALPINE_ARCH}/alpine-minirootfs-${ALPINE_VERSION}.0-${ALPINE_ARCH}.tar.gz"

echo "   Downloading Alpine minirootfs..."
curl -fsSL "$ALPINE_URL" | tar -xz -C "$MOUNT_POINT"

# 4. Configure the system
echo "4. Configuring system..."

# Set up resolv.conf
echo "nameserver 8.8.8.8" > "$MOUNT_POINT/etc/resolv.conf"

# Set up repositories
cat > "$MOUNT_POINT/etc/apk/repositories" << EOF
https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION}/main
https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION}/community
EOF

# Install packages using chroot
echo "   Installing packages..."
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
apk update
apk add --no-cache \
    python3 \
    py3-pip \
    bash \
    curl \
    wget \
    git \
    openssh-client \
    ca-certificates \
    openrc \
    busybox-initscripts
CHROOT_EOF

# 5. Install guest agent
echo "5. Installing guest agent..."
mkdir -p "$MOUNT_POINT/opt/agent"
cp "$GUEST_AGENT_PATH" "$MOUNT_POINT/opt/agent/agent.py"
chmod +x "$MOUNT_POINT/opt/agent/agent.py"

# Create systemd-style init script for OpenRC
cat > "$MOUNT_POINT/etc/init.d/guest-agent" << 'EOF'
#!/sbin/openrc-run

name="guest-agent"
description="Firecracker Guest Agent"
command="/usr/bin/python3"
command_args="/opt/agent/agent.py"
command_background=true
pidfile="/run/guest-agent.pid"
output_log="/var/log/guest-agent.log"
error_log="/var/log/guest-agent.log"

depend() {
    need localmount
    after bootmisc
}
EOF
chmod +x "$MOUNT_POINT/etc/init.d/guest-agent"

# Enable guest agent on boot
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
rc-update add guest-agent default
CHROOT_EOF

# 6. Create workspace directory
echo "6. Creating workspace directory..."
mkdir -p "$MOUNT_POINT/workspace"
chmod 777 "$MOUNT_POINT/workspace"

# 7. Set up init system
echo "7. Configuring init system..."

# Create inittab for serial console
cat > "$MOUNT_POINT/etc/inittab" << 'EOF'
::sysinit:/sbin/openrc sysinit
::sysinit:/sbin/openrc boot
::wait:/sbin/openrc default
ttyS0::respawn:/sbin/getty -L ttyS0 115200 vt100
::ctrlaltdel:/sbin/reboot
::shutdown:/sbin/openrc shutdown
EOF

# Set root password (for debugging - remove in production)
echo "root:root" | chroot "$MOUNT_POINT" chpasswd

# 8. Final cleanup inside chroot
echo "8. Final cleanup..."
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
rm -rf /var/cache/apk/*
CHROOT_EOF

# Unmount
echo "9. Unmounting..."
sync
umount "$MOUNT_POINT"
rmdir "$MOUNT_POINT"
trap - EXIT

echo ""
echo "=== Rootfs Created Successfully ==="
echo "Path: $ROOTFS_PATH"
echo "Size: $(du -h "$ROOTFS_PATH" | cut -f1)"
echo ""
