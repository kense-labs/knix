#!/bin/bash
# Assemble a bootable knix system disk from the native rootfs and the kernel
# built inside an arm64 Linux guest. Executed INSIDE that guest as root.
#
#   sudo KNIX_TARGET_DEV=/dev/vdd bash -s < platform/image/assemble-native.sh
#
# The target is a blank disk, never a copy of the seed: two disks derived from
# the same seed share an ext4 UUID and the guest will mount the wrong root.
set -euo pipefail

BUILD="${KNIX_BUILD_DIR:-/mnt/knix-build}"
TARGET_DEV="${KNIX_TARGET_DEV:-/dev/vdd}"
PART="${TARGET_DEV}1"
MOUNT="${KNIX_MOUNT:-/mnt/knix-target}"
TARBALL="${KNIX_TARBALL:-$BUILD/knix-rootfs.tar.zst}"
KERNEL="${KNIX_KERNEL_IMAGE:-$BUILD/knix-Image}"
MODULES="${KNIX_MODULES_DIR:-$BUILD/modules}"
SUMMARY="${KNIX_KERNEL_SUMMARY:-$BUILD/kernel-build.json}"
RSUMMARY="${KNIX_ROOTFS_SUMMARY:-$BUILD/rootfs-build.json}"
PUBKEY="${KNIX_SSH_PUBKEY_FILE:-$BUILD/knix.pub}"

# The kernel build records its own release; the module directory must match it.
KVER="${KNIX_KERNEL_VERSION:-}"
if [ -z "$KVER" ] && [ -f "$SUMMARY" ]; then
  KVER="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["kernel_version"])' "$SUMMARY")"
fi
KVER="${KVER:-7.2.15}"
MODROOT="$MODULES/lib/modules/$KVER"

for f in "$TARBALL" "$KERNEL" "$PUBKEY"; do
  [ -f "$f" ] || { echo "missing input: $f" >&2; exit 1; }
done
[ -d "$MODROOT" ] || { echo "missing kernel modules: $MODROOT (run build-kernel.sh --apply)" >&2; exit 1; }
NKO="$(find "$MODROOT" -type f \( -name '*.ko' -o -name '*.ko.xz' -o -name '*.ko.zst' -o -name '*.ko.gz' \) | wc -l)"
[ "$NKO" -gt 0 ] || { echo "no kernel modules under $MODROOT" >&2; exit 1; }
[ -f "$SUMMARY" ] || { echo "missing kernel build summary: $SUMMARY" >&2; exit 1; }
[ -f "$RSUMMARY" ] || { echo "missing rootfs build summary: $RSUMMARY (rebuild with build-rootfs.sh --apply)" >&2; exit 1; }
[ -b "$TARGET_DEV" ] || { echo "not a block device: $TARGET_DEV" >&2; exit 1; }

# The build guest produces self-describing summaries. An image assembled from
# drifted inputs would be unreproducible, so refuse before touching the disk.
python3 - "$SUMMARY" "$RSUMMARY" "$KERNEL" "$TARBALL" "$NKO" <<'PY'
import hashlib, json, pathlib, sys
ksum, rsum, image, tarball, nko = sys.argv[1:]
k = json.loads(pathlib.Path(ksum).read_text())
r = json.loads(pathlib.Path(rsum).read_text())
def digest(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
for name, got, expected in (
    ("kernel image", digest(image), k["image_sha256"]),
    ("rootfs tarball", digest(tarball), r["tarball_sha256"]),
    ("module count", int(nko), k["module_count"]),
):
    if got != expected:
        print("INPUT DRIFT %s: %s != %s" % (name, got, expected), file=sys.stderr)
        sys.exit(1)
    print("input OK: %s = %s" % (name, got))
PY

echo "=== partition $TARGET_DEV ==="
umount "$PART" 2>/dev/null || true
printf 'label: gpt\n, , L\n' | sfdisk --wipe always "$TARGET_DEV" > /dev/null
udevadm settle 2>/dev/null || sleep 1
[ -b "$PART" ] || { echo "partition missing: $PART" >&2; exit 1; }

echo "=== filesystem ==="
mkfs.ext4 -q -F -L KNIX-ROOT "$PART"
install -d -m 0755 "$MOUNT"
mount "$PART" "$MOUNT"

echo "=== unpack native rootfs ==="
tar --zstd -xf "$TARBALL" -C "$MOUNT"

echo "=== kernel and modules ==="
install -d -m 0755 "$MOUNT/boot" "$MOUNT/lib/modules"
install -m 0644 "$KERNEL" "$MOUNT/boot/vmlinuz-$KVER-knix"
cp -a "$MODROOT" "$MOUNT/lib/modules/"
# Generate the index with the target's own kmod. The helper guest is only a
# compiler; its userspace is not a product input.
if ! chroot "$MOUNT" depmod -a "$KVER" 2>/dev/null; then
  depmod -b "$MOUNT" "$KVER" || { echo "could not generate modules.dep" >&2; exit 1; }
fi
echo "modules: $NKO .ko installed for $KVER"

echo "=== guest-owned configuration ==="
# Debian ships both a distribution-signed and an upstream-signed regulatory
# database. This kernel embeds the in-tree sforshee key, so only the upstream
# pair passes cfg80211 verification; the default alternative points at the
# Debian-signed one and produces a signature warning on every boot.
for alt in regulatory.db regulatory.db.p7s; do
  if ! chroot "$MOUNT" update-alternatives --set "$alt" "/lib/firmware/$alt-upstream" >/dev/null 2>&1; then
    ln -sfn "/lib/firmware/$alt-upstream" "$MOUNT/etc/alternatives/$alt"
  fi
done
echo "regulatory.db: $(readlink "$MOUNT/etc/alternatives/regulatory.db")"

# When this disk becomes the primary virtio disk it is /dev/vda, so the root
# partition is /dev/vda1. All identity, users, network and authentication stay
# here; nothing is projected from the host.
printf '/dev/vda1 / ext4 rw,relatime 0 1\n' > "$MOUNT/etc/fstab"
printf '127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n127.0.1.1 knix\n' > "$MOUNT/etc/hosts"
printf 'nameserver 10.0.2.3\n' > "$MOUNT/etc/resolv.conf"
install -d -m 0755 "$MOUNT/etc/systemd/network" \
                   "$MOUNT/etc/systemd/system/multi-user.target.wants"
cat > "$MOUNT/etc/systemd/network/20-virtio.network" <<'NET'
[Match]
Name=en*
[Network]
DHCP=yes
NET
for svc in systemd-networkd.service ssh.service; do
  ln -sf "/lib/systemd/system/$svc" \
    "$MOUNT/etc/systemd/system/multi-user.target.wants/$svc"
done
: > "$MOUNT/etc/machine-id"

# ssh-keygen needs /dev/urandom, which a bare rootfs does not provide. Reuse the
# build guest's host keys so sshd can start on first boot.
cp /etc/ssh/ssh_host_* "$MOUNT/etc/ssh/"
chmod 0600 "$MOUNT"/etc/ssh/ssh_host_*_key
chmod 0644 "$MOUNT"/etc/ssh/ssh_host_*_key.pub

chroot "$MOUNT" /bin/bash -s <<'CHROOT'
set -e
if ! id -u knix >/dev/null 2>&1; then
  useradd -m -s /bin/bash -G sudo knix
fi
install -d -m 0700 /home/knix/.ssh
chown -R knix:knix /home/knix/.ssh
CHROOT

install -m 0600 "$PUBKEY" "$MOUNT/home/knix/.ssh/authorized_keys"
chroot "$MOUNT" chown -R knix:knix /home/knix/.ssh
printf 'knix ALL=(ALL) NOPASSWD:ALL\n' > "$MOUNT/etc/sudoers.d/knix"
chmod 0440 "$MOUNT/etc/sudoers.d/knix"

echo "=== verify assembled disk ==="
grep -E '^(NAME|ID|VERSION)=' "$MOUNT/etc/os-release"
test -x "$MOUNT/usr/bin/sway" && echo "sway: yes"
test -f "$MOUNT/usr/share/wayland-sessions/knix.desktop" && echo "wayland session: yes"
echo -n "brand assets: "; ls -1 "$MOUNT/usr/share/knix/brand" | wc -l
ls -lh "$MOUNT/boot/vmlinuz-$KVER-knix"
echo -n "kernel modules on disk: "; find "$MOUNT/lib/modules/$KVER" -type f -name '*.ko*' | wc -l
[ -f "$MOUNT/lib/modules/$KVER/modules.dep" ] || { echo "modules.dep missing" >&2; exit 1; }

sync
umount "$MOUNT"
echo "ASSEMBLE OK: $TARGET_DEV is ready for direct kernel boot"
