#!/bin/bash
# Verify that a booted knix image already has its own non-root Wayland session,
# a guest seat and real libinput devices on a guest-owned DRM device. Executed
# INSIDE the arm64 guest as root, streamed over ssh by build.sh. This script
# never enables or launches the session; it only proves the guest did so at boot.
# The guest has no python3 or jq, so every check is plain shell.
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
# session.json declares autostart: the guest's own multi-user.target enables the
# unit, so a booted knix image already has a compositor before this script runs.
ENABLE_LINK="/etc/systemd/system/multi-user.target.wants/knix-session.service"
[ -L "$ENABLE_LINK" ] || fail "missing guest-owned autostart symlink"
[ "$(readlink "$ENABLE_LINK")" = "../knix-session.service" ] \
  || fail "the autostart symlink drifted"
systemctl is-enabled "$UNIT_NAME" 2>/dev/null | grep -qx enabled \
  || fail "the session unit is not enabled by the guest's own target"
systemctl is-active "$UNIT_NAME" 2>/dev/null | grep -qx active \
  || fail "the session did not start by itself at boot"
BOOT_USER="$(ps -o user= -C sway | head -1 | tr -d ' ')"
[ "$BOOT_USER" = knix ] \
  || fail "the boot-time compositor is $BOOT_USER, not knix"
echo "AUTOSTART VERIFY OK: enabled by multi-user.target, active as knix at boot"
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
# The unit already started at boot. Restart it so verification owns a clean log
# and a known compositor state, then leave the session running when we are done.
rm -f "$RUNTIME"/sway-ipc.*.sock "$RUNTIME"/wayland-? 2>/dev/null || true
systemctl reset-failed "$UNIT_NAME" 2>/dev/null || true
systemctl restart "$UNIT_NAME" || fail "the knix session unit did not restart"
cleanup() {
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

echo "=== guest-owned crash recovery ==="
# Restart=on-failure was declared but never proven. Kill the compositor and
# require the guest's own systemd -- not this script -- to bring back a complete
# knix-owned session before build.sh is allowed to write the receipt.
grep -q '^Restart=on-failure$' "$UNIT" \
  || fail "the session unit has no guest-owned restart policy"
grep -q '^RestartSec=2$' "$UNIT" \
  || fail "the session unit lost its 2s restart backoff"
grep -q '^StartLimitBurst=5$' "$UNIT" \
  || fail "the session unit has no start-rate burst limit"
grep -q '^StartLimitIntervalSec=30$' "$UNIT" \
  || fail "the session unit has no start-rate interval"
OLD_PID="$(systemctl show -p MainPID --value "$UNIT_NAME")"
[ -n "$OLD_PID" ] && [ "$OLD_PID" != 0 ] \
  || fail "the session has no main process to kill"
systemctl kill --signal=SIGKILL "$UNIT_NAME" \
  || fail "could not signal the compositor"
sleep 1
# ExecStartPre truncates the sway log on every start, so the GBM/GL/surface
# markers checked below can only come from the restarted process.
rm -f "$RUNTIME"/sway-ipc.*.sock "$RUNTIME"/wayland-? 2>/dev/null || true
NEW_PID=""
for _ in $(seq 1 30); do
  STATE="$(systemctl is-active "$UNIT_NAME" 2>/dev/null || true)"
  PID="$(systemctl show -p MainPID --value "$UNIT_NAME" 2>/dev/null || true)"
  if [ "$STATE" = active ] && [ -n "$PID" ] && [ "$PID" != 0 ] \
     && [ "$PID" != "$OLD_PID" ]; then
    NEW_PID="$PID"
    break
  fi
  sleep 1
done
[ -n "$NEW_PID" ] \
  || fail "guest systemd did not restart sway after SIGKILL (old pid $OLD_PID)"
NEW_SOCK=""
for _ in $(seq 1 25); do
  NEW_SOCK="$(ls "$RUNTIME"/sway-ipc.*.sock 2>/dev/null | head -1 || true)"
  [ -n "$NEW_SOCK" ] && break
  sleep 1
done
[ -n "$NEW_SOCK" ] || fail "the restarted sway never opened its IPC socket"
export SWAYSOCK="$NEW_SOCK"
WD="$(ls -t "$RUNTIME"/wayland-? 2>/dev/null | head -1 || true)"
[ -n "$WD" ] && export WAYLAND_DISPLAY="${WD##*/}"
SWAY_USER="$(ps -o user= -C sway | head -1 | tr -d ' ')"
[ "$SWAY_USER" = knix ] \
  || fail "the restarted compositor is $SWAY_USER, not knix"
systemctl is-active "$UNIT_NAME" | grep -qx active \
  || fail "the restarted session unit is not active"
swaymsg -t get_inputs > "$RUNTIME/knix-inputs.json"
grep -q '"identifier"' "$RUNTIME/knix-inputs.json" \
  || fail "the restarted session lost its libinput devices"
grep -q 'QEMU Virtio Keyboard' "$RUNTIME/knix-inputs.json" \
  || fail "the restarted session lost the virtio keyboard"
grep -q 'QEMU Virtio Tablet' "$RUNTIME/knix-inputs.json" \
  || fail "the restarted session lost the virtio tablet"
swaymsg -t get_outputs > "$RUNTIME/knix-outputs.json"
grep -q '"name": "Virtual-1"' "$RUNTIME/knix-outputs.json" \
  || fail "the restarted session has no DRM output"
grep -q '"active": true' "$RUNTIME/knix-outputs.json" \
  || fail "the restarted session has no active DRM output"
for _ in $(seq 1 10); do
  if grep -q 'Allocated .* GBM buffer' "$LOG" \
     && grep -q 'Created GL FBO' "$LOG"; then
    break
  fi
  sleep 1
done
grep -q 'Allocated .* GBM buffer' "$LOG" \
  || fail "the restarted sway allocated no GBM framebuffer"
grep -q 'Created GL FBO' "$LOG" \
  || fail "the restarted sway created no GL render target"
swaymsg exec /usr/bin/foot > /dev/null
for _ in $(seq 1 10); do
  swaymsg -t get_tree | grep -q '"app_id": "foot"' && break
  sleep 1
done
swaymsg -t get_tree | grep -q '"app_id": "foot"' \
  || fail "foot did not get a Wayland window after the restart"
grep -q 'New wlr_surface' "$LOG" \
  || fail "no Wayland surface was committed after the restart"
echo "RESILIENCE VERIFY OK: systemd restarted sway $OLD_PID -> $NEW_PID as knix with DRM, libinput and a Wayland client"

echo "=== guest-owned crash-loop limit ==="
# P9 proved ONE restart. The declared start limit has never been exercised: a
# broken compositor must be stopped by the guest's own systemd, not restarted
# forever. The exact restart count lives in systemd's 30s sliding window (it is
# 4 or 5 depending on earlier restarts); the durable evidence is the journal's
# "Start request repeated too quickly." line, not Result=start-limit-hit.
LIMIT_ROUNDS=0
LIMIT_FAILED=0
LIMIT_STATE=""
for _ in $(seq 1 8); do
  READY=0
  for _ in $(seq 1 15); do
    LIMIT_STATE="$(systemctl show -p ActiveState --value "$UNIT_NAME" 2>/dev/null || true)"
    LIMIT_PID="$(systemctl show -p MainPID --value "$UNIT_NAME" 2>/dev/null || true)"
    if [ "$LIMIT_STATE" = active ] && [ -n "$LIMIT_PID" ] && [ "$LIMIT_PID" != 0 ]; then
      READY=1
      break
    fi
    [ "$LIMIT_STATE" = failed ] && break
    sleep 1
  done
  if [ "$LIMIT_STATE" = failed ]; then
    LIMIT_FAILED=1
    break
  fi
  [ "$READY" = 1 ] || fail "the compositor never became killable during the crash loop"
  systemctl kill --signal=SIGKILL "$UNIT_NAME" >/dev/null 2>&1 \
    || fail "could not signal the compositor during the crash loop"
  LIMIT_ROUNDS=$((LIMIT_ROUNDS + 1))
  sleep 4
done
[ "$LIMIT_FAILED" = 1 ] \
  || fail "guest systemd kept restarting sway after $LIMIT_ROUNDS SIGKILLs"
RESTARTS="$(systemctl show -p NRestarts --value "$UNIT_NAME")"
[ "$RESTARTS" -ge 3 ] && [ "$RESTARTS" -le 8 ] \
  || fail "unexpected restart count before the start limit: $RESTARTS"
[ "$(systemctl show -p MainPID --value "$UNIT_NAME")" = 0 ] \
  || fail "the failed session still has a main process"
SWAY_PROCS="$(pgrep -c sway || true)"
[ -z "$SWAY_PROCS" ] || [ "$SWAY_PROCS" = 0 ] \
  || fail "$SWAY_PROCS sway processes survived the crash loop"
journalctl -u "$UNIT_NAME" --no-pager -n 80 \
  > "$RUNTIME/knix-limit-journal.txt" 2>/dev/null || true
grep -q 'Start request repeated too quickly' "$RUNTIME/knix-limit-journal.txt" \
  || fail "guest systemd never reported its start-rate limit"

# A failed compositor is a degraded, still-manageable guest: exactly one failed
# unit (the session itself), zero sway processes, ssh and the serial console.
FAILED_UNITS="$(systemctl --failed --no-legend | wc -l)"
[ "$FAILED_UNITS" -eq 1 ] \
  || fail "$FAILED_UNITS failed units during the crash-loop fallback (expected exactly the session unit)"
systemctl --failed --no-legend | grep -q "$UNIT_NAME" \
  || fail "the failed unit is not the guest-owned session unit"
systemctl is-active ssh.service 2>/dev/null | grep -qx active \
  || fail "ssh is not available during the crash-loop fallback"
[ -c /dev/ttyAMA0 ] \
  || fail "the serial console is not available during the crash-loop fallback"

# Cleaning up the test is NOT the recovery proof: P9's SIGKILL segment above is.
# reset-failed clears the start-rate counter; removing stale IPC sockets first
# is essential or the checks below connect to a dead compositor's socket.
systemctl reset-failed "$UNIT_NAME" >/dev/null 2>&1 || true
rm -f "$RUNTIME"/sway-ipc.*.sock "$RUNTIME"/wayland-? 2>/dev/null || true
systemctl restart "$UNIT_NAME" \
  || fail "guest systemd did not recover after the crash loop"
LIMIT_SOCK=""
for _ in $(seq 1 25); do
  LIMIT_SOCK="$(ls -t "$RUNTIME"/sway-ipc.*.sock 2>/dev/null | head -1 || true)"
  [ -n "$LIMIT_SOCK" ] && break
  sleep 1
done
[ -n "$LIMIT_SOCK" ] || fail "the recovered sway never opened its IPC socket"
export SWAYSOCK="$LIMIT_SOCK"
WD="$(ls -t "$RUNTIME"/wayland-? 2>/dev/null | head -1 || true)"
[ -n "$WD" ] && export WAYLAND_DISPLAY="${WD##*/}"
SWAY_USER="$(ps -o user= -C sway | head -1 | tr -d ' ')"
[ "$SWAY_USER" = knix ] \
  || fail "the recovered compositor is $SWAY_USER, not knix"
systemctl is-active "$UNIT_NAME" | grep -qx active \
  || fail "the recovered session unit is not active"
swaymsg -t get_inputs > "$RUNTIME/knix-inputs.json"
grep -q '"identifier"' "$RUNTIME/knix-inputs.json" \
  || fail "the recovered session lost its libinput devices"
grep -q 'QEMU Virtio Keyboard' "$RUNTIME/knix-inputs.json" \
  || fail "the recovered session lost the virtio keyboard"
grep -q 'QEMU Virtio Tablet' "$RUNTIME/knix-inputs.json" \
  || fail "the recovered session lost the virtio tablet"
swaymsg -t get_outputs > "$RUNTIME/knix-outputs.json"
grep -q '"name": "Virtual-1"' "$RUNTIME/knix-outputs.json" \
  || fail "the recovered session has no DRM output"
grep -q '"active": true' "$RUNTIME/knix-outputs.json" \
  || fail "the recovered session has no active DRM output"
for _ in $(seq 1 10); do
  if grep -q 'Allocated .* GBM buffer' "$LOG" \
     && grep -q 'Created GL FBO' "$LOG"; then
    break
  fi
  sleep 1
done
grep -q 'Allocated .* GBM buffer' "$LOG" \
  || fail "the recovered sway allocated no GBM framebuffer"
grep -q 'Created GL FBO' "$LOG" \
  || fail "the recovered sway created no GL render target"
swaymsg exec /usr/bin/foot > /dev/null
for _ in $(seq 1 10); do
  swaymsg -t get_tree | grep -q '"app_id": "foot"' && break
  sleep 1
done
swaymsg -t get_tree | grep -q '"app_id": "foot"' \
  || fail "foot did not get a Wayland window after the crash loop"
grep -q 'New wlr_surface' "$LOG" \
  || fail "no Wayland surface was committed after the crash loop"
echo "LIMIT VERIFY OK: guest systemd stopped the crash loop after $RESTARTS restarts ($LIMIT_ROUNDS kills, 1 failed unit, ssh + serial intact) and recovered sway as knix with DRM, libinput and a Wayland client"

echo "=== guest-owned framebuffer pattern ==="
# The compositor proves it painted by choosing a colour from guest entropy and
# putting it on the scanout. The host only reads that colour from this log and
# reads the standard virtio-gpu framebuffer through QMP; it draws nothing.
# Close the verification terminals first so no window covers the sample grid.
swaymsg '[app_id="foot"] kill' >/dev/null 2>&1 || true
sleep 1
RAW="$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
# Lift every channel into 0x40..0xff: a near-black colour would be
# indistinguishable from an uninitialised framebuffer on the host side.
PATTERN_R=$((0x${RAW:0:2} % 0xC0 + 0x40))
PATTERN_G=$((0x${RAW:2:2} % 0xC0 + 0x40))
PATTERN_B=$((0x${RAW:4:2} % 0xC0 + 0x40))
PATTERN_HEX="$(printf '%02x%02x%02x' "$PATTERN_R" "$PATTERN_G" "$PATTERN_B")"
swaymsg "output * background #$PATTERN_HEX solid_color" \
  | grep -q '"success": true' \
  || fail "sway refused to paint the guest-chosen framebuffer pattern"
sleep 2
echo "FRAMEBUFFER PATTERN: $PATTERN_HEX"
echo "FRAMEBUFFER PATTERN PAINTED: the restarted sway painted #$PATTERN_HEX"

echo "SESSION VERIFY OK: knix/seatd, $(grep -c '"identifier"' "$RUNTIME/knix-inputs.json") libinput devices"
echo "GRAPHICS VERIFY OK"
