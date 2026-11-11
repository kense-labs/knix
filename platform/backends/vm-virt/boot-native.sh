#!/bin/bash
# Launch the single knix image through its own EFI system partition.
# A start path only: identity, authentication, UI and device semantics stay
# inside the guest. See platform/CONTRACT.md.
#
#   KNIX_DEV_DIR=../knix-m0 platform/backends/vm-virt/boot-native.sh
#
# A KERNEL override still reaches boot-dev.sh for debugging; the normal path
# starts from EDK2's removable media path on the image's own ESP.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
DEV="${KNIX_DEV_DIR:-$(cd "$ROOT/.." && pwd)/knix-m0}"

export KNIX_DEV_DIR="$DEV"
export DISK="${DISK:-$DEV/knix.img}"
# knix has no cloud-init package. A self-booting image must not be handed a
# host-provided datasource.
export SEED="${SEED-}"
# The guest owns the DRM device and its input semantics. Both are standard
# virtio devices; GPU=none / GPU_INPUT=none keeps the serial+ssh fallback.
export GPU="${GPU-virtio-gpu-pci}"
export GPU_INPUT="${GPU_INPUT-virtio-keyboard-pci virtio-tablet-pci}"
# A fresh EDK2 variable store proves the boot needs no EFI Boot#### entry left
# by an earlier seed boot: EDK2 falls back to the removable media path
# EFI/BOOT/BOOTAA64.EFI on the ESP.
export VARS="${VARS:-$DEV/AAVMF_VARS-selfboot.fd}"

TEMPLATE=""
for c in /opt/homebrew/share/qemu/edk2-arm-vars.fd \
         /usr/local/share/qemu/edk2-arm-vars.fd; do
  if [ -f "$c" ]; then TEMPLATE="$c"; break; fi
done
if [ -z "${KNIX_KEEP_VARS:-}" ]; then
  if [ -n "$TEMPLATE" ]; then
    cp "$TEMPLATE" "$VARS"
  elif [ ! -f "$VARS" ]; then
    echo "missing EDK2 vars template (edk2-arm-vars.fd)" >&2
    exit 1
  fi
fi

if [ ! -f "$DISK" ]; then
  echo "missing image: $DISK (build it with platform/image/build.sh --build)" >&2
  exit 1
fi
if [ ! -f "$VARS" ]; then
  echo "missing EFI variable store: $VARS" >&2
  exit 1
fi

exec "$HERE/boot-dev.sh"
