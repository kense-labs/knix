#!/bin/bash
# Reproducible knix.img build. One artifact, one receipt, one publish surface.
#
#   platform/image/build.sh --check    # verify every declared input hash
#   platform/image/build.sh --build   # assemble -> cold boot -> verify -> receipt
#
# The kernel and the native rootfs are produced INSIDE the arm64 Linux build
# guest by build-kernel.sh / build-rootfs.sh, each with a self-describing JSON
# summary. This script consumes those artifacts, assembles the single image,
# boots it once and writes a receipt that records every input digest.
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
  echo "CHECK OK: manifest inputs are reproducible"
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
KNIX_DEV_DIR="$DEV" DISK="$HELPER" EXTRA_DISKS="$BUILDDISK $IMAGE" \
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
KNIX_DEV_DIR="$DEV" DISK="$IMAGE" \
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
stop_guest

echo "=== receipt ==="
IMG_SHA="$(sha256 "$IMAGE")"
python3 - "$DEV" "$IMG_SHA" "$ROOT" <<'PY'
import datetime, json, pathlib, sys
dev, img_sha, root = pathlib.Path(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3])
identity = json.loads((root / "platform/identity/identity.json").read_text())
m = json.loads((root / "platform/image/manifest.json").read_text())
kernel = json.loads((dev / "kernel-build.json").read_text())
rootfs = json.loads((dev / "rootfs-build.json").read_text())
receipt = {
    "schema": 2,
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
    "publish_surface": identity["publish_surface"],
}
(dev / "BUILD-RECEIPT.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
PY
"$ROOT/platform/image/sign.sh"
echo "BUILD OK: knix.img + BUILD-RECEIPT.json"
