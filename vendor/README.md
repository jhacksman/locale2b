# Vendored Dependencies

This directory contains ALL external dependencies needed to run locale2b.

**Security Model: Repo-First**
- No external downloads during setup
- All binaries committed to git
- Everything comes from YOUR repository
- No trust in random CDNs or package servers

## Contents

### firecracker/
- `firecracker` - Firecracker VMM binary
- `jailer` - Firecracker jailer for process isolation
- `VERSION` - Version info

**Source:** https://github.com/firecracker-microvm/firecracker/releases

### kernels/
- `vmlinux.bin` - Linux kernel for guest VMs
- `VERSION` - Kernel version

**Source:** AWS S3 (Firecracker official kernels)

### rootfs/
- `alpine-minirootfs.tar.gz` - Alpine Linux minimal root filesystem
- `VERSION` - Alpine version

**Source:** https://alpinelinux.org/downloads/

## Why Vendor?

**Old (insecure) approach:**
```bash
curl https://random-url.com/binary | sudo bash
```

**New (secure) approach:**
```bash
git clone YOUR-REPO
./scripts/setup-secure.sh  # Uses only vendored files
```

## Updating Vendored Files

To update to newer versions:

```bash
# Run the vendor script (maintainer only)
./scripts/vendor-artifacts.sh

# Review what changed
git diff vendor/

# Commit if acceptable
git add vendor/
git commit -m "Update vendored dependencies"
git push
```

## File Sizes

These are large binary files tracked with Git LFS:
- Firecracker binary: ~2.6MB
- Jailer binary: ~2.2MB
- Kernel: ~21MB
- Alpine rootfs: ~3.2MB

Total: ~29MB

This is acceptable for the security benefit of having everything in one place.
