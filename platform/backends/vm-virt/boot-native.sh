#!/bin/bash
# Launch the single knix image with the kernel built from repos/linux.
# A start path only: identity, authentication, UI and device semantics stay
# inside the guest. See platform/CONTRACT.md.
#
#   KNIX_DEV_DIR=../knix-m0 platform/backends/vm-virt/boot-native.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
DEV="${KNIX_DEV_DIR:-$(cd "$ROOT/.." && pwd)/knix-m0}"

export KNIX_DEV_DIR="$DEV"
export DISK="${DISK:-$DEV/knix.img}"
export KERNEL="${KERNEL:-$DEV/knix-Image}"
export KERNEL_APPEND="${KERNEL_APPEND:-root=/dev/vda1 rw console=ttyAMA0}"

if [ ! -f "$KERNEL" ]; then
  echo "missing kernel: $KERNEL (build it with platform/image/build-kernel.sh)" >&2
  exit 1
fi
if [ ! -f "$DISK" ]; then
  echo "missing image: $DISK (build it with platform/image/build.sh --build)" >&2
  exit 1
fi

exec "$HERE/boot-dev.sh"
