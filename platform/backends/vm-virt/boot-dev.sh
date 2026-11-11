#!/bin/bash
# vm-virt launch path. A start path only: identity, authentication, UI and
# device semantics stay inside the guest. See platform/CONTRACT.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DEV="${KNIX_DEV_DIR:-$(cd "$ROOT/.." && pwd)/knix-m0}"
QEMU="${QEMU:-$(command -v qemu-system-aarch64)}"
DISK="${DISK:-$DEV/knix.img}"
# An empty SEED means no cloud-init datasource: the self-booting knix image
# carries its own EFI system partition and needs none. The helper guest keeps
# the seed ISO as its bootstrap device.
SEED="${SEED-$DEV/seed.iso}"
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
KERNEL_APPEND="${KERNEL_APPEND:-root=/dev/vda2 rw console=ttyAMA0}"
# The standard knix launch (boot-native.sh) enables the guest-owned GPU and
# input devices. The helper/build path leaves them off so the virtio-blk probe
# order, and therefore /dev/vdc and /dev/vdd, never changes.
GPU="${GPU-none}"
GPU_INPUT="${GPU_INPUT-none}"
DISPLAY_BACKEND="${DISPLAY_BACKEND:-none}"
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

# knix.img carries its own EFI system partition. With no KERNEL, EDK2 starts it
# through the removable media path; KERNEL stays available as a debug override.
for f in "$QEMU" "$DISK" "$CODE" "$VARS"; do
  if [ ! -e "$f" ]; then echo "missing: $f" >&2; exit 1; fi
done
if [ -n "$SEED" ]; then
  if [ ! -e "$SEED" ]; then echo "missing: $SEED" >&2; exit 1; fi
fi
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
  -device virtio-rng-pci
  -nic "user,model=virtio-net-pci,hostfwd=tcp::$SSH_PORT-:22"
  -display "$DISPLAY_BACKEND" -serial "file:$SERIAL" -monitor none
  -qmp "unix:$QMP,server=on,wait=off"
  -pidfile "$PIDFILE"
)

if [ -n "$SEED" ]; then
  ARGS+=( -drive "if=virtio,format=raw,file=$SEED,readonly=on" )
fi

# Standard virtio devices only. DRM, input and authentication stay inside the
# guest; no host adapter or private framework is projected into the VM.
if [ "$GPU" != none ]; then
  ARGS+=( -device "$GPU" )
  echo "gpu: $GPU (display=$DISPLAY_BACKEND)"
fi
if [ "$GPU_INPUT" != none ]; then
  for d in $GPU_INPUT; do
    ARGS+=( -device "$d" )
  done
  echo "input: $GPU_INPUT"
fi

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
