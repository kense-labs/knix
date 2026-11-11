#!/bin/bash
# vm-virt launch path. A start path only: identity, authentication, UI and
# device semantics stay inside the guest. See platform/CONTRACT.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DEV="${KNIX_DEV_DIR:-$(cd "$ROOT/.." && pwd)/knix-m0}"
QEMU="${QEMU:-$(command -v qemu-system-aarch64)}"
DISK="${DISK:-$DEV/knix.img}"
SEED="${SEED:-$DEV/seed.iso}"
VARS="${VARS:-$DEV/AAVMF_VARS.fd}"
SERIAL="${SERIAL:-$DEV/serial.log}"
PIDFILE="${PIDFILE:-$DEV/qemu.pid}"
QMP="${QMP:-$DEV/qmp.sock}"

SSH_PORT="${SSH_PORT:-2222}"
ACCEL="${ACCEL:-hvf}"
CPU="${CPU:-host}"
GIC="${GIC:-3}"
SMP="${SMP:-4}"
MEM="${MEM:-4096}"
KERNEL="${KERNEL:-}"
INITRD="${INITRD:-}"
KERNEL_APPEND="${KERNEL_APPEND:-root=/dev/vda1 ro console=ttyAMA0}"
EXTRA_DISK="${EXTRA_DISK:-}"
# Space-separated list for stages that need more than one extra virtio disk
# (for example a build disk plus a target image being assembled).
EXTRA_DISKS="${EXTRA_DISKS:-}"
EXTRA_LIST="$EXTRA_DISK $EXTRA_DISKS"

if [ -z "${CODE:-}" ]; then
  for c in /opt/homebrew/share/qemu/edk2-aarch64-code.fd \
           /usr/local/share/qemu/edk2-aarch64-code.fd \
           "$DEV/AAVMF_CODE.fd"; do
    if [ -f "$c" ]; then CODE="$c"; break; fi
  done
fi
: "${CODE:?UEFI code firmware not found; set CODE=...}"

for f in "$QEMU" "$DISK" "$SEED" "$CODE" "$VARS"; do
  if [ ! -e "$f" ]; then echo "missing: $f" >&2; exit 1; fi
done
for d in $EXTRA_LIST; do
  if [ ! -e "$d" ]; then echo "missing: $d" >&2; exit 1; fi
done

: > "$SERIAL"
rm -f "$QMP"
echo "launch vm-virt: accel=$ACCEL cpu=$CPU gic=$GIC smp=$SMP mem=${MEM}M disk=$DISK"

ARGS=(
  -name knix -accel "$ACCEL" -cpu "$CPU" -machine "virt,gic-version=$GIC"
  -smp "$SMP" -m "$MEM"
  -drive "if=pflash,format=raw,file=$CODE,readonly=on"
  -drive "if=pflash,format=raw,file=$VARS"
  -drive "if=virtio,format=qcow2,file=$DISK"
  -drive "if=virtio,format=raw,file=$SEED,readonly=on"
  -device virtio-rng-pci
  -nic "user,model=virtio-net-pci,hostfwd=tcp::$SSH_PORT-:22"
  -display none -serial "file:$SERIAL" -monitor none
  -qmp "unix:$QMP,server=on,wait=off"
  -pidfile "$PIDFILE"
)

for d in $EXTRA_LIST; do
  ARGS+=( -drive "if=virtio,format=qcow2,file=$d" )
  echo "extra disk: $d"
done

if [ -n "$KERNEL" ]; then
  ARGS+=( -kernel "$KERNEL" -append "$KERNEL_APPEND" )
  [ -n "$INITRD" ] && ARGS+=( -initrd "$INITRD" )
  echo "direct kernel boot: $KERNEL"
fi

exec "$QEMU" "${ARGS[@]}"
