#!/bin/bash
# Install locale2b as a systemd service
# This script must be run with sudo

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/locale2b.service"
INSTALL_PATH="/etc/systemd/system/locale2b.service"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo: sudo $0"
    exit 1
fi

# Check if service file exists
if [ ! -f "$SERVICE_FILE" ]; then
    echo "Error: Service file not found at $SERVICE_FILE"
    exit 1
fi

# Detect the current user (the one who ran sudo)
ACTUAL_USER="${SUDO_USER:-$(whoami)}"
ACTUAL_HOME=$(eval echo "~$ACTUAL_USER")
LOCALE2B_DIR="$(dirname "$SCRIPT_DIR")"

echo "Installing locale2b service..."
echo "  User: $ACTUAL_USER"
echo "  Home: $ACTUAL_HOME"
echo "  Locale2b directory: $LOCALE2B_DIR"

# Create a customized service file
cat > "$INSTALL_PATH" << EOF
[Unit]
Description=Locale2b Firecracker Workspace Service
Documentation=https://github.com/jhacksman/locale2b
After=network.target

[Service]
Type=simple
User=$ACTUAL_USER
Group=$ACTUAL_USER
WorkingDirectory=$LOCALE2B_DIR

# Python virtual environment
Environment="PATH=$LOCALE2B_DIR/.venv/bin:/usr/local/bin:/usr/bin:/bin"

# Start the service
ExecStart=$LOCALE2B_DIR/.venv/bin/uvicorn workspace_service.main:app --host 0.0.0.0 --port 8080

# Restart on failure
Restart=always
RestartSec=5

# Capabilities for TAP device management (instead of sudo)
# CAP_NET_ADMIN: Create/configure TAP devices and attach to bridge
AmbientCapabilities=CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_ADMIN

# KVM access for Firecracker (user must be in kvm group or have ACL)
SupplementaryGroups=kvm

[Install]
WantedBy=multi-user.target
EOF

echo "Service file installed to $INSTALL_PATH"

# Reload systemd
systemctl daemon-reload
echo "Systemd daemon reloaded"

# Enable the service
systemctl enable locale2b
echo "Service enabled"

# Start the service
systemctl start locale2b
echo "Service started"

# Show status
echo ""
echo "Service status:"
systemctl status locale2b --no-pager || true

echo ""
echo "Installation complete!"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status locale2b   - Check service status"
echo "  sudo systemctl restart locale2b  - Restart the service"
echo "  sudo journalctl -u locale2b -f   - View logs"
