#!/bin/bash
# Setup network infrastructure for Firecracker VMs
# This creates a bridge and enables NAT for VM internet access
# Run this once as root before starting the workspace service

set -e

echo "=== Setting up Firecracker Network Infrastructure ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run as root"
    echo "Usage: sudo $0"
    exit 1
fi

# Configuration
BRIDGE_NAME="fc-br0"
BRIDGE_IP="172.16.0.1/16"  # /16 = 65,534 usable IPs
BRIDGE_NETWORK="172.16.0.0/16"

# 1. Check if bridge already exists
echo "1. Checking for existing bridge..."
if ip link show "$BRIDGE_NAME" &> /dev/null; then
    echo "   Bridge $BRIDGE_NAME already exists, skipping creation"
else
    echo "   Creating bridge $BRIDGE_NAME..."
    ip link add name "$BRIDGE_NAME" type bridge
    ip addr add "$BRIDGE_IP" dev "$BRIDGE_NAME"
    ip link set "$BRIDGE_NAME" up
    echo "   ✓ Bridge created"
fi

# 2. Enable IP forwarding
echo "2. Enabling IP forwarding..."
sysctl -w net.ipv4.ip_forward=1 > /dev/null
echo "   ✓ IP forwarding enabled"

# Make IP forwarding persistent
if ! grep -q "^net.ipv4.ip_forward=1" /etc/sysctl.conf 2>/dev/null; then
    echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
    echo "   ✓ IP forwarding made persistent"
fi

# 3. Detect primary internet interface
echo "3. Detecting primary network interface..."
PRIMARY_IFACE=$(ip route | grep default | awk '{print $5}' | head -1)
if [ -z "$PRIMARY_IFACE" ]; then
    echo "   Warning: Could not detect primary interface, assuming eth0"
    PRIMARY_IFACE="eth0"
fi
echo "   Primary interface: $PRIMARY_IFACE"

# 4. Set up NAT (iptables)
echo "4. Setting up NAT for internet access..."

# Check if rule already exists
if ! iptables -t nat -C POSTROUTING -s "$BRIDGE_NETWORK" -o "$PRIMARY_IFACE" -j MASQUERADE 2>/dev/null; then
    iptables -t nat -A POSTROUTING -s "$BRIDGE_NETWORK" -o "$PRIMARY_IFACE" -j MASQUERADE
    echo "   ✓ NAT rule added"
else
    echo "   NAT rule already exists"
fi

# Allow forwarding from bridge to internet
if ! iptables -C FORWARD -i "$BRIDGE_NAME" -o "$PRIMARY_IFACE" -j ACCEPT 2>/dev/null; then
    iptables -A FORWARD -i "$BRIDGE_NAME" -o "$PRIMARY_IFACE" -j ACCEPT
fi

# Allow return traffic
if ! iptables -C FORWARD -i "$PRIMARY_IFACE" -o "$BRIDGE_NAME" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; then
    iptables -A FORWARD -i "$PRIMARY_IFACE" -o "$BRIDGE_NAME" -m state --state RELATED,ESTABLISHED -j ACCEPT
fi

echo "   ✓ NAT configured"

# 5. Set up DHCP server (dnsmasq) for VMs
echo "5. Setting up DHCP server (dnsmasq)..."

# Install dnsmasq if not present
if ! command -v dnsmasq &> /dev/null; then
    echo "   Installing dnsmasq..."
    if command -v apt-get &> /dev/null; then
        apt-get install -y dnsmasq
    elif command -v dnf &> /dev/null; then
        dnf install -y dnsmasq
    elif command -v yum &> /dev/null; then
        yum install -y dnsmasq
    else
        echo "   Error: Could not find package manager to install dnsmasq"
        exit 1
    fi
fi

# Configure dnsmasq for the bridge
cat > /etc/dnsmasq.d/firecracker-bridge.conf << EOF
# DHCP configuration for Firecracker VMs
interface=$BRIDGE_NAME
bind-interfaces

# DHCP range: 172.16.1.0 - 172.16.255.254 (65,024 IPs for ~10k+ VMs)
# Using /16 subnet for massive scale
dhcp-range=172.16.1.0,172.16.255.254,255.255.0.0,12h

# Gateway is the bridge IP
dhcp-option=3,172.16.0.1

# DNS servers (Google DNS)
dhcp-option=6,8.8.8.8,8.8.4.4

# Don't read /etc/resolv.conf or /etc/hosts
no-resolv
no-hosts

# Log DHCP requests (helpful for debugging)
log-dhcp

# Increase DHCP lease cache for many VMs
dhcp-lease-max=65000
EOF

# Restart dnsmasq
systemctl restart dnsmasq
systemctl enable dnsmasq
echo "   ✓ DHCP server configured and started"

# 6. Install iptables-persistent (optional, for persistence across reboots)
echo "6. Checking iptables persistence..."
if ! command -v iptables-save &> /dev/null; then
    echo "   Warning: iptables-save not found, rules won't persist across reboots"
    echo "   Install iptables-persistent: apt install iptables-persistent"
else
    # Save current rules
    if command -v netfilter-persistent &> /dev/null; then
        netfilter-persistent save
        echo "   ✓ iptables rules saved"
    fi
fi

# 7. Create helper script for creating TAP devices
echo "7. Creating TAP device helper script..."
cat > /usr/local/bin/fc-create-tap << 'EOF'
#!/bin/bash
# Helper script to create TAP devices for Firecracker VMs
# Usage: fc-create-tap <tap-name> <bridge-name>

TAP_NAME="${1}"
BRIDGE_NAME="${2:-fc-br0}"

if [ -z "$TAP_NAME" ]; then
    echo "Usage: $0 <tap-name> [bridge-name]"
    exit 1
fi

# Create TAP device if it doesn't exist
if ! ip link show "$TAP_NAME" &> /dev/null; then
    ip tuntap add "$TAP_NAME" mode tap
    ip link set "$TAP_NAME" master "$BRIDGE_NAME"
    ip link set "$TAP_NAME" up
    echo "Created and attached $TAP_NAME to $BRIDGE_NAME"
else
    echo "$TAP_NAME already exists"
fi
EOF
chmod +x /usr/local/bin/fc-create-tap
echo "   ✓ Helper script created: /usr/local/bin/fc-create-tap"

# 8. Set up systemd service for network persistence (optional)
echo "8. Creating systemd service for network persistence..."
cat > /etc/systemd/system/firecracker-network.service << EOF
[Unit]
Description=Firecracker Network Bridge
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'ip link add name $BRIDGE_NAME type bridge || true; ip addr add $BRIDGE_IP dev $BRIDGE_NAME || true; ip link set $BRIDGE_NAME up'
ExecStop=/bin/bash -c 'ip link set $BRIDGE_NAME down; ip link delete $BRIDGE_NAME'

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable firecracker-network.service
systemctl start firecracker-network.service
echo "   ✓ systemd service created and enabled"

echo ""
echo "=== Network Setup Complete ==="
echo ""
echo "Bridge: $BRIDGE_NAME ($BRIDGE_IP)"
echo "VM Network: $BRIDGE_NETWORK"
echo "NAT Interface: $PRIMARY_IFACE"
echo ""
echo "DHCP Range: 172.16.1.0 - 172.16.255.254 (65,024 IPs)"
echo "Gateway: 172.16.0.1"
echo "DNS: 8.8.8.8, 8.8.4.4"
echo ""
echo "Capacity: ~65,000 concurrent VMs with unique IPs"
echo "VMs will have internet access through $PRIMARY_IFACE"
echo ""
echo "Test with:"
echo "  ping 172.16.0.1  (should reach the bridge)"
echo ""
