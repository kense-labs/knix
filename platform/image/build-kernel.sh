#!/bin/bash
# Build the knix kernel natively inside an aarch64 Linux guest.
# The build host only supplies standard virtio devices; no host toolchain,
# framework or state is an input to the kernel.
#
#   KNIX_KERNEL_SRC=/mnt/knix-build/linux platform/image/build-kernel.sh
set -euo pipefail

SRC="${KNIX_KERNEL_SRC:-/mnt/knix-build/linux}"
OUT="${KNIX_KERNEL_OUT:-/mnt/knix-build/out}"
ARTIFACT="${KNIX_KERNEL_ARTIFACT:-/mnt/knix-build/Image.gz}"
JOBS="${KNIX_JOBS:-4}"

cd "$SRC"
echo "kernel source: $(grep -m1 '^VERSION' Makefile) $(grep -m1 '^PATCHLEVEL' Makefile) $(grep -m1 '^SUBLEVEL' Makefile)"
echo "toolchain: $(make --version | head -1) / $(gcc --version | head -1)"

make O="$OUT" defconfig

# virt.config is a fragment: turn off Apple SoC and non mach-virt subsystems.
python3 - "$OUT/.config" "$SRC/arch/arm64/configs/virt.config" <<'PY'
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
grep -E '^CONFIG_(VIRTIO_BLK|VIRTIO_NET|VIRTIO_PCI|EXT4_FS|SERIAL_AMBA_PL011)=' "$OUT/.config" || true
grep -q '^CONFIG_ARCH_APPLE=y' "$OUT/.config" && echo "WARN: Apple SoC still enabled" || echo "CONFIG_ARCH_APPLE off"

make -j"$JOBS" O="$OUT" Image.gz
cp "$OUT/arch/arm64/boot/Image.gz" "$ARTIFACT"
# The build disk is a qcow2 backed by the host. A guest page-cache write that
# is not flushed before QEMU stops is silently lost.
sync
echo "=== artifact ==="
ls -lh "$ARTIFACT"
sha256sum "$ARTIFACT"
