#!/bin/bash
# Assemble a self-booting knix system disk. Executed INSIDE the arm64 Linux
# build guest as root.
#
#   sudo KNIX_TARGET_DEV=/dev/vdd bash -s < platform/image/assemble-native.sh
#
# The target is a blank disk, never a copy of the seed: two disks derived from
# the same seed share an ext4 UUID and the guest will mount the wrong root.
# The disk carries its own EFI system partition, bootloader, kernel, initramfs
# and loader entries. No host kernel, initrd, seed ISO or EFI boot variable is
# needed to start it.
set -euo pipefail

BUILD="${KNIX_BUILD_DIR:-/mnt/knix-build}"
TARGET_DEV="${KNIX_TARGET_DEV:-/dev/vdd}"
ESP_DEV="${TARGET_DEV}1"
ROOT_DEV="${TARGET_DEV}2"
MOUNT="${KNIX_MOUNT:-/mnt/knix-target}"
EFI="$MOUNT/efi"
TARBALL="${KNIX_TARBALL:-$BUILD/knix-rootfs.tar.zst}"
KERNEL="${KNIX_KERNEL_IMAGE:-$BUILD/knix-Image}"
MODULES="${KNIX_MODULES_DIR:-$BUILD/modules}"
SUMMARY="${KNIX_KERNEL_SUMMARY:-$BUILD/kernel-build.json}"
RSUMMARY="${KNIX_ROOTFS_SUMMARY:-$BUILD/rootfs-build.json}"
PUBKEY="${KNIX_SSH_PUBKEY_FILE:-$BUILD/knix.pub}"
BOOT_EFI="/usr/lib/systemd/boot/efi/systemd-bootaa64.efi"

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

echo "=== partition $TARGET_DEV (GPT: ESP + root) ==="
# A previous failed assemble may have left mounts behind; clean them before
# wiping so no bind mount survives into the next run.
for d in sys proc dev; do umount "$MOUNT/$d" 2>/dev/null || true; done
umount "$EFI" 2>/dev/null || true
umount "$MOUNT" 2>/dev/null || true
printf 'label: gpt\nsize=512M,type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B\ntype=0FC63DAF-8483-4772-8E79-3D69D8477DE4\n' \
  | sfdisk --wipe always "$TARGET_DEV" > /dev/null
udevadm settle 2>/dev/null || sleep 1
[ -b "$ESP_DEV" ] || { echo "EFI partition missing: $ESP_DEV" >&2; exit 1; }
[ -b "$ROOT_DEV" ] || { echo "root partition missing: $ROOT_DEV" >&2; exit 1; }

echo "=== filesystems ==="
mkfs.vfat -F32 -n KNIX-EFI "$ESP_DEV"
mkfs.ext4 -q -F -L KNIX-ROOT "$ROOT_DEV"
install -d -m 0755 "$MOUNT"
mount "$ROOT_DEV" "$MOUNT"
install -d -m 0755 "$EFI"
mount "$ESP_DEV" "$EFI"

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

echo "=== guest-owned initramfs ==="
# mkinitramfs needs a live /dev, /proc and /sys. The bind mounts come from the
# helper guest only while the archive is generated; no helper userspace is
# copied into the product.
for d in dev proc sys; do
  install -d -m 0755 "$MOUNT/$d"
  mount --bind "/$d" "$MOUNT/$d"
done
if ! chroot "$MOUNT" /usr/sbin/mkinitramfs \
       -o "/boot/initrd.img-$KVER-knix" "$KVER" > "$BUILD/initramfs.log" 2>&1; then
  cat "$BUILD/initramfs.log" >&2
  echo "could not build the guest-owned initramfs" >&2
  exit 1
fi
for d in sys proc dev; do umount "$MOUNT/$d"; done
[ -s "$MOUNT/boot/initrd.img-$KVER-knix" ] || { echo "empty initramfs" >&2; exit 1; }
ls -lh "$MOUNT/boot/initrd.img-$KVER-knix"

echo "=== EFI system partition ==="
[ -f "$MOUNT$BOOT_EFI" ] || {
  echo "missing $BOOT_EFI in rootfs (boot package set drift)" >&2
  exit 1
}
install -d -m 0755 "$EFI/EFI/BOOT" "$EFI/EFI/knix" "$EFI/loader/entries"
install -m 0644 "$MOUNT$BOOT_EFI" "$EFI/EFI/BOOT/BOOTAA64.EFI"
install -m 0644 "$MOUNT/boot/vmlinuz-$KVER-knix" "$EFI/EFI/knix/vmlinuz-$KVER-knix"
install -m 0644 "$MOUNT/boot/initrd.img-$KVER-knix" "$EFI/EFI/knix/initrd.img-$KVER-knix"
# The removable media path needs no EFI Boot#### variable; EDK2 finds it on a
# fresh variable store. loader.conf and the entry live at the ESP root.
cat > "$EFI/loader/loader.conf" <<'LOADER'
default knix.conf
timeout 3
console-mode keep
LOADER
cat > "$EFI/loader/entries/knix.conf" <<ENTRY
title   Knix 0.1 (bootstrap)
linux   /EFI/knix/vmlinuz-$KVER-knix
initrd  /EFI/knix/initrd.img-$KVER-knix
options root=/dev/vda2 rw console=ttyAMA0
ENTRY
cat "$EFI/loader/entries/knix.conf"

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

# When this disk becomes the primary virtio disk it is /dev/vda: partition 1
# is the ESP and partition 2 is the root. All identity, users, network and
# authentication stay here; nothing is projected from the host.
printf '/dev/vda1 /efi vfat umask=0077,defaults 0 1\n/dev/vda2 / ext4 rw,relatime 0 1\n' > "$MOUNT/etc/fstab"
printf '127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n127.0.1.1 knix\n' > "$MOUNT/etc/hosts"
printf 'nameserver 10.0.2.3\n' > "$MOUNT/etc/resolv.conf"
install -d -m 0755 "$MOUNT/etc/systemd/network" \
                   "$MOUNT/etc/modules-load.d" \
                   "$MOUNT/etc/systemd/system" \
                   "$MOUNT/etc/systemd/system/multi-user.target.wants" \
                   "$MOUNT/etc/udev/rules.d"
# The DRM and input devices are standard virtio, but explicitly loading their
# modules keeps /dev/dri and /dev/input deterministic on a cold boot. The
# compositor and the seat stay inside the guest.
printf 'virtio-gpu\nvirtio_input\n' > "$MOUNT/etc/modules-load.d/knix-graphics.conf"
# Generic seat rules leave PCI virtio keyboards and pointers without a seat
# tag, so libinput's udev enumeration cannot see them. knix owns this rule.
cat > "$MOUNT/etc/udev/rules.d/71-knix-virtio-input.rules" <<'UDEV'
# knix owns the semantics of its virtio input devices.
SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_PATH}=="pci-*", \
  ENV{ID_SEAT}="seat0", TAG+="seat", TAG+="uaccess"
UDEV
# The session is a guest-owned systemd unit, not a root shell. seatd provides
# the seat; both wlroots backends are mandatory -- WLR_BACKENDS=drm alone
# excludes libinput and leaves swaymsg -t get_inputs empty.
cat > "$MOUNT/etc/systemd/system/knix-session.service" <<'UNIT'
[Unit]
Description=Knix Wayland session
After=systemd-logind.service seatd.service systemd-user-sessions.service
Wants=seatd.service
ConditionPathExists=/dev/dri/card0
ConditionPathExists=/dev/dri/renderD128

[Service]
Type=simple
User=knix
Group=knix
ExecStartPre=+/usr/bin/install -d -m 0700 -o knix -g knix /run/user/1000
ExecStartPre=+/usr/bin/sh -c ': > /run/knix-session.log'
Environment=XDG_RUNTIME_DIR=/run/user/1000
Environment=XDG_SESSION_TYPE=wayland
Environment=XDG_CURRENT_DESKTOP=Knix
Environment=XDG_SESSION_DESKTOP=Knix
Environment=QT_QPA_PLATFORM=wayland
Environment=MOZ_ENABLE_WAYLAND=1
Environment=WLR_BACKENDS=drm,libinput
Environment=LIBSEAT_BACKEND=seatd
ExecStart=/usr/bin/sway -d
StandardOutput=append:/run/knix-session.log
StandardError=append:/run/knix-session.log
Restart=on-failure
RestartSec=1

[Install]
WantedBy=multi-user.target
UNIT
# Disabled by default: the verification recipe starts it explicitly, and the
# ConditionPathExists keeps GPU=none boots on the serial/ssh path.
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
  useradd -m -s /bin/bash -G sudo,video,input,render knix
fi
# seatd's socket is root:video 0770; the input devices are root:input. These
# groups let the non-root compositor take the fds from the guest's seat manager.
usermod -aG sudo,video,input,render knix
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
test -f "$MOUNT/etc/udev/rules.d/71-knix-virtio-input.rules" \
  || { echo "missing guest-owned virtio input rule" >&2; exit 1; }
test -f "$MOUNT/etc/systemd/system/knix-session.service" \
  || { echo "missing guest-owned session unit" >&2; exit 1; }
! test -e "$MOUNT/etc/systemd/system/multi-user.target.wants/knix-session.service" \
  || { echo "session unit must stay disabled until verification starts it" >&2; exit 1; }
echo "seat: seatd + drm,libinput + guest udev rule (unit disabled)"
echo -n "brand assets: "; ls -1 "$MOUNT/usr/share/knix/brand" | wc -l
ls -lh "$MOUNT/boot/vmlinuz-$KVER-knix"
echo -n "kernel modules on disk: "; find "$MOUNT/lib/modules/$KVER" -type f -name '*.ko*' | wc -l
[ -f "$MOUNT/lib/modules/$KVER/modules.dep" ] || { echo "modules.dep missing" >&2; exit 1; }
for f in "$EFI/EFI/BOOT/BOOTAA64.EFI" \
         "$EFI/EFI/knix/vmlinuz-$KVER-knix" \
         "$EFI/EFI/knix/initrd.img-$KVER-knix" \
         "$EFI/loader/loader.conf" \
         "$EFI/loader/entries/knix.conf"; do
  [ -s "$f" ] || { echo "missing EFI payload: $f" >&2; exit 1; }
done
echo "EFI payload OK: BOOTAA64.EFI + vmlinuz + initrd + loader entries"

sync
umount "$EFI"
umount "$MOUNT"
echo "ASSEMBLE OK: $TARGET_DEV is ready for UEFI self-boot"
