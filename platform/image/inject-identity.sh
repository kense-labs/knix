#!/bin/bash
# Install the knix identity into the running dev-preview guest.
# Identity bytes come only from platform/identity/ and docs/brand/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEV="${KNIX_DEV_DIR:-$(cd "$ROOT/.." && pwd)/knix-m0}"
PORT="${SSH_PORT:-2222}"
KEY="${SSH_KEY:-$DEV/id_knix_m0}"
TARGET="${SSH_TARGET:-knix@127.0.0.1}"

SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 -i $KEY -p $PORT"
SCP="scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $KEY -P $PORT"

$SSH "$TARGET" true || { echo "guest is not reachable on port $PORT" >&2; exit 1; }

# Identity documents. tar over ssh keeps the bytes verbatim and avoids
# rewriting files as root through a stream.
COPYFILE_DISABLE=1 tar -cf - -C "$ROOT/platform/identity" \
    os-release knix-release identity.json packages.json session.json brand.manifest.json \
  | $SSH "$TARGET" 'sudo install -d /etc/knix /usr/share/knix/brand && sudo tar -xf - -C /etc/knix'

COPYFILE_DISABLE=1 tar -cf - -C "$ROOT/docs/brand" \
    mark-light.png mark-dark.png icon.svg wordmark-light.svg \
  | $SSH "$TARGET" 'sudo tar -xf - -C /usr/share/knix/brand'

$SSH "$TARGET" 'sudo bash -s' <<'GUEST'
set -euo pipefail
# Debian ships /etc/os-release as a symlink; replace it with the identity-owned file.
rm -f /etc/os-release
install -m 0644 /etc/knix/os-release /etc/os-release
install -m 0644 /etc/knix/knix-release /etc/knix-release
printf 'knix\n' > /etc/hostname
hostnamectl set-hostname knix 2>/dev/null || true
printf 'Knix 0.1 (bootstrap) — dev preview\n\n' > /etc/motd
printf 'Knix 0.1 (bootstrap) \\n \\l\n\n' > /etc/issue
install -d -m 0755 /usr/share/knix
# Brand bytes are installed read-only; nothing is re-rendered.
chmod 0644 /usr/share/knix/brand/*
sync
GUEST

echo "=== identity in guest ==="
$SSH "$TARGET" '. /etc/os-release; echo "NAME=$NAME ID=$ID VERSION=$VERSION PRETTY_NAME=$PRETTY_NAME"; echo "hostname=$(hostname)"; echo "knix-release: $(tr "\n" " " < /etc/knix-release)"; echo "brand:"; ls -1 /usr/share/knix/brand; sha256sum /usr/share/knix/brand/*'
