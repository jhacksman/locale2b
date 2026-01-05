#!/bin/bash
# Create the Ultimate All-in-One Rootfs Image
# Includes: Python, Node.js, Go, Rust, Ruby, Docker CLI, database clients, and more
# Network enabled by default (DHCP)

set -e

echo "=== Creating Ultimate All-in-One Rootfs Image ==="
echo ""

# Configuration
ROOTFS_SIZE_MB=3072  # Increased to 3GB for all runtimes
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
echo "   Installing base packages..."
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
apk update
apk add --no-cache \
    bash \
    curl \
    wget \
    git \
    openssh-client \
    ca-certificates \
    openrc \
    nano \
    vim \
    htop \
    procps \
    net-tools \
    iputils \
    bind-tools \
    iproute2 \
    iptables \
    dhcpcd
CHROOT_EOF

# Install Python stack
echo "   Installing Python 3.11 and packages..."
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
apk add --no-cache \
    python3 \
    python3-dev \
    py3-pip \
    py3-virtualenv \
    gcc \
    g++ \
    musl-dev \
    linux-headers \
    libffi-dev \
    openssl-dev

# Install common Python packages
pip3 install --no-cache-dir --break-system-packages \
    requests \
    httpx \
    fastapi \
    uvicorn \
    pydantic \
    python-dotenv \
    pytest \
    pytest-asyncio \
    black \
    ruff \
    aiohttp \
    beautifulsoup4 \
    lxml
CHROOT_EOF

# Install Node.js stack
echo "   Installing Node.js 20 LTS..."
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
apk add --no-cache nodejs npm

# Install Yarn and pnpm globally
npm install -g yarn pnpm

# Install common Node packages globally
npm install -g \
    typescript \
    ts-node \
    nodemon \
    prettier \
    eslint \
    pm2
CHROOT_EOF

# Install Go
echo "   Installing Go 1.21..."
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
apk add --no-cache go

# Set up Go environment in root's profile
mkdir -p /root/go
echo 'export GOPATH=/root/go' >> /root/.profile
echo 'export PATH=$PATH:/usr/lib/go/bin:$GOPATH/bin' >> /root/.profile
CHROOT_EOF

# Install Rust
echo "   Installing Rust and Cargo..."
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
apk add --no-cache \
    rust \
    cargo

mkdir -p /root/.cargo
CHROOT_EOF

# Install Ruby
echo "   Installing Ruby 3.2..."
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
apk add --no-cache \
    ruby \
    ruby-dev \
    ruby-bundler \
    ruby-json \
    build-base
CHROOT_EOF

# Install database clients
echo "   Installing database clients..."
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
apk add --no-cache \
    postgresql-client \
    mysql-client \
    sqlite \
    redis
CHROOT_EOF

# Install Docker CLI (no daemon, just client)
echo "   Installing Docker CLI..."
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
apk add --no-cache docker-cli
CHROOT_EOF

# Install additional dev tools
echo "   Installing additional dev tools..."
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
apk add --no-cache \
    make \
    cmake \
    autoconf \
    automake \
    libtool \
    jq \
    yq \
    tmux \
    screen \
    strace \
    lsof \
    file \
    tree \
    zip \
    unzip \
    tar \
    gzip \
    bzip2 \
    xz
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

# 6. Configure networking
echo "6. Configuring networking (DHCP enabled)..."

# Set up network interfaces for DHCP
cat > "$MOUNT_POINT/etc/network/interfaces" << 'EOF'
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
    hostname firecracker-vm
EOF

# Enable networking service
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
rc-update add networking boot
rc-update add dhcpcd default
CHROOT_EOF

# 7. Create workspace directory
echo "7. Creating workspace directory..."
mkdir -p "$MOUNT_POINT/workspace"
chmod 777 "$MOUNT_POINT/workspace"

# 8. Create welcome message
echo "8. Creating welcome message..."
cat > "$MOUNT_POINT/etc/motd" << 'EOF'
========================================
Firecracker Workspace - Ultimate Edition
========================================
Languages:
  - Python 3.11 (with pip, requests, fastapi, pytest)
  - Node.js 20 LTS (with npm, yarn, pnpm, typescript)
  - Go 1.21 (with GOPATH configured)
  - Rust (with cargo)
  - Ruby 3.2 (with bundler)

Tools:
  - Git, curl, wget, vim, nano
  - Docker CLI, database clients
  - Build tools (gcc, make, cmake)
  - Networking (dhcpcd, dig, ping)

Network: eth0 (DHCP enabled)
Workspace: /workspace
Guest Agent: vsock port 5000
========================================
EOF

# 9. Set up init system
echo "9. Configuring init system..."

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

# 10. Final cleanup inside chroot
echo "10. Final cleanup..."
chroot "$MOUNT_POINT" /bin/sh << 'CHROOT_EOF'
rm -rf /var/cache/apk/*
rm -rf /tmp/*
rm -rf /root/.npm
rm -rf /root/.cache
CHROOT_EOF

# Unmount
echo "11. Unmounting..."
sync
umount "$MOUNT_POINT"
rmdir "$MOUNT_POINT"
trap - EXIT

echo ""
echo "=== Ultimate All-in-One Rootfs Created Successfully ==="
echo "Path: $ROOTFS_PATH"
echo "Size: $(du -h "$ROOTFS_PATH" | cut -f1)"
echo ""
echo "Includes:"
echo "  ✓ Python 3.11 + pip + common packages"
echo "  ✓ Node.js 20 LTS + npm/yarn/pnpm + TypeScript"
echo "  ✓ Go 1.21"
echo "  ✓ Rust + Cargo"
echo "  ✓ Ruby 3.2 + bundler"
echo "  ✓ Database clients (psql, mysql, sqlite, redis)"
echo "  ✓ Docker CLI"
echo "  ✓ Build tools (gcc, make, cmake)"
echo "  ✓ Network enabled (DHCP on eth0)"
echo "  ✓ Git + all dev tools"
echo ""
