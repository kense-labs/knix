#!/bin/bash
# Build the knix kernel and its modules natively inside an aarch64 Linux guest.
# The build host only supplies standard virtio devices; no host toolchain,
# framework or state is an input to the kernel.
#
#   platform/image/build-kernel.sh --check
#   platform/image/build-kernel.sh --apply
#   KNIX_MODE=--apply platform/image/build-kernel.sh   # streamed over ssh
#   KNIX_CLEAN=1 platform/image/build-kernel.sh --apply
set -euo pipefail

SRC="${KNIX_KERNEL_SRC:-/mnt/knix-build/linux}"
BUILD="${KNIX_BUILD_DIR:-/mnt/knix-build}"
OUT="${KNIX_KERNEL_OUT:-$BUILD/out}"
ARTIFACT="${KNIX_KERNEL_ARTIFACT:-$BUILD/Image.gz}"
IMAGE="${KNIX_KERNEL_IMAGE:-$BUILD/knix-Image}"
MODULES="${KNIX_MODULES_DIR:-$BUILD/modules}"
SUMMARY="${KNIX_KERNEL_SUMMARY:-$BUILD/kernel-build.json}"
JOBS="${KNIX_JOBS:-4}"
MODE="${1:-${KNIX_MODE:---apply}}"
FRAGMENT="${KNIX_KERNEL_FRAGMENT:-$SRC/arch/arm64/configs/virt.config}"

# A -kernel boot has no initrd and no /lib/modules before the root filesystem
# is mounted, so every one of these has to be built in, not a module.
BOOT_DRIVERS=(CONFIG_VIRTIO_BLK CONFIG_VIRTIO_NET CONFIG_VIRTIO_PCI
              CONFIG_EXT4_FS CONFIG_SERIAL_AMBA_PL011 CONFIG_DEVTMPFS)

die() { echo "$*" >&2; exit 1; }

[ -f "$SRC/Makefile" ] || die "kernel source missing: $SRC/Makefile"
[ -f "$FRAGMENT" ] || die "config fragment missing: $FRAGMENT"

# virt.config is a fragment of disabled options. It must never turn off a boot
# driver; this check also runs in CI, where no toolchain is present.
python3 - "$FRAGMENT" "${BOOT_DRIVERS[@]}" <<'PY'
import pathlib, re, sys
frag = pathlib.Path(sys.argv[1])
off = set()
for line in frag.read_text().splitlines():
    m = re.match(r'# (CONFIG_\w+) is not set', line.strip())
    if m:
        off.add(m.group(1))
clash = sorted(off.intersection(sys.argv[2:]))
if clash:
    print("fragment disables boot drivers: %s" % ", ".join(clash), file=sys.stderr)
    sys.exit(1)
print("fragment options: %d, boot drivers untouched" % len(off))
PY

if [ "$MODE" = "--check" ]; then
  echo "kernel source: $SRC"
  grep -E '^(VERSION|PATCHLEVEL|SUBLEVEL) =' "$SRC/Makefile" | head -3
  echo "config fragment: $FRAGMENT"
  echo "boot drivers stay built-in: ${BOOT_DRIVERS[*]}"
  echo "kernel config resolves; guest is the build environment"
  exit 0
fi
[ "$MODE" = "--apply" ] || die "usage: build-kernel.sh [--check|--apply]"

for tool in make gcc python3 gzip; do
  command -v "$tool" >/dev/null || die "missing build tool: $tool"
done

# A guest that is stopped before its page cache is flushed leaves truncated
# objects in the qcow2 build disk. modpost then fails with a section header
# offset beyond the file size, so detect that tree and rebuild it instead.
elf_healthy() {
  python3 - "$1" <<'PY'
import pathlib, struct, sys
p = pathlib.Path(sys.argv[1])
with p.open("rb") as f:
    h = f.read(64)
if len(h) < 64 or h[:4] != b"\x7fELF" or h[4] != 2:
    print("not a 64-bit ELF: %s" % p, file=sys.stderr)
    sys.exit(2)
shoff = struct.unpack_from("<Q", h, 0x28)[0]
shentsize, shnum = struct.unpack_from("<HH", h, 0x3A)
size = p.stat().st_size
end = shoff + shentsize * shnum if shnum else shoff
if shoff > size or end > size:
    print("truncated ELF: shoff=%d end=%d filesize=%d" % (shoff, end, size),
          file=sys.stderr)
    sys.exit(1)
print("ELF OK: shoff=%d end=%d filesize=%d" % (shoff, end, size))
PY
}

CLEAN="${KNIX_CLEAN:-0}"
if [ -f "$OUT/vmlinux.o" ] && ! elf_healthy "$OUT/vmlinux.o"; then
  echo "corrupt prior build tree detected; rebuilding from scratch"
  CLEAN=1
fi
if [ "$CLEAN" = "1" ]; then
  case "$OUT" in
    ""|"/"|"$SRC"|"$BUILD") die "refusing to clean unsafe output path: $OUT" ;;
  esac
  echo "cleaning build tree: $OUT"
  rm -rf -- "$OUT"
fi
mkdir -p "$OUT" "$MODULES"

cd "$SRC"
echo "toolchain: $(make --version | head -1) / $(gcc --version | head -1)"
make O="$OUT" defconfig

python3 - "$OUT/.config" "$FRAGMENT" <<'PY'
import pathlib, re, sys
cfg = pathlib.Path(sys.argv[1])
frag = pathlib.Path(sys.argv[2])
off = [re.match(r'# (CONFIG_\w+) is not set', l.strip()).group(1)
       for l in frag.read_text().splitlines()
       if l.startswith('# CONFIG_') and l.strip().endswith('is not set')]
out, changed = [], []
for l in cfg.read_text().splitlines():
    m = re.match(r'^(CONFIG_\w+)=', l)
    if m and m.group(1) in off:
        out.append('# %s is not set' % m.group(1))
        changed.append(m.group(1))
    else:
        out.append(l)
cfg.write_text('\n'.join(out) + '\n')
print('fragment options: %d, turned off: %d' % (len(off), len(changed)))
PY

make O="$OUT" olddefconfig
echo "=== resolved virtio baseline ==="
for d in "${BOOT_DRIVERS[@]}"; do
  v="$(grep -E "^${d}=" "$OUT/.config" || true)"
  [ "$v" = "${d}=y" ] || die "boot driver is not built in: ${v:-$d is not set}"
  echo "$v"
done
grep -q '^CONFIG_ARCH_APPLE=y' "$OUT/.config" && echo "WARN: Apple SoC still enabled" || echo "CONFIG_ARCH_APPLE off"

make -j"$JOBS" O="$OUT" Image.gz modules
KVER="$(make -s O="$OUT" kernelrelease | tail -1)"
echo "kernelrelease: $KVER"
elf_healthy "$OUT/vmlinux.o"

make -j"$JOBS" O="$OUT" INSTALL_MOD_PATH="$MODULES" modules_install
MODDIR="$MODULES/lib/modules/$KVER"
[ -d "$MODDIR" ] || die "modules_install did not create $MODDIR"
NKO="$(find "$MODDIR" -type f \( -name '*.ko' -o -name '*.ko.xz' -o -name '*.ko.zst' -o -name '*.ko.gz' \) | wc -l)"
[ "$NKO" -gt 0 ] || die "no kernel modules were installed into $MODDIR"

cp "$OUT/arch/arm64/boot/Image.gz" "$ARTIFACT"
cp "$OUT/arch/arm64/boot/Image" "$IMAGE"
[ -s "$ARTIFACT" ] || die "empty compressed kernel: $ARTIFACT"
[ -s "$IMAGE" ] || die "empty kernel image: $IMAGE"
gzip -t "$ARTIFACT"

python3 - "$SUMMARY" "$KVER" "$ARTIFACT" "$IMAGE" "$MODDIR" "$NKO" "${BOOT_DRIVERS[*]}" "$OUT/.config" <<'PY'
import hashlib, json, pathlib, sys
out, kver, gz, image, moddir, nko, drivers, cfg = sys.argv[1:]
sha = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
data = {
    "schema": 1,
    "source": "repos/linux",
    "config_fragment": "arch/arm64/configs/virt.config",
    "config_sha256": sha(cfg),
    "config_note": "defconfig + virt.config; the regulatory.db variant is selected at assemble time",
    "kernel_version": kver,
    "image_sha256": sha(image),
    "image_gz_sha256": sha(gz),
    "modules_dir": "modules/lib/modules/" + kver,
    "module_count": int(nko),
    "boot_drivers": drivers.split(),
}
pathlib.Path(out).write_text(json.dumps(data, indent=2) + "\n")
print(json.dumps(data, indent=2))
PY

# The build disk is a qcow2 backed by the host. A guest page-cache write that
# is not flushed before QEMU stops is silently lost.
sync
echo "=== artifacts ==="
ls -lh "$ARTIFACT" "$IMAGE" "$SUMMARY"
echo "KERNEL BUILD OK: $KVER, $NKO modules"
