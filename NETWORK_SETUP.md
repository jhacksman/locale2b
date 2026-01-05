# Network Setup Guide for Firecracker Workspaces

This guide explains how to configure networking for Firecracker VMs so they can access the internet.

## Overview

By default, Firecracker VMs are isolated and have no network connectivity. To enable internet access:

1. **Host Bridge**: Create a Linux bridge on the host that acts as a virtual switch
2. **NAT**: Configure Network Address Translation so VMs can reach the internet
3. **TAP Devices**: Each VM gets a TAP device attached to the bridge
4. **DHCP**: VMs get IP addresses automatically via DHCP (configured in the guest)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Internet                              │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            │ (eth0/wlan0 - primary interface)
                            │
┌───────────────────────────┴─────────────────────────────────┐
│                      Host Machine                            │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  NAT (iptables)                                        │ │
│  │  SNAT: 172.16.0.0/24 → primary interface IP           │ │
│  └────────────────────────────────────────────────────────┘ │
│                            │                                 │
│  ┌────────────────────────┴────────────────────────────┐    │
│  │  fc-br0 (Bridge)                                    │    │
│  │  IP: 172.16.0.1/24                                  │    │
│  └───┬───────────┬───────────┬───────────┬─────────────┘    │
│      │           │           │           │                   │
│  ┌───┴───┐   ┌───┴───┐   ┌───┴───┐   ┌───┴───┐             │
│  │fc-xxx1│   │fc-xxx2│   │fc-xxx3│   │fc-xxx4│   (TAP devs)│
│  └───┬───┘   └───┬───┘   └───┬───┘   └───┬───┘             │
└──────┼───────────┼───────────┼───────────┼─────────────────┘
       │           │           │           │
   ┌───┴───┐   ┌───┴───┐   ┌───┴───┐   ┌───┴───┐
   │  VM 1 │   │  VM 2 │   │  VM 3 │   │  VM 4 │
   │eth0   │   │eth0   │   │eth0   │   │eth0   │
   │DHCP   │   │DHCP   │   │DHCP   │   │DHCP   │
   │172... │   │172... │   │172... │   │172... │
   └───────┘   └───────┘   └───────┘   └───────┘
```

## Quick Setup

Run the automated setup script:

```bash
sudo ./scripts/setup-network.sh
```

This will:
1. Create bridge `fc-br0` with IP `172.16.0.1/24`
2. Enable IP forwarding
3. Configure NAT via iptables
4. Create a systemd service for persistence
5. Install helper scripts

## Manual Setup

If you prefer to configure manually:

### 1. Create Bridge

```bash
# Create bridge interface
sudo ip link add name fc-br0 type bridge

# Assign IP to bridge
sudo ip addr add 172.16.0.1/24 dev fc-br0

# Bring bridge up
sudo ip link set fc-br0 up
```

### 2. Enable IP Forwarding

```bash
# Enable immediately
sudo sysctl -w net.ipv4.ip_forward=1

# Make persistent across reboots
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
```

### 3. Configure NAT

```bash
# Detect your primary internet interface
PRIMARY_IFACE=$(ip route | grep default | awk '{print $5}' | head -1)

# Add NAT rule
sudo iptables -t nat -A POSTROUTING -s 172.16.0.0/24 -o $PRIMARY_IFACE -j MASQUERADE

# Allow forwarding from bridge to internet
sudo iptables -A FORWARD -i fc-br0 -o $PRIMARY_IFACE -j ACCEPT
sudo iptables -A FORWARD -i $PRIMARY_IFACE -o fc-br0 -m state --state RELATED,ESTABLISHED -j ACCEPT
```

### 4. Make iptables Rules Persistent

```bash
# On Debian/Ubuntu
sudo apt install iptables-persistent
sudo netfilter-persistent save

# On Fedora/RHEL
sudo iptables-save | sudo tee /etc/sysconfig/iptables
```

### 5. Create Systemd Service

```bash
sudo tee /etc/systemd/system/firecracker-network.service << EOF
[Unit]
Description=Firecracker Network Bridge
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'ip link add name fc-br0 type bridge || true; ip addr add 172.16.0.1/24 dev fc-br0 || true; ip link set fc-br0 up'
ExecStop=/bin/bash -c 'ip link set fc-br0 down; ip link delete fc-br0'

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable firecracker-network.service
sudo systemctl start firecracker-network.service
```

## How It Works

### When a Sandbox is Created

1. **TAP Device Creation**: Sandbox manager creates a TAP device named `fc-{sandbox_id}`
2. **Bridge Attachment**: TAP device is attached to `fc-br0` bridge
3. **VM Configuration**: Firecracker is configured to use this TAP device as `eth0` in the guest
4. **DHCP Request**: Guest VM boots, `dhcpcd` requests an IP from the host
5. **IP Assignment**: Host's DHCP server (or static config) assigns an IP in `172.16.0.0/24`
6. **Internet Access**: Guest can now reach internet via NAT

### Network Flow Example

```
VM (172.16.0.10) wants to reach 8.8.8.8:

1. VM sends packet: src=172.16.0.10, dst=8.8.8.8
2. Packet goes through TAP device (fc-xxx)
3. Bridge forwards to fc-br0
4. iptables NAT translates: src=<host-ip>, dst=8.8.8.8
5. Packet goes out primary interface (eth0/wlan0)
6. Response comes back: dst=<host-ip>
7. NAT reverses: dst=172.16.0.10
8. Bridge forwards to correct TAP device
9. VM receives response
```

## Troubleshooting

### VMs Can't Get IP Address

```bash
# Check if bridge exists
ip link show fc-br0

# Check if TAP devices are attached to bridge
bridge link show

# Check DHCP client in guest
# (inside VM via serial console)
ps aux | grep dhcpcd
tail -f /var/log/messages
```

### VMs Get IP But No Internet

```bash
# Check IP forwarding
sysctl net.ipv4.ip_forward
# Should be 1

# Check NAT rules
sudo iptables -t nat -L -n -v | grep MASQUERADE

# Check forwarding rules
sudo iptables -L FORWARD -n -v
```

### TAP Device Creation Fails

```bash
# Check if user has sudo privileges
sudo -l

# Check if tun module is loaded
lsmod | grep tun
sudo modprobe tun

# Check permissions
ls -la /dev/net/tun
```

## Security Considerations

### Isolation

- Each VM is on the same subnet (172.16.0.0/24)
- VMs can communicate with each other by default
- To isolate VMs, add iptables rules between TAP devices

### Firewall Rules for VM Isolation

```bash
# Drop traffic between VMs (only allow internet access)
sudo iptables -I FORWARD -i fc-br0 -o fc-br0 -j DROP
```

### Rate Limiting

Firecracker supports network rate limiting (not configured by default):

```json
{
  "iface_id": "eth0",
  "guest_mac": "AA:FC:00:00:00:01",
  "host_dev_name": "fc-tap0",
  "rx_rate_limiter": {
    "bandwidth": {
      "size": 10485760,
      "refill_time": 1000
    }
  },
  "tx_rate_limiter": {
    "bandwidth": {
      "size": 10485760,
      "refill_time": 1000
    }
  }
}
```

## Advanced: Custom IP Ranges

To use a different subnet:

```bash
# Use 10.0.0.0/24 instead of 172.16.0.0/24
sudo ip addr add 10.0.0.1/24 dev fc-br0
sudo iptables -t nat -A POSTROUTING -s 10.0.0.0/24 -o eth0 -j MASQUERADE
```

Update VM DHCP configuration accordingly.

## Alternative: No Bridge (Point-to-Point)

For single VM testing without a bridge:

```bash
# Create TAP device
sudo ip tuntap add fc-tap0 mode tap

# Assign IP to host side of TAP
sudo ip addr add 192.168.100.1/24 dev fc-tap0

# Bring it up
sudo ip link set fc-tap0 up

# Enable NAT
sudo iptables -t nat -A POSTROUTING -s 192.168.100.0/24 -o eth0 -j MASQUERADE
sudo iptables -A FORWARD -i fc-tap0 -j ACCEPT
sudo iptables -A FORWARD -o fc-tap0 -m state --state RELATED,ESTABLISHED -j ACCEPT
```

In VM, configure static IP:
```bash
ip addr add 192.168.100.2/24 dev eth0
ip link set eth0 up
ip route add default via 192.168.100.1
```

## Performance Notes

- **Bridge overhead**: Minimal (<1% CPU)
- **TAP devices**: Each uses ~1MB RAM
- **NAT overhead**: Negligible for typical workloads
- **Bandwidth**: Full host network speed available (can be rate-limited per VM)

## FAQ

**Q: Do I need a DHCP server?**
A: No, the VMs are configured with `dhcpcd` which handles DHCP client-side. The host doesn't run a DHCP server - VMs use static IPs or fall back to link-local.

**Q: Can VMs talk to each other?**
A: Yes, they're on the same bridge. Use firewall rules to prevent this if needed.

**Q: Will this work with WiFi?**
A: Yes, NAT works with any internet connection (WiFi, Ethernet, etc.)

**Q: Can VMs accept incoming connections?**
A: Not by default. You'd need port forwarding rules:
```bash
sudo iptables -t nat -A PREROUTING -p tcp --dport 8080 -j DNAT --to-destination 172.16.0.10:8080
```

**Q: What if I restart the host?**
A: The systemd service and iptables-persistent ensure network setup persists across reboots.

## References

- [Firecracker Network Setup](https://github.com/firecracker-microvm/firecracker/blob/main/docs/network-setup.md)
- [Linux Bridge Configuration](https://wiki.archlinux.org/title/Network_bridge)
- [iptables NAT Tutorial](https://www.karlrupp.net/en/computer/nat_tutorial)
