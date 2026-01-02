# Firecracker on Apple Silicon Mac Mini - Setup Guide

**Status:** ✅ **FEASIBLE - GO**

This guide covers setting up locale2b (Firecracker microVM sandbox service) on an Apple Silicon Mac Mini running Asahi Linux.

## Executive Summary

**Good News:** Firecracker works on ARM64 and Asahi Linux has KVM support. This is a viable path.

**Key Facts:**
- Firecracker officially supports aarch64 (since v0.24)
- Asahi Linux has working KVM virtualization (functional since 2021)
- vhost_vsock kernel module is available on ARM64 Linux (kernel 4.8+)
- Fedora Asahi Remix 41 is the recommended, production-ready distribution

**Timeline Estimate:** 1-2 days for initial setup and smoke test

---

## Part 1: Install Asahi Linux (Fedora Asahi Remix)

### Prerequisites

- Apple Silicon Mac Mini (M1, M2, M3 series - all supported)
- macOS Ventura 13.5+ or macOS Sonoma 14.2+ currently installed
- At least 70GB free disk space for the Linux partition
- Internet connection

### Installation Steps

1. **Boot into macOS and open Terminal**

2. **Run the Asahi Linux installer:**
   ```bash
   curl https://alx.sh | sh
   ```

3. **Follow the interactive installer:**
   - Choose "Fedora Asahi Remix" when prompted
   - Allocate at least 70GB for the Linux partition (recommend 100GB+ for development)
   - The installer will:
     - Resize your macOS partition
     - Install the Asahi Linux bootloader
     - Download and install Fedora Asahi Remix 41 (KDE Plasma 6.2)
     - Configure boot menu to dual-boot macOS and Linux

4. **Reboot and select "Fedora Asahi Remix" from the boot menu**
   - Hold down the power button during startup to access the boot picker
   - Select the Fedora Asahi Remix boot option

5. **Complete initial Fedora setup:**
   - Create your user account
   - Set timezone and keyboard layout
   - Wait for system updates to complete

### Post-Installation Verification

```bash
# Verify you're on ARM64
uname -m
# Should output: aarch64

# Check kernel version (should be 6.x or newer)
uname -r

# Verify Fedora Asahi Remix
cat /etc/os-release
# Should show: Fedora Asahi Remix 41 or newer
```

---

## Part 2: Verify KVM and Virtualization Support

### Check KVM Availability

```bash
# Check if KVM module is loaded
lsmod | grep kvm
# Should show: kvm, kvm_apple (or similar)

# Verify /dev/kvm exists
ls -la /dev/kvm
# Should show: crw-rw---- 1 root kvm ... /dev/kvm
```

### Grant KVM Access to Your User

```bash
# Add your user to the kvm group
sudo usermod -a -G kvm $USER

# Or use ACL (alternative method)
sudo setfacl -m u:${USER}:rw /dev/kvm

# Verify access
groups
# Should include 'kvm' in the list

# Log out and log back in for group changes to take effect
```

### Load vhost_vsock Module

```bash
# Load the vhost_vsock module (needed for Firecracker host-guest communication)
sudo modprobe vhost_vsock

# Verify it's loaded
lsmod | grep vhost_vsock
# Should show: vhost_vsock ...

# Make it load on boot
echo "vhost_vsock" | sudo tee /etc/modules-load.d/vhost_vsock.conf
```

---

## Part 3: Install Firecracker (aarch64)

### Install Dependencies

```bash
# Update system
sudo dnf update -y

# Install required packages
sudo dnf install -y \
    curl \
    wget \
    git \
    python3 \
    python3-pip \
    python3-virtualenv \
    gcc \
    make \
    flex \
    bison \
    elfutils-libelf-devel \
    openssl-devel \
    bc \
    ncurses-devel
```

### Download Firecracker Binary

```bash
# Get latest Firecracker release for aarch64
RELEASE_URL="https://github.com/firecracker-microvm/firecracker/releases"
LATEST=$(basename $(curl -fsSLI -o /dev/null -w %{url_effective} ${RELEASE_URL}/latest))

echo "Downloading Firecracker ${LATEST} for aarch64..."

# Download and extract
curl -L ${RELEASE_URL}/download/${LATEST}/firecracker-${LATEST}-aarch64.tgz | tar -xz

# Install binaries
sudo mv release-${LATEST}-aarch64/firecracker-${LATEST}-aarch64 /usr/local/bin/firecracker
sudo mv release-${LATEST}-aarch64/jailer-${LATEST}-aarch64 /usr/local/bin/jailer
sudo chmod +x /usr/local/bin/firecracker /usr/local/bin/jailer

# Clean up
rm -rf release-${LATEST}-aarch64

# Verify installation
firecracker --version
```

---

## Part 4: Set Up locale2b

### Clone the Repository

```bash
cd ~
git clone https://github.com/jhacksman/locale2b.git
cd locale2b
```

### Create Directory Structure

```bash
# Create Firecracker workspace directories
sudo mkdir -p /var/lib/firecracker-workspaces/{kernels,rootfs,sandboxes,snapshots}
sudo chown -R ${USER}:${USER} /var/lib/firecracker-workspaces
```

### Obtain ARM64 Kernel

You have two options:

**Option A: Use Pre-built Kernel (Fastest)**

```bash
# Firecracker maintains pre-built kernels for aarch64
curl -fsSL -o /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin \
    https://s3.amazonaws.com/spec.ccfc.min/img/quickstart_guide/aarch64/kernels/vmlinux.bin

# Verify download
ls -lh /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin
```

**Option B: Build Custom Kernel (More Control)**

See Appendix A for detailed kernel build instructions.

### Create ARM64 Guest Rootfs

The existing `scripts/create-rootfs.sh` already supports aarch64 (see lines 59-67), so you can run it directly:

```bash
# Install required tools for rootfs creation
sudo dnf install -y e2fsprogs

# Run the rootfs creation script (must be root for mounting)
sudo ./scripts/create-rootfs.sh
```

The script will:
- Detect your architecture (aarch64)
- Download Alpine Linux 3.19 for aarch64
- Install Python 3 and dependencies
- Install the guest agent
- Create a 2GB ext4 rootfs image

### Set Up Python Environment

```bash
# Install uv (modern Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env

# Create virtual environment and install dependencies
cd ~/locale2b
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## Part 5: Smoke Test

### Start the Service

```bash
cd ~/locale2b
source .venv/bin/activate

# Start the workspace service
uv run python -m workspace_service.main
```

The service should start on `http://0.0.0.0:8080`

### Run Test Script (in a new terminal)

```bash
cd ~/locale2b

# Make test script executable
chmod +x scripts/test-sandbox.sh

# Run the test
./scripts/test-sandbox.sh
```

### Manual Smoke Test

```bash
# 1. Create a sandbox
curl -X POST http://localhost:8080/sandboxes \
  -H "Content-Type: application/json" \
  -d '{"memory_mb": 512, "vcpu_count": 1}'

# Note the sandbox_id from the response (e.g., "abc123")
SANDBOX_ID="abc123"  # Replace with actual ID

# 2. Execute a command in the VM
curl -X POST http://localhost:8080/sandboxes/${SANDBOX_ID}/exec \
  -H "Content-Type: application/json" \
  -d '{"command": "uname -a"}'

# Expected output should include:
# - Linux
# - aarch64 (confirming ARM64 architecture)
# - The kernel version

# 3. Test file operations
curl -X POST http://localhost:8080/sandboxes/${SANDBOX_ID}/files/write \
  -H "Content-Type: application/json" \
  -d '{"path": "/workspace/test.txt", "content": "Hello from ARM64!"}'

curl -X GET "http://localhost:8080/sandboxes/${SANDBOX_ID}/files/read?path=/workspace/test.txt"

# 4. Clean up
curl -X DELETE http://localhost:8080/sandboxes/${SANDBOX_ID}
```

### Success Criteria

✅ **You've succeeded if:**
- Service starts without errors
- Sandbox creation succeeds
- `uname -a` returns output containing "aarch64" and "Linux"
- File write/read operations work
- Sandbox cleanup works

---

## Troubleshooting

### Issue: `/dev/kvm` Permission Denied

**Symptoms:**
```
Error: Failed to open /dev/kvm: Permission denied
```

**Solution:**
```bash
# Check current permissions
ls -la /dev/kvm

# Add yourself to kvm group
sudo usermod -a -G kvm $USER

# Or use ACL
sudo setfacl -m u:${USER}:rw /dev/kvm

# Log out and log back in
```

### Issue: `vhost_vsock` Module Not Found

**Symptoms:**
```
Error: vhost_vsock kernel module not loaded
```

**Solution:**
```bash
# Load the module manually
sudo modprobe vhost_vsock

# Check if it's available in your kernel
modinfo vhost_vsock

# If module doesn't exist, you may need a newer kernel or need to rebuild
# with CONFIG_VHOST_VSOCK=m enabled
```

### Issue: Firecracker Binary Not Found for aarch64

**Symptoms:**
```
Error: No release found for aarch64
```

**Solution:**
```bash
# Manually check available releases
curl -s https://api.github.com/repos/firecracker-microvm/firecracker/releases/latest | \
    grep "browser_download_url.*aarch64.tgz"

# If no pre-built binary, you'll need to build from source
# See Appendix B for build instructions
```

### Issue: VM Fails to Boot (No Guest Agent Response)

**Symptoms:**
```
Error: Timeout waiting for guest agent
```

**Debugging Steps:**
```bash
# 1. Check if Firecracker process is running
ps aux | grep firecracker

# 2. Check Firecracker logs (if running with systemd)
journalctl -u firecracker-workspace -f

# 3. Manually boot a VM to see serial output
sudo firecracker --api-sock /tmp/test.sock &

# In another terminal, configure and boot the VM
# (see Firecracker documentation for manual boot process)

# 4. Check kernel and rootfs paths
ls -lh /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin
ls -lh /var/lib/firecracker-workspaces/rootfs/default-rootfs.ext4
```

### Issue: vsock Connection Timeout

**Symptoms:**
```
Error: Failed to connect to guest agent via vsock
```

**Solution:**
```bash
# 1. Verify vhost_vsock is loaded
lsmod | grep vhost_vsock

# 2. Check if vsock socket exists
ls -la /var/lib/firecracker-workspaces/sandboxes/*/vsock.sock

# 3. Verify guest agent is running inside VM
# (you'll need to boot manually and check with serial console)

# 4. Check firewall isn't blocking (unlikely for vsock)
sudo firewall-cmd --list-all
```

### Issue: Kernel Panic on Boot

**Symptoms:**
VM boots but immediately crashes with kernel panic

**Possible Causes:**
1. **Wrong architecture kernel:** Using x86_64 kernel instead of aarch64
2. **Incompatible kernel config:** Missing required Firecracker features
3. **Corrupted kernel image:** Re-download or rebuild

**Solution:**
```bash
# Verify kernel architecture
file /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin
# Should contain: ARM aarch64

# Re-download the kernel
rm /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin
curl -fsSL -o /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin \
    https://s3.amazonaws.com/spec.ccfc.min/img/quickstart_guide/aarch64/kernels/vmlinux.bin
```

### Issue: Alpine Linux Package Download Fails

**Symptoms:**
```
Error: Failed to download Alpine minirootfs
```

**Solution:**
```bash
# Check if URL is accessible
curl -I https://dl-cdn.alpinelinux.org/alpine/v3.19/releases/aarch64/alpine-minirootfs-3.19.0-aarch64.tar.gz

# Try alternative mirror
# Edit scripts/create-rootfs.sh and change the mirror URL
# Alpine mirror list: https://mirrors.alpinelinux.org/
```

---

## What's Next: Networking (Phase 8.1)

The current setup provides isolated VMs **without network access**. For real development work (pip install, git clone, etc.), you'll need networking.

### Quick Network Setup (for testing)

```bash
# 1. Create a TAP device
sudo ip tuntap add tap0 mode tap
sudo ip addr add 172.16.0.1/24 dev tap0
sudo ip link set tap0 up

# 2. Enable IP forwarding
sudo sysctl -w net.ipv4.ip_forward=1

# 3. Set up NAT (replace eth0 with your interface name)
INTERNET_IF=$(ip route | grep default | awk '{print $5}')
sudo firewall-cmd --permanent --zone=public --add-masquerade
sudo firewall-cmd --permanent --direct --add-rule ipv4 filter FORWARD 0 -i tap0 -o ${INTERNET_IF} -j ACCEPT
sudo firewall-cmd --permanent --direct --add-rule ipv4 filter FORWARD 0 -i ${INTERNET_IF} -o tap0 -m state --state RELATED,ESTABLISHED -j ACCEPT
sudo firewall-cmd --reload
```

**Note:** This gives VMs full internet access. For production, implement egress allowlisting as described in DEVELOPMENT_PHASES.md Phase 8.1.

---

## Appendix A: Building Custom ARM64 Kernel

If you need a custom kernel configuration:

```bash
# 1. Install build dependencies
sudo dnf install -y \
    git \
    gcc \
    make \
    flex \
    bison \
    elfutils-libelf-devel \
    openssl-devel \
    bc \
    ncurses-devel \
    perl \
    rpm-build

# 2. Clone Linux kernel
cd ~/
git clone --depth=1 -b linux-6.6.y \
    git://git.kernel.org/pub/scm/linux/kernel/git/stable/linux-stable.git
cd linux-stable

# 3. Download Firecracker's recommended ARM64 kernel config
curl -fsSL -o .config \
    https://raw.githubusercontent.com/firecracker-microvm/firecracker/main/resources/guest_configs/microvm-kernel-ci-aarch64-6.1.config

# Or use a more recent config
curl -fsSL -o .config \
    https://raw.githubusercontent.com/firecracker-microvm/firecracker/main/resources/guest_configs/microvm-kernel-aarch64-6.1.config

# 4. Update config for current kernel version
make olddefconfig

# 5. Ensure required modules are enabled
scripts/config --enable CONFIG_VSOCKETS
scripts/config --enable CONFIG_VSOCKETS_DIAG
scripts/config --enable CONFIG_VIRTIO_VSOCKETS
scripts/config --enable CONFIG_VIRTIO_VSOCKETS_COMMON

# 6. Build the kernel (this takes 30-60 minutes on Mac Mini M1)
make vmlinux -j$(nproc)

# 7. Copy to kernels directory
cp vmlinux /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin

# 8. Verify architecture
file /var/lib/firecracker-workspaces/kernels/default-vmlinux.bin
# Should show: ARM aarch64, version 1 (SYSV), statically linked, stripped
```

---

## Appendix B: Building Firecracker from Source

If you need to build Firecracker from source (e.g., for a custom patch):

```bash
# 1. Install Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# 2. Install build dependencies
sudo dnf install -y \
    git \
    gcc \
    make \
    pkg-config \
    openssl-devel

# 3. Clone Firecracker repository
cd ~/
git clone https://github.com/firecracker-microvm/firecracker.git
cd firecracker

# 4. Checkout latest stable release
git checkout $(git describe --tags $(git rev-list --tags --max-count=1))

# 5. Build for aarch64
cargo build --release --target aarch64-unknown-linux-gnu

# 6. Install binaries
sudo cp target/aarch64-unknown-linux-gnu/release/firecracker /usr/local/bin/
sudo cp target/aarch64-unknown-linux-gnu/release/jailer /usr/local/bin/
sudo chmod +x /usr/local/bin/firecracker /usr/local/bin/jailer

# 7. Verify
firecracker --version
```

---

## Appendix C: ARM64-Specific Considerations

### GICv3 Requirement for Snapshots

Firecracker's snapshot/resume functionality on aarch64 **only works with GICv3** (Generic Interrupt Controller v3).

Apple Silicon Macs use GICv3, so **snapshots will work**. To verify:

```bash
# Check your GIC version (from Linux)
dmesg | grep -i gic

# Expected output:
# GICv3: ... redistributors detected
```

### RTC Limitations

The pl031 RTC device on aarch64 does not support interrupts. This means:
- Programs using RTC alarms (e.g., `hwclock --systohc`) won't work inside the VM
- Use NTP or other time synchronization methods instead

### Performance Expectations

ARM64 Firecracker performance on Apple Silicon should be **excellent**:
- M1/M2/M3 have hardware virtualization (Apple Hypervisor.framework)
- Asahi Linux's KVM uses the virtual GIC for enhanced performance
- Expected VM boot time: <5 seconds (same as x86_64)
- Expected command execution overhead: <100ms

---

## References and Sources

### Firecracker ARM64 Support
- [Firecracker Official Site](https://firecracker-microvm.github.io/)
- [Firecracker GitHub Repository](https://github.com/firecracker-microvm/firecracker)
- [Arch Linux ARM Firecracker Package](https://archlinuxarm.org/packages/aarch64/firecracker)
- [Firecracker aarch64 CI Integration](https://github.com/firecracker-microvm/firecracker/issues/874)

### Asahi Linux KVM Support
- [Asahi Linux Official Site](https://asahilinux.org/)
- [Asahi Linux Progress Report: Linux 6.14](https://asahilinux.org/2025/03/progress-report-6-14/)
- [Developing QEMU on Asahi Linux](https://daynix.github.io/2023/06/03/developing-qemu-on-asahi-linux-linux-port-for-apple-silicon.html)
- [Linux Desktop on Apple Silicon in Practice](https://gist.github.com/akihikodaki/87df4149e7ca87f18dc56807ec5a1bc5)

### Fedora Asahi Remix
- [Fedora Asahi Remix Official Page](https://asahilinux.org/fedora/)
- [Fedora Asahi Remix User Guide](https://docs.fedoraproject.org/en-US/fedora-asahi-remix/)
- [Fedora Asahi Remix 41 Release Announcement](https://9to5linux.com/fedora-asahi-remix-41-released-for-apple-silicon-macs-with-kde-plasma-6-2)

### vhost_vsock on ARM64
- [Kata Containers vhost_vsock Support](https://github.com/kata-containers/runtime/issues/1512)
- [OpenWrt vsock.ko for aarch64](https://forum.openwrt.org/t/vsock-ko-kernel-module-needed-aarch64-x86-64-targets/220316)
- [Ubuntu vhost_vsock Module Issue](https://bugs.launchpad.net/bugs/1974178)
- [Qualcomm Linux Kernel Guide - Virtualization](https://docs.qualcomm.com/bundle/publicresource/topics/80-70020-3/virtualization.html)

---

## Go/No-Go Decision

### ✅ GO - This is Feasible

**Reasons to proceed:**
1. Firecracker officially supports aarch64
2. Asahi Linux has production-ready KVM support
3. vhost_vsock works on ARM64 Linux kernels
4. Fedora Asahi Remix is stable and well-maintained
5. Your existing locale2b codebase already handles aarch64 (scripts auto-detect architecture)

**Estimated effort:**
- Initial setup: 4-6 hours (including Asahi install)
- Kernel/rootfs creation: 1-2 hours
- Smoke test and validation: 1-2 hours
- **Total: 1-2 days** for a working system

**Risks:**
- Low: All core components are proven and in production use elsewhere
- Medium: You're combining them in a new configuration (Asahi + Firecracker)
- Mitigation: Follow this guide step-by-step, verify each stage

### When to Stop (Hard Blockers)

**You should reconsider if:**
1. Your Mac Mini is M4 (current Asahi Linux M4 support is experimental and "painful" per devs)
2. KVM doesn't work after Asahi install (very unlikely - it's been stable since 2021)
3. Firecracker refuses to run even with proper KVM access (has never been reported on Asahi)

**Fallback option:**
If blocked, use a remote Linux server (AWS Graviton, Hetzner ARM64 VPS, or local x86_64 NUC).

---

## Conclusion

You're in an excellent position:
- Apple Silicon has great performance for virtualization
- Asahi Linux is mature and well-supported
- Firecracker works on ARM64
- Your locale2b codebase is architecture-agnostic

**Next steps:**
1. Install Fedora Asahi Remix (1-2 hours)
2. Verify KVM and install Firecracker (30 minutes)
3. Create ARM64 kernel and rootfs (1 hour)
4. Run smoke test (30 minutes)
5. Celebrate your working Firecracker setup! 🎉

If you hit any issues, refer to the Troubleshooting section or open a GitHub issue on the locale2b repository.
