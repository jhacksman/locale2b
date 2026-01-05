#!/bin/bash
# Template Builder for Firecracker Workspace Service
# Builds custom rootfs images with different software stacks

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ROOTFS_DIR="/var/lib/firecracker-workspaces/rootfs"
TEMP_DIR="/tmp/firecracker-rootfs-build"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Template definitions
declare -A TEMPLATES

TEMPLATES[minimal]="Alpine base + guest agent only (50MB, fastest boot)"
TEMPLATES[python]="Python 3.11 + pip + common packages (200MB)"
TEMPLATES[node]="Node.js 20 LTS + npm + yarn (250MB)"
TEMPLATES[golang]="Go 1.21 + common tools (400MB)"
TEMPLATES[rust]="Rust + Cargo + rustup (500MB)"
TEMPLATES[java]="OpenJDK 17 + Maven + Gradle (450MB)"
TEMPLATES[ruby]="Ruby 3.2 + bundler + common gems (300MB)"
TEMPLATES[fullstack]="Python + Node.js + Go all-in-one (600MB)"
TEMPLATES[data-science]="Python + Jupyter + pandas + numpy + scipy (800MB)"
TEMPLATES[devops]="Docker + kubectl + terraform + ansible (700MB)"

usage() {
    echo -e "${GREEN}Usage: $0 <template-name>${NC}"
    echo ""
    echo "Available templates:"
    for template in "${!TEMPLATES[@]}"; do
        echo -e "  ${YELLOW}$template${NC} - ${TEMPLATES[$template]}"
    done | sort
    echo ""
    echo "Examples:"
    echo "  sudo $0 python     # Build Python development template"
    echo "  sudo $0 fullstack  # Build all-in-one template"
    echo "  sudo $0 minimal    # Build minimal template"
    exit 1
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root${NC}"
    echo "Please run: sudo $0 $@"
    exit 1
fi

# Parse arguments
TEMPLATE="${1:-}"
if [ -z "$TEMPLATE" ]; then
    usage
fi

if [ -z "${TEMPLATES[$TEMPLATE]}" ]; then
    echo -e "${RED}Error: Unknown template '$TEMPLATE'${NC}"
    echo ""
    usage
fi

echo -e "${GREEN}=== Building Firecracker Rootfs Template: $TEMPLATE ===${NC}"
echo -e "Description: ${TEMPLATES[$TEMPLATE]}"
echo ""

# Create temp directory
rm -rf "$TEMP_DIR"
mkdir -p "$TEMP_DIR"
mkdir -p "$ROOTFS_DIR"

ROOTFS_FILE="$TEMP_DIR/rootfs.ext4"
MOUNT_POINT="$TEMP_DIR/mnt"

# Determine size based on template
case "$TEMPLATE" in
    minimal)
        SIZE_MB=512
        ;;
    python|node|ruby)
        SIZE_MB=1024
        ;;
    golang|java)
        SIZE_MB=1536
        ;;
    fullstack|devops)
        SIZE_MB=2048
        ;;
    data-science)
        SIZE_MB=2560
        ;;
    rust)
        SIZE_MB=1536
        ;;
    *)
        SIZE_MB=1024
        ;;
esac

echo -e "${YELLOW}Creating ${SIZE_MB}MB ext4 image...${NC}"
dd if=/dev/zero of="$ROOTFS_FILE" bs=1M count="$SIZE_MB" status=progress
mkfs.ext4 -F "$ROOTFS_FILE"

# Mount the image
mkdir -p "$MOUNT_POINT"
mount "$ROOTFS_FILE" "$MOUNT_POINT"

# Ensure cleanup on exit
cleanup() {
    echo -e "${YELLOW}Cleaning up...${NC}"
    umount "$MOUNT_POINT" 2>/dev/null || true
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

# Install Alpine base system
echo -e "${YELLOW}Installing Alpine Linux base...${NC}"
ALPINE_VERSION="v3.19"
ALPINE_MIRROR="https://dl-cdn.alpinelinux.org/alpine"

# Detect architecture
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)
        ALPINE_ARCH="x86_64"
        ;;
    aarch64)
        ALPINE_ARCH="aarch64"
        ;;
    *)
        echo -e "${RED}Unsupported architecture: $ARCH${NC}"
        exit 1
        ;;
esac

# Bootstrap Alpine
apk -X "${ALPINE_MIRROR}/${ALPINE_VERSION}/main" \
    -U --allow-untrusted --root "$MOUNT_POINT" --initdb add \
    alpine-base alpine-conf openrc util-linux coreutils

# Configure basic system
echo -e "${YELLOW}Configuring base system...${NC}"

# Set up fstab
cat > "$MOUNT_POINT/etc/fstab" << 'EOF'
/dev/vda    /           ext4    defaults,noatime    0 1
proc        /proc       proc    defaults            0 0
sysfs       /sys        sysfs   defaults            0 0
tmpfs       /tmp        tmpfs   defaults            0 0
devpts      /dev/pts    devpts  gid=5,mode=620      0 0
EOF

# Set up networking
cat > "$MOUNT_POINT/etc/network/interfaces" << 'EOF'
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
    hostname localhost
EOF

# Configure DNS
cat > "$MOUNT_POINT/etc/resolv.conf" << 'EOF'
nameserver 8.8.8.8
nameserver 8.8.4.4
EOF

# Enable services
ln -sf /etc/init.d/networking "$MOUNT_POINT/etc/runlevels/boot/networking" || true
ln -sf /etc/init.d/hostname "$MOUNT_POINT/etc/runlevels/boot/hostname" || true

# Set root password
echo 'root:firecracker' | chroot "$MOUNT_POINT" chpasswd

# Install common tools for all templates
echo -e "${YELLOW}Installing common tools...${NC}"
chroot "$MOUNT_POINT" apk add --no-cache \
    bash \
    curl \
    wget \
    git \
    openssh-client \
    ca-certificates \
    tzdata \
    nano \
    vim \
    htop \
    procps \
    net-tools \
    iputils \
    bind-tools

# Copy guest agent
echo -e "${YELLOW}Installing guest agent...${NC}"
mkdir -p "$MOUNT_POINT/opt/agent"
cp "$PROJECT_DIR/guest_agent/agent.py" "$MOUNT_POINT/opt/agent/"

# Make sure Python 3 is installed for the agent
chroot "$MOUNT_POINT" apk add --no-cache python3

# Create systemd-style init script for guest agent
cat > "$MOUNT_POINT/etc/init.d/guest-agent" << 'EOF'
#!/sbin/openrc-run

name="Guest Agent"
command="/usr/bin/python3"
command_args="/opt/agent/agent.py"
command_background=true
pidfile="/run/guest-agent.pid"

depend() {
    need net
    after firewall
}
EOF
chmod +x "$MOUNT_POINT/etc/init.d/guest-agent"
ln -sf /etc/init.d/guest-agent "$MOUNT_POINT/etc/runlevels/default/guest-agent"

# Create workspace directory
mkdir -p "$MOUNT_POINT/workspace"
chmod 777 "$MOUNT_POINT/workspace"

# Template-specific installations
echo -e "${YELLOW}Installing template-specific packages: $TEMPLATE${NC}"

case "$TEMPLATE" in
    minimal)
        # Already done - just base + agent
        ;;

    python)
        chroot "$MOUNT_POINT" apk add --no-cache \
            python3 \
            python3-dev \
            py3-pip \
            py3-virtualenv \
            gcc \
            musl-dev \
            linux-headers

        # Install common Python packages
        chroot "$MOUNT_POINT" pip3 install --no-cache-dir --break-system-packages \
            requests \
            httpx \
            fastapi \
            uvicorn \
            pydantic \
            python-dotenv \
            pytest \
            black \
            ruff
        ;;

    node)
        chroot "$MOUNT_POINT" apk add --no-cache nodejs npm

        # Install Yarn
        chroot "$MOUNT_POINT" npm install -g yarn pnpm

        # Common global packages
        chroot "$MOUNT_POINT" npm install -g \
            typescript \
            ts-node \
            nodemon \
            prettier \
            eslint
        ;;

    golang)
        chroot "$MOUNT_POINT" apk add --no-cache go

        # Set up Go environment
        mkdir -p "$MOUNT_POINT/root/go"
        echo 'export GOPATH=/root/go' >> "$MOUNT_POINT/root/.profile"
        echo 'export PATH=$PATH:/usr/lib/go/bin:$GOPATH/bin' >> "$MOUNT_POINT/root/.profile"
        ;;

    rust)
        # Install Rust dependencies
        chroot "$MOUNT_POINT" apk add --no-cache \
            rust \
            cargo \
            gcc \
            musl-dev

        # Configure cargo
        mkdir -p "$MOUNT_POINT/root/.cargo"
        ;;

    java)
        chroot "$MOUNT_POINT" apk add --no-cache \
            openjdk17 \
            maven \
            gradle

        # Set JAVA_HOME
        echo 'export JAVA_HOME=/usr/lib/jvm/default-jvm' >> "$MOUNT_POINT/root/.profile"
        echo 'export PATH=$PATH:$JAVA_HOME/bin' >> "$MOUNT_POINT/root/.profile"
        ;;

    ruby)
        chroot "$MOUNT_POINT" apk add --no-cache \
            ruby \
            ruby-dev \
            ruby-bundler \
            ruby-json \
            build-base

        # Install common gems
        chroot "$MOUNT_POINT" gem install --no-document \
            rails \
            sinatra \
            rake \
            rspec
        ;;

    fullstack)
        # Install Python
        chroot "$MOUNT_POINT" apk add --no-cache \
            python3 py3-pip gcc musl-dev linux-headers
        chroot "$MOUNT_POINT" pip3 install --no-cache-dir --break-system-packages \
            requests httpx fastapi uvicorn pydantic pytest

        # Install Node.js
        chroot "$MOUNT_POINT" apk add --no-cache nodejs npm
        chroot "$MOUNT_POINT" npm install -g yarn typescript ts-node prettier

        # Install Go
        chroot "$MOUNT_POINT" apk add --no-cache go
        mkdir -p "$MOUNT_POINT/root/go"
        echo 'export GOPATH=/root/go' >> "$MOUNT_POINT/root/.profile"
        echo 'export PATH=$PATH:/usr/lib/go/bin:$GOPATH/bin' >> "$MOUNT_POINT/root/.profile"
        ;;

    data-science)
        # Install Python and data science stack
        chroot "$MOUNT_POINT" apk add --no-cache \
            python3 \
            python3-dev \
            py3-pip \
            py3-numpy \
            py3-scipy \
            py3-pandas \
            gcc \
            g++ \
            gfortran \
            musl-dev \
            linux-headers \
            openblas-dev \
            lapack-dev

        # Install Jupyter and ML packages
        chroot "$MOUNT_POINT" pip3 install --no-cache-dir --break-system-packages \
            jupyter \
            jupyterlab \
            matplotlib \
            seaborn \
            scikit-learn \
            ipython
        ;;

    devops)
        # Install Docker (CLI only, no daemon in VM by default)
        chroot "$MOUNT_POINT" apk add --no-cache \
            docker-cli \
            docker-compose

        # Install kubectl (download latest)
        KUBECTL_VERSION=$(wget -qO- https://dl.k8s.io/release/stable.txt)
        wget -O "$MOUNT_POINT/usr/local/bin/kubectl" \
            "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ALPINE_ARCH}/kubectl"
        chmod +x "$MOUNT_POINT/usr/local/bin/kubectl"

        # Install Terraform
        chroot "$MOUNT_POINT" apk add --no-cache terraform

        # Install Ansible
        chroot "$MOUNT_POINT" apk add --no-cache \
            ansible \
            py3-jinja2

        # Install other DevOps tools
        chroot "$MOUNT_POINT" apk add --no-cache \
            helm \
            jq \
            yq
        ;;
esac

# Create a template info file
cat > "$MOUNT_POINT/etc/template-info" << EOF
TEMPLATE_NAME=$TEMPLATE
TEMPLATE_DESC=${TEMPLATES[$TEMPLATE]}
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
ARCH=$ALPINE_ARCH
ALPINE_VERSION=$ALPINE_VERSION
EOF

# Create a welcome message
cat > "$MOUNT_POINT/etc/motd" << EOF
========================================
Firecracker Workspace - $TEMPLATE
========================================
Template: ${TEMPLATES[$TEMPLATE]}
Architecture: $ALPINE_ARCH
Build Date: $(date -u +"%Y-%m-%d")

Network: eth0 (DHCP enabled)
Workspace: /workspace

Guest Agent: Running on vsock port 5000
========================================
EOF

# Clean up package cache
echo -e "${YELLOW}Cleaning up...${NC}"
rm -rf "$MOUNT_POINT/var/cache/apk"/*
rm -rf "$MOUNT_POINT/tmp"/*

# Unmount
sync
umount "$MOUNT_POINT"

# Move to final location
FINAL_PATH="$ROOTFS_DIR/${TEMPLATE}-rootfs.ext4"
mv "$ROOTFS_FILE" "$FINAL_PATH"
chown ${SUDO_USER:-root}:${SUDO_USER:-root} "$FINAL_PATH"

echo -e "${GREEN}✓ Template '$TEMPLATE' built successfully!${NC}"
echo -e "Location: $FINAL_PATH"
echo -e "Size: $(du -h "$FINAL_PATH" | cut -f1)"
echo ""
echo "Test it with:"
echo "  curl -X POST http://localhost:8080/sandboxes \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"template\": \"$TEMPLATE\", \"memory_mb\": 512}'"
