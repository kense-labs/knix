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
# excludes libinput and leaves swaymsg -t get_inputs empty. The unit is enabled
# by the guest's own multi-user.target: a booted knix image has a desktop
# without any host-side launcher or verification script.
cat > "$MOUNT/etc/systemd/system/knix-session.service" <<'UNIT'
[Unit]
Description=Knix Wayland session
After=systemd-logind.service seatd.service systemd-user-sessions.service systemd-modules-load.service systemd-udev-settle.service
Wants=seatd.service systemd-udev-settle.service
ConditionPathExists=/dev/dri/card0
ConditionPathExists=/dev/dri/renderD128
# A crashed compositor is recovered by the guest's own systemd, not by a host
# launcher. The start limit stops a restart loop from burning the CPU forever.
StartLimitIntervalSec=30
StartLimitBurst=5
# A failed compositor is diagnosed by the guest's own systemd. OnFailure= fires
# once per failed state -- including the brief failure before each automatic
# restart -- so the history is a crash timeline and the report is the latest
# snapshot. No host input, network or private framework is involved.
OnFailure=knix-session-failure.service

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
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT

# Crash diagnostics are a guest capability, not a host-side report. systemd
# invokes this oneshot on every failed state of the session unit. The script
# reads only guest unit state, the guest journal and local device nodes; it
# always succeeds so it never becomes a second failed unit. /run is a tmpfs,
# so the guest also appends every report to an archive on the persistent root
# filesystem and pins journald to persistent storage: a reboot must not erase
# the crash timeline. Nothing here is written or collected by the host.
mkdir -p "$MOUNT/usr/lib/knix" "$MOUNT/etc/systemd/journald.conf.d" \
         "$MOUNT/var/lib/knix" "$MOUNT/var/log/journal"
chmod 0750 "$MOUNT/var/lib/knix"
chmod 2755 "$MOUNT/var/log/journal"
cat > "$MOUNT/etc/systemd/journald.conf.d/knix-persistence.conf" <<'JOURNAL'
[Journal]
Storage=persistent
SystemMaxUse=128M
JOURNAL
cat > "$MOUNT/usr/lib/knix/session-failure-report.sh" <<'REPORT'
#!/bin/bash
# Guest-owned crash diagnostics, invoked by knix-session.service OnFailure=.
# Besides the report and the persistent boot-stamped archive, this script owns
# three operational duties: bounded archive rotation, cross-boot statistics and
# a local err-priority journal alert with a login-visible notice. Nothing is
# written, rotated or forwarded by the host. A verification drill may redirect
# all outputs into an isolated /run/knix subdirectory; /var/lib/knix (the
# shipped disk) is never touched by a drill.
set -u
UNIT=knix-session.service
DIR=/run/knix
PDIR=/var/lib/knix
LIMIT=8192
GENS=3
TAG=knix-diagnostics
DRILL="${KNIX_DIAG_DRILL_DIR:-}"
if [ -n "$DRILL" ]; then
  case "$DRILL" in
    /run/knix/*) ;;
    *) exit 0 ;;
  esac
  DIR="$DRILL/run"
  PDIR="$DRILL/var"
fi
mkdir -p "$DIR" "$PDIR" || exit 0
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"
BOOT_ID="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)"
if [ -n "$DRILL" ]; then
  # A drill exercises bookkeeping only: it must not query systemd 200 times and
  # must never raise a real alert or touch the production /run snapshot.
  ACTIVE_STATE=active
  RESULT=success
  EXEC_STATUS=0
  RESTARTS=0
  FAILED_UNITS=0
  SWAY_PROCS=0
  SSH_STATE=active
  SERIAL_STATE=present
  LIMIT_LINES=0
else
  ACTIVE_STATE="$(systemctl show -p ActiveState --value "$UNIT" 2>/dev/null || echo unknown)"
  RESULT="$(systemctl show -p Result --value "$UNIT" 2>/dev/null || echo unknown)"
  EXEC_STATUS="$(systemctl show -p ExecMainStatus --value "$UNIT" 2>/dev/null || echo unknown)"
  RESTARTS="$(systemctl show -p NRestarts --value "$UNIT" 2>/dev/null || echo unknown)"
  FAILED_UNITS="$(systemctl --failed --no-legend 2>/dev/null | wc -l)"
  SWAY_PROCS="$(pgrep -c sway 2>/dev/null || true)"
  SSH_STATE="$(systemctl is-active ssh.service 2>/dev/null || echo unknown)"
  SERIAL_STATE=missing
  [ -c /dev/ttyAMA0 ] && SERIAL_STATE=present
  LIMIT_LINES="$(journalctl -u "$UNIT" --no-pager -n 40 2>/dev/null | grep -c 'Start request repeated too quickly.' || true)"
fi
{
  echo "unit=$UNIT"
  echo "owner=guest"
  echo "manager=systemd"
  echo "timestamp=$TIMESTAMP"
  echo "boot=$BOOT_ID"
  echo "active_state=$ACTIVE_STATE"
  echo "result=$RESULT"
  echo "exec_main_status=$EXEC_STATUS"
  echo "restarts=$RESTARTS"
  echo "failed_units=$FAILED_UNITS"
  echo "sway_processes=${SWAY_PROCS:-0}"
  echo "ssh=$SSH_STATE"
  echo "serial=$SERIAL_STATE"
  echo "start_limit_lines=${LIMIT_LINES:-0}"
} > "$DIR/session-failure.txt" || exit 0
# The runtime snapshot is a tmpfs and disappears on reboot. Mirror it and append
# one boot-stamped line per event to the guest-owned archive on the root disk.
cp "$DIR/session-failure.txt" "$PDIR/session-failure.txt" || true
ARCHIVE="$PDIR/session-failure.log"
echo "boot=$BOOT_ID timestamp=$TIMESTAMP unit=$UNIT owner=guest manager=systemd restarts=$RESTARTS result=$RESULT active_state=$ACTIVE_STATE exec_main_status=$EXEC_STATUS failed_units=$FAILED_UNITS sway_processes=${SWAY_PROCS:-0} ssh=$SSH_STATE serial=$SERIAL_STATE start_limit_lines=${LIMIT_LINES:-0}" \
  >> "$ARCHIVE" || true
echo "$TIMESTAMP unit=$UNIT restarts=$RESTARTS result=$RESULT active_state=$ACTIVE_STATE failed_units=$FAILED_UNITS" \
  >> "$DIR/session-failure.history" || true

# Bounded rotation: the guest rotates its OWN archive family, oldest first.
# The active archive always stays below LIMIT bytes; generations are .1..N.
SIZE="$(wc -c < "$ARCHIVE" 2>/dev/null || true)"
SIZE="${SIZE//[^0-9]/}"
[ -n "$SIZE" ] || SIZE=0
if [ "$SIZE" -gt "$LIMIT" ]; then
  i=$GENS
  while [ "$i" -gt 1 ]; do
    OLD="$ARCHIVE.$((i - 1))"
    [ -f "$OLD" ] && mv -f "$OLD" "$ARCHIVE.$i"
    i=$((i - 1))
  done
  mv -f "$ARCHIVE" "$ARCHIVE.1" || true
  : > "$ARCHIVE" || true
  ROT="$(cat "$PDIR/session-failure.rotations" 2>/dev/null || echo 0)"
  echo "$((ROT + 1))" > "$PDIR/session-failure.rotations" || true
fi

# Cross-boot statistics are recomputed by the guest from the whole archive
# family, so a rotation can never erase the cumulative count or the boot set.
TOTAL=0
BOOT_SET=""
for f in "$ARCHIVE" "$ARCHIVE".1 "$ARCHIVE".2 "$ARCHIVE".3; do
  [ -f "$f" ] || continue
  N="$(grep -c 'active_state=' "$f" 2>/dev/null || true)"
  TOTAL=$((TOTAL + ${N:-0}))
  BOOT_SET="$BOOT_SET$(cat "$f" 2>/dev/null)\n"
done
BOOTS="$(printf '%b' "$BOOT_SET" | sed -n 's/^boot=\([^ ]*\).*/\1/p' | sort -u | grep -c . || true)"
ROT="$(cat "$PDIR/session-failure.rotations" 2>/dev/null || echo 0)"
{
  echo "owner=guest"
  echo "manager=systemd"
  echo "total_events=$TOTAL"
  echo "boots_seen=${BOOTS:-0}"
  echo "rotations=${ROT:-0}"
  echo "last_boot=$BOOT_ID"
  echo "last_event=$TIMESTAMP"
  echo "terminal_active_state=$ACTIVE_STATE"
  echo "archive_limit_bytes=$LIMIT"
  echo "generations=$GENS"
} > "$PDIR/session-failure.stats" || true

# Cross-boot trends: the SAME guest-written archive family also yields a
# bounded per-boot profile. This is a local report on the guest root disk;
# there is no host collector, network channel or external analytics service.
TREND="$PDIR/session-failure.trends"
TREND_BOOTS=8
TREND_LIMIT=4096
TREND_ROWS="$PDIR/.trends.rows"
TREND_KEEP="$PDIR/.trends.keep"
printf '%b' "$BOOT_SET" | grep '^boot=' | awk -v max="$TREND_BOOTS" '
{
  boot=""; ts=""; state=""; limit=""; restarts=""
  for (i = 1; i <= NF; i++) {
    split($i, kv, "=")
    if (kv[1] == "boot") boot = kv[2]
    else if (kv[1] == "timestamp") ts = kv[2]
    else if (kv[1] == "active_state") state = kv[2]
    else if (kv[1] == "start_limit_lines") limit = kv[2]
    else if (kv[1] == "restarts") restarts = kv[2]
  }
  if (boot == "") next
  n[boot]++
  if (state == "failed") failed[boot]++
  if ((limit + 0) > 0) limited[boot]++
  if ((restarts + 0) > peak[boot]) peak[boot] = restarts + 0
  if (first[boot] == "" || ts < first[boot]) first[boot] = ts
  if (last[boot] == "" || ts > last[boot]) last[boot] = ts
  if (!(boot in seen)) { seen[boot] = 1; order[++k] = boot }
}
END {
  for (j = k; j >= 1 && j > k - max; j--) {
    b = order[j]
    printf "boot=%s events=%d failed_states=%d limit_events=%d restarts_peak=%d first_seen=%s last_seen=%s\n", \
      b, n[b], failed[b], limited[b], peak[b], first[b], last[b]
  }
}' > "$TREND_ROWS" || true

write_trends() {
  rows="$1"
  tsum=0; tmax=0; tlim=0; tkept=0
  while IFS= read -r row; do
    case "$row" in boot=*) ;; *) continue ;; esac
    # Anchor on the field boundary: a greedy ".*events=" would match the
    # later "limit_events=" field and silently zero every row's event count.
    ev="$(printf '%s\n' "$row" | sed -n 's/.* events=\([0-9][0-9]*\) .*/\1/p')"
    le="$(printf '%s\n' "$row" | sed -n 's/.* limit_events=\([0-9][0-9]*\) .*/\1/p')"
    tsum=$((tsum + ${ev:-0}))
    [ "${ev:-0}" -gt "$tmax" ] && tmax=${ev:-0}
    [ "${le:-0}" -gt 0 ] && tlim=$((tlim + 1))
    tkept=$((tkept + 1))
  done < "$rows"
  {
    echo "owner=guest"
    echo "manager=systemd"
    echo "source=session-failure.log family"
    echo "boots_seen=${BOOTS:-0}"
    echo "total_events=$TOTAL"
    echo "trend_boots_kept=$tkept"
    echo "trend_events_sum=$tsum"
    echo "limit_boots=$tlim"
    echo "max_events_per_boot=$tmax"
    echo "trend_boot_limit=$TREND_BOOTS"
    echo "trend_byte_limit=$TREND_LIMIT"
    echo "last_boot=$BOOT_ID"
    echo "--- per-boot (newest first, guest-owned) ---"
    cat "$rows"
  } > "$TREND"
}
ROWS_N="$(grep -c '^boot=' "$TREND_ROWS" 2>/dev/null || true)"
KEEP=${ROWS_N:-0}
[ "$KEEP" -gt "$TREND_BOOTS" ] && KEEP=$TREND_BOOTS
if [ "$KEEP" -lt 1 ]; then
  : > "$TREND_KEEP"
else
  head -n "$KEEP" "$TREND_ROWS" > "$TREND_KEEP" || true
fi
write_trends "$TREND_KEEP"
TSIZE="$(wc -c < "$TREND" 2>/dev/null || echo 0)"
TSIZE="${TSIZE//[^0-9]/}"
[ -n "$TSIZE" ] || TSIZE=0
while [ "$TSIZE" -gt "$TREND_LIMIT" ] && [ "$KEEP" -gt 1 ]; do
  KEEP=$((KEEP - 1))
  head -n "$KEEP" "$TREND_ROWS" > "$TREND_KEEP" || true
  write_trends "$TREND_KEEP"
  TSIZE="$(wc -c < "$TREND" 2>/dev/null || echo 0)"
  TSIZE="${TSIZE//[^0-9]/}"
  [ -n "$TSIZE" ] || TSIZE=0
done
rm -f "$TREND_ROWS" "$TREND_KEEP" || true

# Local alert: the guest raises an err-priority journal message, keeps a
# persistent notice on its root disk and exposes it to interactive logins.
# There is no network, no host channel and no external service involved.
if [ -z "$DRILL" ]; then
  ALERT="alert=$TIMESTAMP unit=$UNIT boot=$BOOT_ID active_state=$ACTIVE_STATE result=$RESULT failed_units=$FAILED_UNITS report=$PDIR/session-failure.txt"
  echo "$ALERT" > "$DIR/session-failure.alert" || true
  echo "$ALERT" > "$PDIR/session-failure.alert" || true
  echo "knix session failure: $ALERT" | systemd-cat -p err -t "$TAG" 2>/dev/null || true
fi
exit 0
REPORT
chmod 0755 "$MOUNT/usr/lib/knix/session-failure-report.sh"
cat > "$MOUNT/etc/profile.d/10-knix-session-alert.sh" <<'NOTICE'
# Guest-owned, login-visible crash notice. It is scoped to the current boot so
# a healthy reboot does not keep repeating an old failure; the durable record
# stays in /var/lib/knix and in the persistent journal.
alert=/var/lib/knix/session-failure.alert
boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)
if [ -r "$alert" ] && [ -n "$boot" ] && grep -q "boot=$boot" "$alert"; then
  printf 'Knix: session failure reported during this boot -- %s\n' "$(cat "$alert")"
fi
NOTICE
chmod 0644 "$MOUNT/etc/profile.d/10-knix-session-alert.sh"
cat > "$MOUNT/etc/systemd/system/knix-session-failure.service" <<'FAILURE'
[Unit]
Description=Knix session failure diagnostics
[Service]
Type=oneshot
ExecStart=/bin/bash /usr/lib/knix/session-failure-report.sh
FAILURE

# Auto-start is a guest-owned enablement symlink, never a host service. The
# ConditionPathExists pair keeps GPU=none boots on the serial/ssh fallback.
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
# session.json declares autostart for multi-user.target. The symlink is relative
# so it resolves inside the guest root after the disk becomes /dev/vda.
ln -sf ../knix-session.service \
  "$MOUNT/etc/systemd/system/multi-user.target.wants/knix-session.service"
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

# P16: the guest owns every account, credential and login decision. sshd must
# refuse root and every password path, and logind must carry an explicit idle
# policy so a single kiosk seat never auto-suspends. No host adapter is involved.
install -d -m 0755 "$MOUNT/etc/ssh/sshd_config.d"
cat > "$MOUNT/etc/ssh/sshd_config.d/10-knix-auth.conf" <<'SSHD'
# Owned by knix: public keys for knix only. No root, no passwords, no host login.
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitEmptyPasswords no
AuthenticationMethods publickey
SSHD
chmod 0644 "$MOUNT/etc/ssh/sshd_config.d/10-knix-auth.conf"

install -d -m 0755 "$MOUNT/etc/systemd/logind.conf.d"
cat > "$MOUNT/etc/systemd/logind.conf.d/10-knix-login.conf" <<'LOGIND'
# Owned by knix: the kiosk seat must never auto-suspend or lock itself.
[Login]
NAutoVTs=2
ReserveVT=1
IdleAction=ignore
IdleActionSec=30min
LOGIND
chmod 0644 "$MOUNT/etc/systemd/logind.conf.d/10-knix-login.conf"
chroot "$MOUNT" passwd -l root >/dev/null 2>&1 || true

echo "=== verify assembled disk ==="
grep -qx 'PermitRootLogin no' "$MOUNT/etc/ssh/sshd_config.d/10-knix-auth.conf" \
  || { echo "guest sshd policy lost PermitRootLogin" >&2; exit 1; }
grep -qx 'AuthenticationMethods publickey' "$MOUNT/etc/ssh/sshd_config.d/10-knix-auth.conf" \
  || { echo "guest sshd policy lost AuthenticationMethods" >&2; exit 1; }
grep -qx 'IdleAction=ignore' "$MOUNT/etc/systemd/logind.conf.d/10-knix-login.conf" \
  || { echo "guest logind idle policy lost IdleAction" >&2; exit 1; }
grep -E '^(NAME|ID|VERSION)=' "$MOUNT/etc/os-release"
test -x "$MOUNT/usr/bin/sway" && echo "sway: yes"
test -f "$MOUNT/usr/share/wayland-sessions/knix.desktop" && echo "wayland session: yes"
test -f "$MOUNT/etc/udev/rules.d/71-knix-virtio-input.rules" \
  || { echo "missing guest-owned virtio input rule" >&2; exit 1; }
test -f "$MOUNT/etc/systemd/system/knix-session.service" \
  || { echo "missing guest-owned session unit" >&2; exit 1; }
ENABLE_LINK="$MOUNT/etc/systemd/system/multi-user.target.wants/knix-session.service"
test -L "$ENABLE_LINK" \
  || { echo "session unit is not enabled by multi-user.target" >&2; exit 1; }
[ "$(readlink "$ENABLE_LINK")" = "../knix-session.service" ] \
  || { echo "session enablement symlink drifted" >&2; exit 1; }
grep -q '^WantedBy=multi-user.target$' "$MOUNT/etc/systemd/system/knix-session.service" \
  || { echo "session unit lost its [Install] target" >&2; exit 1; }
# session.json declares resilience: the unit must recover a crashed compositor
# itself, with an explicit start-rate limit so a broken sway cannot loop forever.
UNIT_FILE="$MOUNT/etc/systemd/system/knix-session.service"
for directive in 'Restart=on-failure' 'RestartSec=2' \
                 'StartLimitBurst=5' 'StartLimitIntervalSec=30'; do
  grep -q "^$directive\$" "$UNIT_FILE" \
    || { echo "session unit lost its resilience directive: $directive" >&2; exit 1; }
done
echo "resilience: Restart=on-failure / RestartSec=2 / StartLimitBurst=5 in 30s (guest systemd)"
# session.json declares diagnostics: the failure report must be produced by a
# guest-owned OnFailure= unit, never by a build-host script.
DIAG_UNIT_FILE="$MOUNT/etc/systemd/system/knix-session-failure.service"
DIAG_SCRIPT="$MOUNT/usr/lib/knix/session-failure-report.sh"
test -f "$DIAG_UNIT_FILE" \
  || { echo "missing guest-owned failure diagnostics unit" >&2; exit 1; }
test -x "$DIAG_SCRIPT" \
  || { echo "missing executable guest-owned diagnostics script" >&2; exit 1; }
grep -q '^OnFailure=knix-session-failure.service$' "$UNIT_FILE" \
  || { echo "session unit lost its guest-owned OnFailure= trigger" >&2; exit 1; }
grep -q '^ExecStart=/bin/bash /usr/lib/knix/session-failure-report.sh$' "$DIAG_UNIT_FILE" \
  || { echo "failure diagnostics unit does not invoke the guest script" >&2; exit 1; }
for token in 'session-failure.txt' 'session-failure.history' \
             'session-failure.log' 'PDIR=/var/lib/knix' 'boot=' \
             'owner=guest' 'manager=systemd' 'active_state=' \
             'failed_units=' 'exit 0'; do
  grep -q "$token" "$DIAG_SCRIPT" \
    || { echo "diagnostics script lost: $token" >&2; exit 1; }
done
# session.json declares diagnostics.operations: bounded rotation, cross-boot
# statistics and a local alert are all owned by the same guest OnFailure=
# script. The verification drill is confined to /run/knix so it can never
# rotate or rewrite the archive on the shipped disk.
for token in 'LIMIT=8192' 'GENS=3' 'TAG=knix-diagnostics' \
             'KNIX_DIAG_DRILL_DIR' '/run/knix/*' 'session-failure.stats' \
             'session-failure.rotations' 'total_events=' 'boots_seen=' \
             'session-failure.alert' 'systemd-cat -p err'; do
  grep -q -- "$token" "$DIAG_SCRIPT" \
    || { echo "diagnostics script lost operations token: $token" >&2; exit 1; }
done
# session.json declares diagnostics.trends: the same guest script recomputes
# a bounded per-boot profile from its own archive family, with no host input.
for token in 'session-failure.trends' 'TREND_BOOTS=8' 'TREND_LIMIT=4096' \
             'write_trends()' 'trend_boots_kept=' 'trend_events_sum=' \
             'limit_boots=' 'max_events_per_boot=' 'restarts_peak=' \
             'first_seen=' 'last_seen=' 'boots_seen=${BOOTS:-0}' \
             '--- per-boot (newest first, guest-owned) ---'; do
  grep -q -- "$token" "$DIAG_SCRIPT" \
    || { echo "diagnostics script lost trends token: $token" >&2; exit 1; }
done
NOTICE="$MOUNT/etc/profile.d/10-knix-session-alert.sh"
test -f "$NOTICE" \
  || { echo "missing guest-owned login-visible failure notice" >&2; exit 1; }
grep -q '/var/lib/knix/session-failure.alert' "$NOTICE" \
  || { echo "login notice lost the persistent alert path" >&2; exit 1; }
PERSIST_CONF="$MOUNT/etc/systemd/journald.conf.d/knix-persistence.conf"
test -f "$PERSIST_CONF" \
  || { echo "missing guest-owned journald persistence config" >&2; exit 1; }
grep -q '^Storage=persistent$' "$PERSIST_CONF" \
  || { echo "journald storage is not pinned to persistent" >&2; exit 1; }
test -d "$MOUNT/var/lib/knix" \
  || { echo "missing guest-owned persistent diagnostics directory" >&2; exit 1; }
test -d "$MOUNT/var/log/journal" \
  || { echo "missing persistent journal directory" >&2; exit 1; }
echo "diagnostics: guest-owned OnFailure= report + crash history + persistent archive + bounded rotation, cross-boot stats and local alert (systemd + journald)"
echo "seat: seatd + drm,libinput + guest udev rule (autostart enabled)"
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
