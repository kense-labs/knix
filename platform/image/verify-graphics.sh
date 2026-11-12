#!/bin/bash
# Verify that knix owns a real DRM device, a guest seat and a non-root Wayland
# session with real libinput devices. Executed INSIDE the arm64 guest as root,
# streamed over ssh by build.sh. The guest has no python3 or jq, so every
# check is plain shell.
set -euo pipefail

LOG="${KNIX_SESSION_LOG:-/run/knix-session.log}"
SESSION_DESKTOP="/usr/share/wayland-sessions/knix.desktop"
UDEV_RULE="/etc/udev/rules.d/71-knix-virtio-input.rules"
UNIT="/etc/systemd/system/knix-session.service"
UNIT_NAME="knix-session.service"
RUNTIME="/run/user/1000"

fail() {
  echo "GRAPHICS VERIFY FAILED: $*" >&2
  tail -30 "$LOG" >&2 2>/dev/null || true
  journalctl -u "$UNIT_NAME" --no-pager -n 25 >&2 2>/dev/null || true
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
KBD="$(readlink -f /dev/input/by-path/*event-kbd 2>/dev/null | head -1 || true)"
MOUSE="$(readlink -f /dev/input/by-path/*event-mouse 2>/dev/null | head -1 || true)"
[ -c "$KBD" ] || fail "no guest virtio keyboard"
[ -c "$MOUSE" ] || fail "no guest virtio pointer"
echo "DRM: card0 + renderD128, connector Virtual-1, 1280x800, virtio input present"

echo "=== guest-owned seat ==="
systemctl is-active seatd.service 2>/dev/null | grep -qx active \
  || fail "the guest seat manager is not running"
for grp in video input render; do
  id -nG knix | tr ' ' '\n' | grep -qx "$grp" \
    || fail "knix is missing the $grp group needed for seatd/libinput fds"
done
[ -f "$UDEV_RULE" ] || fail "missing guest-owned virtio input udev rule"
[ -f "$UNIT" ] || fail "missing guest-owned session unit"
# The unit is deliberately not enabled: verification starts it on demand, and
# ConditionPathExists keeps GPU=none boots on the serial/ssh fallback path.
! systemctl is-enabled "$UNIT_NAME" >/dev/null 2>&1 \
  || fail "the session unit must stay disabled until verification"
for dev in "$KBD" "$MOUSE"; do
  udevadm info --query=all --name="$dev" | grep -q '^E: TAGS=.*:seat:' \
    || fail "$dev has no seat tag"
  udevadm info --query=all --name="$dev" | grep -q '^E: TAGS=.*:uaccess:' \
    || fail "$dev has no uaccess tag"
done
echo "seat: seatd active, knix in video/input/render, virtio nodes tagged seat+uaccess"

echo "=== guest-owned session files ==="
[ -f "$SESSION_DESKTOP" ] || fail "missing $SESSION_DESKTOP"
grep -q '^Exec=sway' "$SESSION_DESKTOP" || fail "the knix session does not start sway"
grep -q '^Name=Knix' "$SESSION_DESKTOP" || fail "the knix session lost its name"
command -v sway > /dev/null || fail "sway is missing"
command -v swaymsg > /dev/null || fail "swaymsg is missing"
[ -x /usr/bin/foot ] || fail "foot is missing"

echo "=== knix owns the running compositor ==="
systemctl reset-failed "$UNIT_NAME" 2>/dev/null || true
systemctl start "$UNIT_NAME" || fail "the knix session unit did not start"
cleanup() {
  systemctl stop "$UNIT_NAME" >/dev/null 2>&1 || true
  systemctl reset-failed "$UNIT_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

SOCK=""
for _ in $(seq 1 25); do
  SOCK="$(ls "$RUNTIME"/sway-ipc.*.sock 2>/dev/null | head -1 || true)"
  [ -n "$SOCK" ] && break
  systemctl is-active "$UNIT_NAME" >/dev/null 2>&1 || break
  sleep 1
done
[ -n "$SOCK" ] || fail "sway never opened its IPC socket"
export SWAYSOCK="$SOCK"
SWAY_USER="$(ps -o user= -C sway | head -1 | tr -d ' ')"
[ "$SWAY_USER" = knix ] || fail "sway is running as $SWAY_USER, not knix"
systemctl is-active "$UNIT_NAME" | grep -qx active \
  || fail "the session unit is no longer active"
# sway passes WAYLAND_DISPLAY to children itself; exporting it also lets any
# client launched from this shell find the compositor without guessing.
WD="$(ls -t "$RUNTIME"/wayland-? 2>/dev/null | head -1 || true)"
[ -n "$WD" ] && export WAYLAND_DISPLAY="${WD##*/}"
echo "compositor: sway as knix via $UNIT_NAME (seatd)"

echo "=== real libinput devices on seat0 ==="
swaymsg -t get_inputs > "$RUNTIME/knix-inputs.json"
grep -q '"identifier"' "$RUNTIME/knix-inputs.json" \
  || fail "swaymsg -t get_inputs is empty"
grep -q 'QEMU Virtio Keyboard' "$RUNTIME/knix-inputs.json" \
  || fail "the virtio keyboard was not claimed by libinput"
grep -q 'QEMU Virtio Tablet' "$RUNTIME/knix-inputs.json" \
  || fail "the virtio pointer was not claimed by libinput"
echo "libinput devices: $(grep -c '"identifier"' "$RUNTIME/knix-inputs.json")"

echo "=== sway on the DRM device ==="
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

echo "SESSION VERIFY OK: knix/seatd, $(grep -c '"identifier"' "$RUNTIME/knix-inputs.json") libinput devices"
echo "GRAPHICS VERIFY OK"
