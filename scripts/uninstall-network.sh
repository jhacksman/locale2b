#!/bin/bash
# Uninstall network infrastructure for Firecracker VMs
# This removes the bridge, NAT rules, and DHCP server created by setup-network.sh
# Run this as root before re-running setup-network.sh

set -e

echo "=== Uninstalling Firecracker Network Infrastructure ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run as root"
    echo "Usage: sudo $0"
    exit 1
fi

# Configuration (must match setup-network.sh)
BRIDGE_NAME="fc-br0"
BRIDGE_IP="172.16.0.1/16"
BRIDGE_NETWORK="172.16.0.0/16"

# Detect primary internet interface
PRIMARY_IFACE=$(ip route | grep default | awk '{print $5}' | head -1)
if [ -z "$PRIMARY_IFACE" ]; then
    PRIMARY_IFACE="eth0"
fi

echo "1. Stopping and removing systemd services..."

# Stop and disable firecracker-network service
if systemctl is-active firecracker-network.service &> /dev/null; then
    systemctl stop firecracker-network.service
    echo "   Stopped firecracker-network.service"
fi
if systemctl is-enabled firecracker-network.service &> /dev/null; then
    systemctl disable firecracker-network.service
    echo "   Disabled firecracker-network.service"
fi
if [ -f /etc/systemd/system/firecracker-network.service ]; then
    rm /etc/systemd/system/firecracker-network.service
    echo "   Removed firecracker-network.service"
fi

# Stop and disable dnsmasq services
for svc in dnsmasq firecracker-dnsmasq; do
    if systemctl is-active "$svc" &> /dev/null; then
        systemctl stop "$svc"
        echo "   Stopped $svc"
    fi
    if systemctl is-enabled "$svc" &> /dev/null; then
        systemctl disable "$svc" 2>/dev/null || true
        echo "   Disabled $svc"
    fi
done

if [ -f /etc/systemd/system/firecracker-dnsmasq.service ]; then
    rm /etc/systemd/system/firecracker-dnsmasq.service
    echo "   Removed firecracker-dnsmasq.service"
fi

systemctl daemon-reload
echo "   Reloaded systemd"

echo ""
echo "2. Removing dnsmasq configuration..."
if [ -f /etc/dnsmasq.d/firecracker-bridge.conf ]; then
    rm /etc/dnsmasq.d/firecracker-bridge.conf
    echo "   Removed /etc/dnsmasq.d/firecracker-bridge.conf"
else
    echo "   No dnsmasq config found"
fi

echo ""
echo "3. Removing iptables rules..."

# Remove NAT rule
if iptables -t nat -C POSTROUTING -s "$BRIDGE_NETWORK" -o "$PRIMARY_IFACE" -j MASQUERADE 2>/dev/null; then
    iptables -t nat -D POSTROUTING -s "$BRIDGE_NETWORK" -o "$PRIMARY_IFACE" -j MASQUERADE
    echo "   Removed NAT MASQUERADE rule"
fi

# Remove FORWARD rules
if iptables -C FORWARD -i "$BRIDGE_NAME" -o "$PRIMARY_IFACE" -j ACCEPT 2>/dev/null; then
    iptables -D FORWARD -i "$BRIDGE_NAME" -o "$PRIMARY_IFACE" -j ACCEPT
    echo "   Removed FORWARD rule (bridge -> internet)"
fi

if iptables -C FORWARD -i "$PRIMARY_IFACE" -o "$BRIDGE_NAME" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; then
    iptables -D FORWARD -i "$PRIMARY_IFACE" -o "$BRIDGE_NAME" -m state --state RELATED,ESTABLISHED -j ACCEPT
    echo "   Removed FORWARD rule (return traffic)"
fi

# Save iptables rules if netfilter-persistent is available
if command -v netfilter-persistent &> /dev/null; then
    netfilter-persistent save
    echo "   Saved iptables rules"
fi

echo ""
echo "4. Removing TAP devices attached to bridge..."
# Find and remove any TAP devices attached to the bridge
for tap in $(ip link show type tun 2>/dev/null | grep -oP 'fc-[a-f0-9]+' || true); do
    ip link delete "$tap" 2>/dev/null && echo "   Removed TAP device: $tap" || true
done

echo ""
echo "5. Removing bridge interface..."
if ip link show "$BRIDGE_NAME" &> /dev/null; then
    ip link set "$BRIDGE_NAME" down
    ip link delete "$BRIDGE_NAME"
    echo "   Removed bridge $BRIDGE_NAME"
else
    echo "   Bridge $BRIDGE_NAME not found"
fi

echo ""
echo "6. Removing helper script..."
if [ -f /usr/local/bin/fc-create-tap ]; then
    rm /usr/local/bin/fc-create-tap
    echo "   Removed /usr/local/bin/fc-create-tap"
else
    echo "   Helper script not found"
fi

echo ""
echo "=== Network Uninstall Complete ==="
echo ""
echo "The following were removed:"
echo "  - Bridge: $BRIDGE_NAME"
echo "  - NAT/FORWARD iptables rules"
echo "  - dnsmasq configuration"
echo "  - systemd services (firecracker-network, firecracker-dnsmasq)"
echo "  - TAP devices (fc-*)"
echo "  - Helper script (/usr/local/bin/fc-create-tap)"
echo ""
echo "Note: IP forwarding (net.ipv4.ip_forward=1) was NOT disabled."
echo "      dnsmasq package was NOT uninstalled."
echo ""
echo "To reinstall, run: sudo ./scripts/setup-network.sh"
echo ""
