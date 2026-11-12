#!/bin/bash
# Reproducible knix.img build. One artifact, one receipt, one publish surface.
#
#   platform/image/build.sh --check    # verify every declared input hash
#   platform/image/build.sh --build   # assemble -> cold boot -> verify -> receipt
#
# The kernel and the native rootfs are produced INSIDE the arm64 Linux build
# guest by build-kernel.sh / build-rootfs.sh, each with a self-describing JSON
# summary. This script consumes those artifacts and assembles the single image.
# It refuses to assemble anything that differs from the kernel/rootfs baseline
# locked in manifest.json, boots once, verifies identity, UEFI self-boot and
# real sway rendering on the guest-owned DRM device, captures the guest-painted
# framebuffer headlessly through QMP, then writes the receipt.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEV="${KNIX_DEV_DIR:-$(cd "$ROOT/.." && pwd)/knix-m0}"
MANIFEST="$ROOT/platform/image/manifest.json"
IMAGE="$DEV/knix.img"
HELPER="${KNIX_HELPER_IMG:-$DEV/knix-helper.img}"
BUILDDISK="${KNIX_BUILD_DISK:-$DEV/build-disk.qcow2}"
KERNEL="${KNIX_KERNEL_IMAGE:-$DEV/knix-Image}"
BOOT="$ROOT/platform/backends/vm-virt/boot-dev.sh"
NATIVE_BOOT="$ROOT/platform/backends/vm-virt/boot-native.sh"
MODE="${1:---check}"

SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -i $DEV/id_knix_m0 -p 2222"

sha256() { shasum -a 256 "$1" | awk '{print $1}'; }

check_inputs() {
  python3 - "$ROOT" "$MANIFEST" "$DEV" <<'PY'
import hashlib, json, pathlib, sys
root, manifest_path, dev = (pathlib.Path(p) for p in sys.argv[1:4])
m = json.loads(manifest_path.read_text())
bad, counts = [], {}
for group, entries in m["inputs"].items():
    if group == "seed" or not isinstance(entries, dict):
        continue
    counts[group] = len(entries)
    for rel, expected in entries.items():
        p = root / rel
        got = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "missing"
        if got != expected:
            bad.append((rel, expected, got))
seed = dev / m["inputs"]["seed"]["name"]
if not seed.is_file():
    bad.append((str(seed), "present", "missing"))
else:
    h = hashlib.sha512()
    with seed.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != m["inputs"]["seed"]["sha512"]:
        bad.append((seed.name, m["inputs"]["seed"]["sha512"], h.hexdigest()))
    # A writable seed can be silently rewritten by a boot. The pristine image
    # must stay read-only so every build starts from the pinned bytes.
    if seed.stat().st_mode & 0o222:
        bad.append((seed.name, "read-only", oct(seed.stat().st_mode & 0o777)))
if bad:
    for b in bad:
        print("INPUT DRIFT:", b, file=sys.stderr)
    sys.exit(1)
print("INPUTS OK: seed sha512 + " +
      ", ".join("%d %s" % (n, g) for g, n in counts.items()))
PY
}

if [ "$MODE" = "--check" ]; then
  check_inputs
  python3 -m json.tool "$MANIFEST" > /dev/null
  python3 "$ROOT/scripts/verify-repro.py"
  echo "CHECK OK: manifest inputs and locked baseline are reproducible"
  exit 0
fi
if [ "$MODE" != "--build" ]; then
  echo "usage: build.sh [--check|--build]" >&2
  exit 2
fi

check_inputs
command -v qemu-img >/dev/null || { echo "qemu-img missing" >&2; exit 1; }
for f in "$HELPER" "$BUILDDISK" "$DEV/seed.iso"; do
  [ -f "$f" ] || { echo "missing build input: $f" >&2; exit 1; }
done

wait_ssh() {
  for _ in $(seq 1 80); do
    if $SSH knix@127.0.0.1 true 2>/dev/null; then return 0; fi
    sleep 3
  done
  return 1
}

# Power off from inside the guest so the qcow2 backing files are flushed by
# the guest before QEMU exits. Killing QEMU directly loses dirty page cache.
stop_guest() {
  local pid
  pid="$(cat "$DEV/qemu.pid")"
  $SSH knix@127.0.0.1 'sudo sync; sudo systemctl poweroff' >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 1
  done
  echo "guest did not stop cleanly; refusing to write a receipt" >&2
  return 1
}

LAUNCH_PID=""
cleanup() {
  if [ -f "$DEV/qemu.pid" ]; then
    kill -TERM "$(cat "$DEV/qemu.pid")" 2>/dev/null || true
  fi
  [ -n "$LAUNCH_PID" ] && wait "$LAUNCH_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== blank target image ==="
# Never derive the target from the seed: two such disks share an ext4 UUID and
# the helper guest will mount the wrong root. Keep the previous artifact so a
# failed assemble can be rolled back.
if [ -f "$IMAGE" ]; then mv -f "$IMAGE" "$IMAGE.prev"; fi
qemu-img create -f qcow2 "$IMAGE" 8G > /dev/null

echo "=== helper guest with build disk + target ==="
# GPU=none is explicit here: adding PCI devices to the helper changes the
# virtio-blk probe order and would move /dev/vdc and /dev/vdd.
KNIX_DEV_DIR="$DEV" DISK="$HELPER" EXTRA_DISKS="$BUILDDISK $IMAGE" \
  GPU=none GPU_INPUT=none DISPLAY_BACKEND=none \
  "$BOOT" > "$DEV/boot-build.log" 2>&1 &
LAUNCH_PID=$!
wait_ssh || { echo "helper guest never became reachable" >&2; exit 1; }
$SSH knix@127.0.0.1 'sudo mount /dev/vdc /mnt/knix-build 2>/dev/null || true;
  echo "=== attached disks ==="; lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS | grep -E "vd(c|d)" || true'

echo "=== fetch build summaries ==="
fetch() {
  $SSH knix@127.0.0.1 "cat $1" > "$2.new"
  [ -s "$2.new" ] || { echo "empty fetch: $1" >&2; return 1; }
  mv -f "$2.new" "$2"
}
fetch /mnt/knix-build/kernel-build.json "$DEV/kernel-build.json"
fetch /mnt/knix-build/rootfs-build.json "$DEV/rootfs-build.json"
fetch /mnt/knix-build/knix-Image "$KERNEL"
fetch /mnt/knix-build/package-versions.txt "$DEV/package-versions.txt"

python3 - "$DEV/kernel-build.json" "$DEV/rootfs-build.json" "$KERNEL" <<'PY'
import hashlib, json, pathlib, sys
ks, rs, image = (pathlib.Path(p) for p in sys.argv[1:4])
k, r = json.loads(ks.read_text()), json.loads(rs.read_text())
h = hashlib.sha256()
with image.open("rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        h.update(chunk)
if h.hexdigest() != k["image_sha256"]:
    print("KERNEL DRIFT:", h.hexdigest(), "!=", k["image_sha256"], file=sys.stderr)
    sys.exit(1)
print("summaries OK: kernel %s, %d modules; rootfs %s" %
      (k["kernel_version"], k["module_count"], r["tarball_sha256"][:12]))
PY
KVER="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["kernel_version"])' "$DEV/kernel-build.json")"
NKO="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["module_count"])' "$DEV/kernel-build.json")"

echo "=== verify locked build baseline ==="
# The summaries are produced inside the guest. Without an in-repo lock, a
# silently refreshed Debian mirror could ship a different rootfs with a valid
# receipt. Every field must equal manifest.json's build_baseline, and the
# guest lockfile must be byte-identical to the one committed in the tree.
python3 - "$MANIFEST" "$DEV/kernel-build.json" "$DEV/rootfs-build.json" \
           "$ROOT/platform/image/package-versions.txt" "$DEV/package-versions.txt" <<'PY'
import hashlib, json, pathlib, sys
mani, ks, rs, lock_repo, lock_guest = (pathlib.Path(p) for p in sys.argv[1:6])
m = json.loads(mani.read_text())
k, r = json.loads(ks.read_text()), json.loads(rs.read_text())
b = m.get("build_baseline")
if not isinstance(b, dict):
    sys.exit("BASELINE DRIFT: manifest.json has no build_baseline lock")
bk, br = b.get("kernel", {}), b.get("rootfs", {})
bad = []
kernel_fields = ("kernel_version", "config_sha256", "image_sha256",
                 "image_gz_sha256", "module_count",
                 "graphics_drivers", "graphics_modules")
rootfs_fields = ("tarball_sha256", "tarball_bytes", "package_set_sha256",
                 "package_count", "package_versions_sha256",
                 "package_versions_count")
for f in kernel_fields:
    if k.get(f) != bk.get(f):
        bad.append("kernel.%s: guest=%r locked=%r" % (f, k.get(f), bk.get(f)))
for f in rootfs_fields:
    if r.get(f) != br.get(f):
        bad.append("rootfs.%s: guest=%r locked=%r" % (f, r.get(f), br.get(f)))
repo_bytes = lock_repo.read_bytes() if lock_repo.is_file() else b""
guest_bytes = lock_guest.read_bytes()
if repo_bytes != guest_bytes:
    bad.append("package-versions.txt differs between the repository and the guest")
if hashlib.sha256(guest_bytes).hexdigest() != br.get("package_versions_sha256"):
    bad.append("guest package lockfile sha256 does not match the baseline")
if bad:
    for line in bad:
        print("BASELINE DRIFT:", line, file=sys.stderr)
    print("Refresh with scripts/refresh-baseline.py: --propose, review the",
          "field-level diff, then --apply --reason \"...\" and rebuild the image.",
          file=sys.stderr)
    sys.exit(1)
print("BASELINE OK: kernel %s/%d modules, rootfs %d package versions locked" %
      (k["kernel_version"], k["module_count"], r["package_versions_count"]))
PY

echo "=== assemble knix.img ==="
$SSH knix@127.0.0.1 'sudo env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin bash -s' \
  < "$ROOT/platform/image/assemble-native.sh" > "$DEV/guest-assemble.log" 2>&1 || {
    cat "$DEV/guest-assemble.log" >&2; exit 1;
  }
tail -12 "$DEV/guest-assemble.log"
stop_guest

echo "=== cold boot verification (UEFI self-boot) ==="
# boot-native.sh starts knix.img from its own ESP with no -kernel, no seed ISO
# and a fresh EDK2 variable store. A reachable ssh proves the image self-boots.
# The cold boot must exercise the standard graphics path: the guest owns the
# DRM device and its input, while the display backend stays headless.
KNIX_DEV_DIR="$DEV" DISK="$IMAGE" QMP="$DEV/qmp.sock" \
  GPU=virtio-gpu-pci GPU_INPUT="virtio-keyboard-pci virtio-tablet-pci" \
  DISPLAY_BACKEND=none \
  "$NATIVE_BOOT" > "$DEV/boot-verify.log" 2>&1 &
LAUNCH_PID=$!
wait_ssh || { echo "knix.img did not self-boot" >&2; exit 1; }

VERIFY_RC=0
$SSH knix@127.0.0.1 "KNIX_KVER=$KVER KNIX_NKO=$NKO bash -s" <<'VERIFY' || VERIFY_RC=$?
set -e
. /etc/os-release
[ "$ID" = knix ]
[ "$NAME" = Knix ]
[ "$(hostname)" = knix ]
[ "$(uname -r)" = "$KNIX_KVER" ]
test -f /etc/knix-release
test "$(ls -1 /usr/share/knix/brand | wc -l)" -eq 4
test -f "/lib/modules/$KNIX_KVER/modules.dep"
[ "$(find "/lib/modules/$KNIX_KVER" -type f -name '*.ko*' | wc -l)" -eq "$KNIX_NKO" ]
sudo modprobe br_netfilter
lsmod | grep -q br_netfilter
[ "$(systemctl --failed --no-legend | wc -l)" -eq 0 ]
test ! -e /var/lib/cloud
test -x /usr/bin/sway
# The guest owns its seat: the compositor runs as knix through seatd, with a
# guest-owned udev rule and a session unit enabled by multi-user.target.
systemctl is-active seatd.service | grep -qx active
for grp in video input render; do
  id -nG knix | tr ' ' '\n' | grep -qx "$grp"
done
test -f /etc/udev/rules.d/71-knix-virtio-input.rules
test -f /etc/systemd/system/knix-session.service
test -L /etc/systemd/system/multi-user.target.wants/knix-session.service
systemctl is-enabled knix-session.service | grep -qx enabled
# This runs before any verification script touches the unit: an active session
# here proves the image booted into its own desktop without a host launcher.
systemctl is-active knix-session.service | grep -qx active
# The self-boot path must be real: the kernel command line comes from the
# loader entry on the ESP, not from QEMU, and the ESP payload is present.
grep -q 'root=/dev/vda2' /proc/cmdline
findmnt /efi > /dev/null
# The ESP is mounted with umask=0077 by systemd; only root may read it.
sudo test -f /efi/EFI/BOOT/BOOTAA64.EFI
sudo test -f /efi/loader/entries/knix.conf
sudo grep -q 'root=/dev/vda2' /efi/loader/entries/knix.conf
test -f "/boot/initrd.img-$KNIX_KVER-knix"
sha256sum "/boot/vmlinuz-$KNIX_KVER-knix"
echo "GUEST VERIFY OK"
VERIFY
if [ "$VERIFY_RC" -ne 0 ]; then
  echo "GUEST VERIFY FAILED (rc=$VERIFY_RC)" >&2
  $SSH knix@127.0.0.1 'set +e; . /etc/os-release; echo "ID=$ID NAME=$NAME"; hostname; uname -r; systemctl --failed --no-legend; cat /proc/cmdline; findmnt /efi; sudo ls -l /efi/EFI/BOOT /efi/loader/entries' >&2 || true
  exit 1
fi

$SSH knix@127.0.0.1 'cd /usr/share/knix/brand && sha256sum *' > "$DEV/guest-brand.sha256"
python3 - "$MANIFEST" "$DEV/guest-brand.sha256" <<'PY'
import json, pathlib, sys
m = json.loads(pathlib.Path(sys.argv[1]).read_text())
expected = {pathlib.Path(k).name: v for k, v in m["inputs"]["brand_sha256"].items()}
got = {}
for line in pathlib.Path(sys.argv[2]).read_text().splitlines():
    digest, name = line.split(None, 1)
    got[name.strip().lstrip("*")] = digest
if got != expected:
    print("BRAND DRIFT:", got, "!=", expected, file=sys.stderr)
    sys.exit(1)
print("BRAND BYTES OK: 4 assets byte-identical")
PY

if $SSH knix@127.0.0.1 'sudo dmesg | grep -qE "failed to load regulatory.db|regulatory.db is malformed|signature is missing/invalid"'; then
  echo "REGULATORY DB DRIFT: cfg80211 rejected regulatory.db" >&2
  exit 1
else
  echo "REGULATORY DB OK"
fi

echo "=== login verification (guest-owned accounts and authentication) ==="
LOGIN_RC=0
$SSH knix@127.0.0.1 'sudo bash -s' \
  < "$ROOT/platform/image/verify-login.sh" > "$DEV/guest-login.log" 2>&1 || LOGIN_RC=$?
cat "$DEV/guest-login.log"
if [ "$LOGIN_RC" -ne 0 ] \
   || ! grep -q 'LOGIN POLICY OK' "$DEV/guest-login.log" \
   || ! grep -q 'LOGIN VERIFY OK' "$DEV/guest-login.log"; then
  echo "LOGIN VERIFY FAILED (rc=$LOGIN_RC)" >&2
  exit 1
fi

echo "=== graphics verification (guest-owned DRM + sway) ==="
GRAPHICS_RC=0
$SSH knix@127.0.0.1 'sudo bash -s' \
  < "$ROOT/platform/image/verify-graphics.sh" > "$DEV/guest-graphics.log" 2>&1 || GRAPHICS_RC=$?
cat "$DEV/guest-graphics.log"
if [ "$GRAPHICS_RC" -ne 0 ] \
   || ! grep -q 'AUTOSTART VERIFY OK' "$DEV/guest-graphics.log" \
   || ! grep -q 'RESILIENCE VERIFY OK' "$DEV/guest-graphics.log" \
   || ! grep -q 'LIMIT VERIFY OK' "$DEV/guest-graphics.log" \
   || ! grep -q 'DIAGNOSTICS VERIFY OK' "$DEV/guest-graphics.log" \
   || ! grep -q 'PERSISTENCE VERIFY OK' "$DEV/guest-graphics.log" \
   || ! grep -q 'DIAGNOSTICS OPS VERIFY OK' "$DEV/guest-graphics.log" \
   || ! grep -q 'ROTATION VERIFY OK' "$DEV/guest-graphics.log" \
   || ! grep -q 'SESSION VERIFY OK' "$DEV/guest-graphics.log" \
   || ! grep -q 'GRAPHICS VERIFY OK' "$DEV/guest-graphics.log"; then
  echo "AUTOSTART/RESILIENCE/LIMIT/DIAGNOSTICS/PERSISTENCE/OPS/ROTATION/SESSION/GRAPHICS VERIFY FAILED (rc=$GRAPHICS_RC)" >&2
  exit 1
fi

echo "=== framebuffer verification (guest-drawn pixels via QMP) ==="
# The guest chose a colour from its own entropy and sway painted it. Read the
# standard virtio-gpu scanout headlessly and require those exact pixels plus
# the guest swaybar before a receipt is allowed to exist. No host-side drawing.
FRAMEBUFFER_RC=0
python3 "$ROOT/platform/image/verify-framebuffer.py" \
  --qmp "$DEV/qmp.sock" --out "$DEV/framebuffer.ppm" \
  --log "$DEV/guest-graphics.log" --summary "$DEV/framebuffer-verify.json" \
  > "$DEV/framebuffer-verify.log" 2>&1 || FRAMEBUFFER_RC=$?
cat "$DEV/framebuffer-verify.log"
if [ "$FRAMEBUFFER_RC" -ne 0 ] \
   || ! grep -q 'FRAMEBUFFER VERIFY OK' "$DEV/framebuffer-verify.log"; then
  echo "FRAMEBUFFER VERIFY FAILED (rc=$FRAMEBUFFER_RC)" >&2
  exit 1
fi
stop_guest

echo "=== reboot verification (guest-owned crash archive survives a restart) ==="
# Relaunch the SAME disk with its EFI variable store kept: no rebuild, no seed
# ISO and no host-written state. The guest must still hold the crash archive it
# wrote during the cold-boot verification, while the /run tmpfs is brand new.
# The receipt is written only after this second clean power-off, so its sha256
# still describes exactly the bytes that ship.
KNIX_DEV_DIR="$DEV" DISK="$IMAGE" QMP="$DEV/qmp-persistence.sock" \
  SERIAL="$DEV/serial-persistence.log" KNIX_KEEP_VARS=1 \
  GPU=virtio-gpu-pci GPU_INPUT="virtio-keyboard-pci virtio-tablet-pci" \
  DISPLAY_BACKEND=none \
  "$NATIVE_BOOT" > "$DEV/boot-persistence.log" 2>&1 &
LAUNCH_PID=$!
wait_ssh || { echo "knix.img did not re-boot for persistence verification" >&2; exit 1; }

PERSIST_RC=0
$SSH knix@127.0.0.1 'sudo bash -s' > "$DEV/guest-persistence.log" 2>&1 <<'PERSIST' || PERSIST_RC=$?
set -e
PDIR=/var/lib/knix
ARCHIVE="$PDIR/session-failure.log"
SNAPSHOT="$PDIR/session-failure.txt"
test -d "$PDIR"
[ -s "$ARCHIVE" ]
[ -s "$SNAPSHOT" ]
# A fresh /run tmpfs proves these are not leftovers of an in-memory snapshot.
if [ -e /run/knix/session-failure.history ]; then
  echo "runtime crash history survived a reboot" >&2; exit 1
fi
if [ -e /run/knix/session-failure.txt ]; then
  echo "runtime failure snapshot survived a reboot" >&2; exit 1
fi
grep -q '^boot=' "$SNAPSHOT"
grep -q '^active_state=failed$' "$SNAPSHOT"
grep -q 'active_state=failed' "$ARCHIVE"
EVENTS="$(grep -c 'active_state=' "$ARCHIVE")"
[ "$EVENTS" -ge 2 ]
CURRENT_BOOT="$(cat /proc/sys/kernel/random/boot_id)"
STORED_BOOT="$(sed -n 's/^boot=//p' "$SNAPSHOT" | head -1)"
[ -n "$STORED_BOOT" ]
if [ "$STORED_BOOT" = "$CURRENT_BOOT" ]; then
  echo "the archive was written in this boot, not the previous one" >&2; exit 1
fi
BOOTS="$(journalctl --list-boots --no-pager 2>/dev/null | grep -cE '^[[:space:]]*-?[0-9]+[[:space:]]+[0-9a-f]{32}' || true)"
[ "$BOOTS" -ge 2 ] || { echo "journal lost the previous boot" >&2; exit 1; }
[ "$(systemctl --failed --no-legend | wc -l)" -eq 0 ]
systemctl is-active ssh.service | grep -qx active
systemctl is-active knix-session.service | grep -qx active
[ -c /dev/ttyAMA0 ]
echo "PERSISTENCE EVENTS: $EVENTS"
echo "DIAGNOSTICS PERSISTENCE VERIFY OK: guest archive survived reboot ($EVENTS events, boot $STORED_BOOT != $CURRENT_BOOT, $BOOTS boots in the journal)"

# P15: the same guest-owned bookkeeping must keep counting in a NEW boot. The
# host only asks guest systemd to signal its own compositor; every write below
# is performed by the guest OnFailure= unit. A verification step must never
# leave a degraded session on the published disk.
STATS="$PDIR/session-failure.stats"
ALERT="$PDIR/session-failure.alert"
NOTICE=/etc/profile.d/10-knix-session-alert.sh
test -s "$STATS" || { echo "guest cross-boot statistics did not survive" >&2; exit 1; }
test -s "$ALERT" || { echo "guest persistent alert did not survive" >&2; exit 1; }
test -f "$NOTICE" || { echo "guest login notice did not survive" >&2; exit 1; }
grep -q '^owner=guest$' "$STATS" \
  || { echo "surviving statistics are not guest-owned" >&2; exit 1; }
BEFORE_EVENTS="$(sed -n 's/^total_events=//p' "$STATS" | head -1)"
BEFORE_BOOTS="$(sed -n 's/^boots_seen=//p' "$STATS" | head -1)"
[ -n "$BEFORE_EVENTS" ] && [ "$BEFORE_EVENTS" -ge 2 ] \
  || { echo "surviving statistics lost the crash timeline" >&2; exit 1; }
grep -q 'active_state=failed' "$ALERT" \
  || { echo "surviving alert lost the terminal failed state" >&2; exit 1; }
journalctl -t knix-diagnostics -p err --no-pager 2>/dev/null \
  | grep -q 'knix session failure' \
  || { echo "the err-priority guest alert did not survive" >&2; exit 1; }
# Signal the compositor through guest systemd. OnFailure= reports the failure,
# which appends a second boot id to the archive and advances the statistics.
systemctl reset-failed knix-session.service >/dev/null 2>&1 || true
# One SIGKILL normally produces an OnFailure= report, but a fast auto-restart
# may finish before the oneshot lands. Retry a bounded number of times; the
# guest start-rate limit (5 in 30s) is never reached.
REPORTED=0
for _attempt in 1 2 3; do
  systemctl kill --signal=SIGKILL knix-session.service >/dev/null 2>&1 || true
  for _ in $(seq 1 10); do
    if grep -q "boot=$CURRENT_BOOT" "$ARCHIVE"; then REPORTED=1; break; fi
    sleep 1
  done
  if [ "$REPORTED" = 1 ]; then break; fi
  systemctl reset-failed knix-session.service >/dev/null 2>&1 || true
done
[ "$REPORTED" = 1 ] \
  || { echo "guest OnFailure= did not report a failure in the second boot" >&2; exit 1; }
AFTER_EVENTS="$(sed -n 's/^total_events=//p' "$STATS" | head -1)"
AFTER_BOOTS="$(sed -n 's/^boots_seen=//p' "$STATS" | head -1)"
[ -n "$AFTER_EVENTS" ] && [ "$AFTER_EVENTS" -gt "$BEFORE_EVENTS" ] \
  || { echo "guest statistics did not advance across the reboot ($BEFORE_EVENTS -> ${AFTER_EVENTS:-none})" >&2; exit 1; }
[ -n "$AFTER_BOOTS" ] && [ "$AFTER_BOOTS" -ge 2 ] \
  || { echo "guest statistics did not keep both boots (${AFTER_BOOTS:-none})" >&2; exit 1; }
grep -q "boot=$STORED_BOOT" "$ARCHIVE" \
  || { echo "the previous boot vanished from the guest archive" >&2; exit 1; }
grep -q "boot=$CURRENT_BOOT" "$ALERT" \
  || { echo "the guest did not refresh its alert in this boot" >&2; exit 1; }
# Guest systemd restores the session on its own; never ship a failed unit.
systemctl reset-failed knix-session.service >/dev/null 2>&1 || true
systemctl restart knix-session.service \
  || { echo "guest systemd did not recover after the second-boot failure" >&2; exit 1; }
RECOVERED=0
for _ in $(seq 1 30); do
  if systemctl is-active knix-session.service | grep -qx active \
     && [ "$(systemctl --failed --no-legend | wc -l)" -eq 0 ]; then
    RECOVERED=1; break
  fi
  sleep 1
done
[ "$RECOVERED" = 1 ] \
  || { echo "the guest session stayed degraded after the second-boot failure" >&2; exit 1; }
[ "$(ps -o user= -C sway | head -1 | tr -d ' ')" = knix ] \
  || { echo "the recovered compositor is not knix-owned" >&2; exit 1; }
echo "OPS EVENTS: $AFTER_EVENTS"
echo "OPS BOOTS: $AFTER_BOOTS"
echo "DIAGNOSTICS OPS PERSISTENCE VERIFY OK: guest archive and stats advanced across reboot ($BEFORE_EVENTS -> $AFTER_EVENTS events, $AFTER_BOOTS boots), alert + err journal intact, session recovered as knix"
PERSIST
cat "$DEV/guest-persistence.log"
if [ "$PERSIST_RC" -ne 0 ] \
   || ! grep -q 'DIAGNOSTICS PERSISTENCE VERIFY OK' "$DEV/guest-persistence.log" \
   || ! grep -q '^PERSISTENCE EVENTS: ' "$DEV/guest-persistence.log" \
   || ! grep -q 'DIAGNOSTICS OPS PERSISTENCE VERIFY OK' "$DEV/guest-persistence.log" \
   || ! grep -q '^OPS EVENTS: ' "$DEV/guest-persistence.log"; then
  echo "DIAGNOSTICS PERSISTENCE/OPS VERIFY FAILED (rc=$PERSIST_RC)" >&2
  exit 1
fi
stop_guest

echo "=== receipt ==="
IMG_SHA="$(sha256 "$IMAGE")"
python3 - "$DEV" "$IMG_SHA" "$ROOT" <<'PY'
import datetime, hashlib, json, pathlib, re, sys
dev, img_sha, root = pathlib.Path(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3])
# The receipt records how many crashes the guest itself reported. Refuse to
# write a receipt when the guest-produced verification log lacks that line.
guest_log = (dev / "guest-graphics.log").read_text()
# P16: the receipt must not exist unless the guest itself proved its login
# policy with both the static assertions and the real authentication refusal.
login_log = (dev / "guest-login.log").read_text()
if "LOGIN POLICY OK" not in login_log:
    sys.exit("LOGIN DRIFT: the guest did not assert its login policy")
if "LOGIN VERIFY OK" not in login_log:
    sys.exit("LOGIN DRIFT: the guest did not refuse root and password logins")
diag_match = re.search(r"^DIAGNOSTICS EVENTS: (\d+)$", guest_log, re.M)
if not diag_match or int(diag_match.group(1)) < 2:
    sys.exit("DIAGNOSTICS DRIFT: guest systemd did not report its crash timeline")
diag_events = int(diag_match.group(1))
# The reboot verification must also have succeeded, otherwise the crash
# timeline is only a /run snapshot that would vanish on the next boot.
persist_log = (dev / "guest-persistence.log").read_text()
persist_match = re.search(r"^PERSISTENCE EVENTS: (\d+)$", persist_log, re.M)
if (not persist_match or int(persist_match.group(1)) < 2
        or "DIAGNOSTICS PERSISTENCE VERIFY OK" not in persist_log):
    sys.exit("PERSISTENCE DRIFT: the guest crash archive did not survive a reboot")
persist_events = int(persist_match.group(1))
m = json.loads((root / "platform/image/manifest.json").read_text())
# P15: the receipt must describe real guest-owned operations, not a promise.
# The cold-boot log proves the rotation drill and the local alert; the second
# boot log proves the statistics advanced with a new boot id.
if ("DIAGNOSTICS OPS VERIFY OK" not in guest_log
        or "ROTATION VERIFY OK" not in guest_log):
    sys.exit("OPS DRIFT: the guest did not verify its diagnostics operations")
ops_match = re.search(r"^OPS EVENTS: (\d+)$", persist_log, re.M)
boots_match = re.search(r"^OPS BOOTS: (\d+)$", persist_log, re.M)
min_boots = m["graphics"]["diagnostics"]["operations"]["min_boots_seen"]
if (not ops_match or not boots_match
        or int(ops_match.group(1)) <= persist_events
        or int(boots_match.group(1)) < min_boots
        or "DIAGNOSTICS OPS PERSISTENCE VERIFY OK" not in persist_log):
    sys.exit("OPS DRIFT: guest diagnostics operations did not advance across a reboot")
ops_events = int(ops_match.group(1))
ops_boots = int(boots_match.group(1))
identity = json.loads((root / "platform/identity/identity.json").read_text())
kernel = json.loads((dev / "kernel-build.json").read_text())
rootfs = json.loads((dev / "rootfs-build.json").read_text())
framebuffer = json.loads((dev / "framebuffer-verify.json").read_text())
if framebuffer.get("verified") is not True or framebuffer.get("owner") != "guest":
    sys.exit("FRAMEBUFFER DRIFT: the pixel proof was not produced by the guest")
baseline = m["build_baseline"]
baseline_sha = hashlib.sha256(
    json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
receipt = {
    "schema": 3,
    "product": "knix",
    "version": identity["version"],
    "stage": identity["stage"],
    "artifact": "knix.img",
    "format": "qcow2",
    "sha256": img_sha,
    "bytes": (dev / "knix.img").stat().st_size,
    "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "kernel": kernel,
    "rootfs": rootfs,
    "inputs": {k: v for k, v in m["inputs"].items() if k != "seed"},
    "seed_sha512": m["inputs"]["seed"]["sha512"],
    "boot": {
        "entry": "platform/backends/vm-virt/boot-native.sh",
        "method": "UEFI self-boot",
        "esp": "EFI/BOOT/BOOTAA64.EFI (removable media path)",
        "initrd": "EFI/knix/initrd.img-%s-knix" % kernel["kernel_version"],
        "append": "root=/dev/vda2 rw console=ttyAMA0",
        "seed_iso": False,
    },
    "graphics": {
        "device": m["graphics"]["device"],
        "input": m["graphics"]["input"],
        "compositor": m["graphics"]["compositor"],
        "owner": m["graphics"]["owner"],
        "verify": m["graphics"]["verify"],
        "verified": True,
        "framebuffer": {
            "owner": m["graphics"]["framebuffer"]["owner"],
            "capture": m["graphics"]["framebuffer"]["capture"],
            "verify": m["graphics"]["framebuffer"]["verify"],
            "width": framebuffer["width"],
            "height": framebuffer["height"],
            "pattern_sha256": framebuffer["pattern_sha256"],
            "samples": framebuffer["samples"],
            "matching_samples": framebuffer["matching_samples"],
            "bar_pixels": framebuffer["bar_pixels"],
            "verified": True,
        },
        "session": {
            "user": m["graphics"]["session_user"],
            "seat_manager": m["graphics"]["seat_manager"],
            "unit": m["graphics"]["unit"],
            "backends": m["graphics"]["backends"],
            "input_devices": m["graphics"]["input_devices"],
            "verified": True,
            "authentication": {
                "owner": m["graphics"]["authentication"]["owner"],
                "manager": m["graphics"]["authentication"]["manager"],
                "policy": m["graphics"]["authentication"]["policy"],
                "logind_policy": m["graphics"]["authentication"]["logind_policy"],
                "accounts": m["graphics"]["authentication"]["accounts"],
                "root_login": False,
                "password_authentication": False,
                "public_key_authentication": True,
                "autologin": True,
                "idle_action": m["graphics"]["authentication"]["idle_action"],
                "idle_action_sec": m["graphics"]["authentication"]["idle_action_sec"],
                "verified": True,
            },
            "autostart": {
                "enabled": m["graphics"]["autostart"],
                "target": m["graphics"]["autostart_target"],
                "condition": m["graphics"]["autostart_condition"],
                "verified": True,
            },
            "resilience": {
                "owner": m["graphics"]["resilience"]["owner"],
                "manager": m["graphics"]["resilience"]["manager"],
                "restart": m["graphics"]["resilience"]["restart"],
                "restart_sec": m["graphics"]["resilience"]["restart_sec"],
                "start_limit_burst": m["graphics"]["resilience"]["start_limit_burst"],
                "start_limit_interval_sec": m["graphics"]["resilience"]["start_limit_interval_sec"],
                "verified": True,
            },
            "crash_loop": {
                "owner": m["graphics"]["crash_loop"]["owner"],
                "manager": m["graphics"]["crash_loop"]["manager"],
                "burst": m["graphics"]["crash_loop"]["burst"],
                "interval_sec": m["graphics"]["crash_loop"]["interval_sec"],
                "degraded_failed_units": m["graphics"]["crash_loop"]["degraded_failed_units"],
                "verified": True,
            },
            "diagnostics": {
                "owner": m["graphics"]["diagnostics"]["owner"],
                "manager": m["graphics"]["diagnostics"]["manager"],
                "unit": m["graphics"]["diagnostics"]["unit"],
                "report": m["graphics"]["diagnostics"]["report"],
                "history": m["graphics"]["diagnostics"]["history"],
                "events": diag_events,
                "verified": True,
                "persistence": {
                    "owner": m["graphics"]["diagnostics"]["persistence"]["owner"],
                    "manager": m["graphics"]["diagnostics"]["persistence"]["manager"],
                    "log": m["graphics"]["diagnostics"]["persistence"]["log"],
                    "snapshot": m["graphics"]["diagnostics"]["persistence"]["snapshot"],
                    "journal": m["graphics"]["diagnostics"]["persistence"]["journal"],
                    "events": persist_events,
                    "survives_reboot": True,
                    "verified": True,
                },
                "operations": {
                    "owner": m["graphics"]["diagnostics"]["operations"]["owner"],
                    "statistics": m["graphics"]["diagnostics"]["operations"]["statistics"],
                    "alert": m["graphics"]["diagnostics"]["operations"]["alert"],
                    "archive_limit_bytes": m["graphics"]["diagnostics"]["operations"]["archive_limit_bytes"],
                    "generations": m["graphics"]["diagnostics"]["operations"]["generations"],
                    "journal_tag": m["graphics"]["diagnostics"]["operations"]["journal_tag"],
                    "journal_priority": m["graphics"]["diagnostics"]["operations"]["journal_priority"],
                    "rotation_drill_verified": True,
                    "cross_boot_verified": True,
                    "total_events": ops_events,
                    "boots_seen": ops_boots,
                    "verified": True,
                },
            },
        },
    },
    "baseline": {
        "sha256": baseline_sha,
        "kernel": baseline["kernel"],
        "rootfs": baseline["rootfs"],
        "package_lock": "platform/image/package-versions.txt",
        "verified": True,
    },
    "publish_surface": identity["publish_surface"],
}
(dev / "BUILD-RECEIPT.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
PY
"$ROOT/platform/image/sign.sh"
echo "BUILD OK: knix.img + BUILD-RECEIPT.json"
