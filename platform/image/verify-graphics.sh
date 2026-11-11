#!/bin/bash
# Verify that knix owns a real DRM device and renders its own Wayland session.
# Executed INSIDE the arm64 guest as root, streamed over ssh by build.sh.
# The guest has no python3 or jq, so every check is plain shell.
set -euo pipefail

RUNTIME="${KNIX_GFX_RUNTIME:-/run/user/0}"
LOG="${KNIX_GFX_LOG:-/tmp/knix-graphics-verify.log}"
SESSION="/usr/share/wayland-sessions/knix.desktop"

fail() {
  echo "GRAPHICS VERIFY FAILED: $*" >&2
  tail -30 "$LOG" >&2 2>/dev/null || true
  exit 1
}

[ "$(id -u)" -eq 0 ] || fail "must run as root inside the guest"

echo "=== guest-owned DRM device ==="
[ -c /dev/dri/card0 ] || fail "/dev/dri/card0 is not a guest-owned DRM device"
[ -c /dev/dri/renderD128 ] || fail "the render node is missing"
[ -d /sys/module/virtio_gpu ] || fail "the virtio_gpu DRM driver is not loaded"
[ -d /sys/module/virtio_input ] || fail "the virtio_input driver is not loaded"
[ -e /sys/class/drm/card0-Virtual-1 ] || fail "the virtio-gpu connector is missing"
grep -q '^virtio_gpu' /proc/modules || fail "virtio_gpu is not in the guest kernel"
grep -q '^virtio_input' /proc/modules || fail "virtio_input is not in the guest kernel"
cat /sys/class/drm/card0-Virtual-1/modes | grep -q '1280x800' \
  || fail "no virtio-gpu mode is exposed"
ls /dev/input/by-path/*event-kbd > /dev/null 2>&1 \
  || fail "no guest virtio keyboard"
ls /dev/input/by-path/*event-mouse > /dev/null 2>&1 \
  || fail "no guest virtio pointer"
echo "DRM: card0 + renderD128, connector Virtual-1, 1280x800, virtio input present"

echo "=== guest-owned session files ==="
[ -f "$SESSION" ] || fail "missing $SESSION"
grep -q '^Exec=sway' "$SESSION" || fail "the knix session does not start sway"
grep -q '^Name=Knix' "$SESSION" || fail "the knix session lost its name"
command -v sway > /dev/null || fail "sway is missing"
command -v swaymsg > /dev/null || fail "swaymsg is missing"
[ -x /usr/bin/foot ] || fail "foot is missing"

install -d -m 0700 "$RUNTIME"
rm -f "$RUNTIME"/sway-ipc.*.sock
export XDG_RUNTIME_DIR="$RUNTIME" WLR_BACKENDS=drm
# EGL explicitly selects the DRM device here. LIBGL_ALWAYS_SOFTWARE=1 conflicts
# with that and makes Mesa segfault, so never force software rendering.
unset LIBGL_ALWAYS_SOFTWARE

echo "=== sway on the DRM device ==="
sway -d > "$LOG" 2>&1 &
SWAY_PID=$!
cleanup() {
  kill "$SWAY_PID" 2>/dev/null || true
  wait "$SWAY_PID" 2>/dev/null || true
}
trap cleanup EXIT

SOCK=""
for _ in $(seq 1 20); do
  SOCK="$(ls "$RUNTIME"/sway-ipc.*.sock 2>/dev/null | head -1 || true)"
  [ -n "$SOCK" ] && break
  kill -0 "$SWAY_PID" 2>/dev/null || break
  sleep 1
done
[ -n "$SOCK" ] || fail "sway never opened its IPC socket"
export SWAYSOCK="$SOCK"
# sway passes WAYLAND_DISPLAY to children itself; exporting it also lets any
# client launched from this shell find the compositor without guessing.
WD="$(ls -t "$RUNTIME"/wayland-? 2>/dev/null | head -1 || true)"
[ -n "$WD" ] && export WAYLAND_DISPLAY="${WD##*/}"

swaymsg -t get_version | grep -q '"human_readable"' \
  || fail "sway IPC is not answering"
swaymsg -t get_outputs > "$RUNTIME/knix-outputs.json"
grep -q '"name": "Virtual-1"' "$RUNTIME/knix-outputs.json" \
  || fail "the virtio-gpu output is missing"
grep -q '"active": true' "$RUNTIME/knix-outputs.json" \
  || fail "no DRM output is active"
grep -q '"width": 1280' "$RUNTIME/knix-outputs.json" \
  || fail "the DRM output has no real mode"
grep -q 'Allocated .* GBM buffer' "$LOG" \
  || fail "no GBM framebuffer was allocated"
grep -q 'Created GL FBO' "$LOG" \
  || fail "no GL render target was created"
echo "sway: $(swaymsg -t get_version | grep -o '"human_readable": "[^"]*"')"
echo "GBM buffers: $(grep -c 'Allocated .* GBM buffer' "$LOG")"

echo "=== a real Wayland client gets a window ==="
swaymsg exec /usr/bin/foot > /dev/null
for _ in $(seq 1 10); do
  swaymsg -t get_tree | grep -q '"app_id": "foot"' && break
  sleep 1
done
swaymsg -t get_tree | grep -q '"app_id": "foot"' \
  || fail "foot did not get a Wayland window"
grep -q 'New wlr_surface' "$LOG" || fail "no Wayland surface was committed"
echo "Wayland surfaces: $(grep -c 'New wlr_surface' "$LOG")"
echo "wayland client: foot window present"

echo "GRAPHICS VERIFY OK"
