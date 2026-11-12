#!/bin/bash
# Guest-owned login and authentication acceptance. Run as root inside knix.
# It reads only guest state: sshd's effective config, shadow status, logind,
# systemd units and one real authentication attempt against the guest's sshd.
# No host state, no private key and no second login surface is introduced.
set -euo pipefail
fail() { echo "LOGIN VERIFY FAILED: $*" >&2; exit 1; }

SSHD_T="$(sshd -T 2>/dev/null)" || fail "sshd -T is unavailable"
# sshd -T lowercases every directive. Assert whole lines, not substrings, so a
# renamed value or an extra allow-rule cannot slip through.
for line in \
  'permitrootlogin no' \
  'passwordauthentication no' \
  'kbdinteractiveauthentication no' \
  'pubkeyauthentication yes' \
  'permitemptypasswords no' \
  'authenticationmethods publickey' \
  'usepam yes'; do
  grep -qx -- "$line" <<<"$SSHD_T" || fail "sshd policy drift: $line"
done
echo "sshd: root, password, keyboard-interactive and empty passwords refused; public keys only"

# No interactive account may carry a usable password. Print status, never hashes.
awk -F: '
  /^(root|knix):/ {
    if ($2 == "" || $2 !~ /^[!*]/) { print "unlocked account: " $1 > "/dev/stderr"; bad = 1 }
  }
  END { exit (bad ? 1 : 0) }
' /etc/shadow || fail "an interactive account has a usable password"
getent passwd knix | grep -q '^knix:x:1000:1000:' \
  || fail "knix is not the uid 1000 interactive account"
AUTH_CONF=/etc/ssh/sshd_config.d/10-knix-auth.conf
grep -q '^PermitRootLogin no$' "$AUTH_CONF" || fail "guest sshd policy lost PermitRootLogin"
grep -q '^PasswordAuthentication no$' "$AUTH_CONF" || fail "guest sshd policy lost PasswordAuthentication"
grep -q '^AuthenticationMethods publickey$' "$AUTH_CONF" \
  || fail "guest sshd policy lost AuthenticationMethods"
echo "accounts: knix is the only interactive account, root and knix passwords locked"

# logind: an explicit guest-owned idle policy and no getty autologin override.
LOGIN_CONF=/etc/systemd/logind.conf.d/10-knix-login.conf
grep -q '^IdleAction=ignore$' "$LOGIN_CONF" || fail "guest logind idle policy missing"
systemd-analyze cat-config systemd/logind.conf | grep -q '^IdleAction=ignore$' \
  || fail "IdleAction=ignore is not in effect"
test ! -d /etc/systemd/system/getty@tty1.service.d \
  || fail "a getty autologin override would create a second login surface"
echo "logind: IdleAction=ignore in effect, no getty autologin override"

# Autologin is proven by the guest's own target, not by a host-supplied login.
systemctl is-enabled knix-session.service | grep -qx enabled \
  || fail "session unit is not enabled by multi-user.target"
systemctl is-active knix-session.service | grep -qx active \
  || fail "session unit is not active"
pgrep -u knix -x sway >/dev/null || fail "sway is not running as knix"
loginctl show-user knix | grep -qx 'State=active' \
  || fail "knix has no active login session state"
if loginctl list-sessions --no-legend | awk '{print $2}' | grep -qx '^0$'; then
  fail "root holds a login session"
fi
loginctl show-seat seat0 | grep -qx 'CanGraphical=yes' || fail "seat0 is not graphical"
echo "autologin: knix-session.service enabled+active, sway owned by knix, no root session"

echo "LOGIN POLICY OK: guest sshd, accounts and logind policy are asserted"

# A real authentication attempt must be refused. No private key is shipped for
# this test; the guest owns its credentials.
SSHOPT=(-o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
        -o ConnectTimeout=5 -o LogLevel=ERROR)
(exec 3<>/dev/tcp/127.0.0.1/22) 2>/dev/null || fail "guest sshd is not listening"
if ssh "${SSHOPT[@]}" -o PreferredAuthentications=password -o PubkeyAuthentication=no \
      knix@127.0.0.1 true >/dev/null 2>&1; then
  fail "password authentication was accepted"
fi
if ssh "${SSHOPT[@]}" -o PreferredAuthentications=none \
      root@127.0.0.1 true >/dev/null 2>&1; then
  fail "root login was accepted"
fi
echo "rejections: password login and root login both refused by the guest sshd"

echo "LOGIN VERIFY OK: guest-owned authentication, autologin and idle policy proven"
