#!/bin/bash
# Reproducible knix.img build. One artifact, one receipt, one publish surface.
#
#   platform/image/build.sh --check    # verify every declared input hash
#   platform/image/build.sh --build   # derive -> boot -> inject -> verify -> receipt
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEV="${KNIX_DEV_DIR:-$(cd "$ROOT/.." && pwd)/knix-m0}"
MANIFEST="$ROOT/platform/image/manifest.json"
MODE="${1:---check}"

sha256() { shasum -a 256 "$1" | awk '{print $1}'; }

check_inputs() {
  python3 - "$ROOT" "$MANIFEST" "$DEV" <<'PY'
import hashlib, json, pathlib, sys
root, manifest_path, dev = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
m = json.loads(manifest_path.read_text())
bad = []
for rel, expected in m["inputs"]["identity_sha256"].items():
    p = root / rel
    got = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "missing"
    if got != expected: bad.append((rel, expected, got))
for rel, expected in m["inputs"]["brand_sha256"].items():
    p = root / rel
    got = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "missing"
    if got != expected: bad.append((rel, expected, got))
seed = dev / m["inputs"]["seed"]["name"]
if not seed.is_file():
    bad.append((str(seed), "present", "missing"))
else:
    h = hashlib.sha512()
    with seed.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != m["inputs"]["seed"]["sha512"]:
        bad.append((seed.name, m["inputs"]["seed"]["sha512"], h.hexdigest()))
    # A writable seed can be silently rewritten by a boot. The pristine image
    # must stay read-only so every build starts from the pinned bytes.
    if seed.stat().st_mode & 0o222:
        bad.append((seed.name, "read-only", oct(seed.stat().st_mode & 0o777)))
if bad:
    for b in bad: print("INPUT DRIFT:", b, file=sys.stderr)
    sys.exit(1)
print("INPUTS OK: seed sha512, 6 identity files, 4 brand assets")
PY
}

if [ "$MODE" = "--check" ]; then
  check_inputs
  python3 -m json.tool "$MANIFEST" > /dev/null
  echo "CHECK OK: manifest inputs are reproducible"
  exit 0
fi

if [ "$MODE" != "--build" ]; then
  echo "usage: build.sh [--check|--build]" >&2
  exit 2
fi

check_inputs
command -v qemu-img >/dev/null || { echo "qemu-img missing" >&2; exit 1; }

wait_ssh() {
  for _ in $(seq 1 80); do
    if $SSH knix@127.0.0.1 true 2>/dev/null; then return 0; fi
    sleep 3
  done
  return 1
}

stop_guest() {
  local pid
  pid="$(cat "$DEV/qemu.pid")"
  $SSH knix@127.0.0.1 'sudo sync' || true
  sleep 2
  kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 30); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 1
  done
  echo "guest did not stop cleanly; refusing to write a receipt" >&2
  return 1
}

echo "=== derive ==="
qemu-img convert -O qcow2 "$DEV/debian-13-generic-arm64.qcow2" "$DEV/knix.img"

echo "=== boot ==="
"$ROOT/platform/backends/vm-virt/boot-dev.sh" > "$DEV/boot.log" 2>&1 &
LAUNCH_PID=$!
cleanup() {
  if [ -f "$DEV/qemu.pid" ]; then
    kill -TERM "$(cat "$DEV/qemu.pid")" 2>/dev/null || true
  fi
  wait "$LAUNCH_PID" 2>/dev/null || true
}
trap cleanup EXIT

SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -i $DEV/id_knix_m0 -p 2222"
wait_ssh || { echo "guest never became reachable" >&2; exit 1; }

echo "=== inject identity ==="
"$ROOT/platform/image/inject-identity.sh"

echo "=== verify ==="
$SSH knix@127.0.0.1 'set -e; . /etc/os-release; [ "$ID" = knix ]; [ "$NAME" = Knix ]; [ "$(hostname)" = knix ]; test -f /etc/knix-release; test "$(ls -1 /usr/share/knix/brand | wc -l)" -eq 4'
echo "IDENTITY OK"

$SSH knix@127.0.0.1 'cd /usr/share/knix/brand && sha256sum *' > "$DEV/guest-brand.sha256"
python3 - "$MANIFEST" "$DEV/guest-brand.sha256" <<'PY'
import json, pathlib, sys
m = json.loads(pathlib.Path(sys.argv[1]).read_text())
expected = {pathlib.Path(k).name: v for k, v in m["inputs"]["brand_sha256"].items()}
got = {}
for line in pathlib.Path(sys.argv[2]).read_text().splitlines():
    digest, name = line.split(None, 1)
    got[name.strip().lstrip("*")] = digest
if got != expected:
    print("BRAND DRIFT:", got, "!=", expected, file=sys.stderr)
    sys.exit(1)
print("BRAND BYTES OK: 4 assets byte-identical")
PY

echo "=== quiesce ==="
# Injecting files only dirties the guest page cache. Without an explicit sync,
# killing QEMU can lose those writes and produce an image without the identity.
stop_guest

echo "=== reboot verification ==="
"$ROOT/platform/backends/vm-virt/boot-dev.sh" > "$DEV/boot-reboot.log" 2>&1 &
LAUNCH_PID=$!
wait_ssh || { echo "guest did not come back after reboot" >&2; exit 1; }
$SSH knix@127.0.0.1 'set -e
  . /etc/os-release
  [ "$ID" = knix ]
  [ "$NAME" = Knix ]
  [ "$(hostname)" = knix ]
  [ ! -L /etc/os-release ]
  test -f /etc/knix-release
  test "$(ls -1 /usr/share/knix/brand | wc -l)" -eq 4'
echo "REBOOT PERSISTENCE OK"
stop_guest

echo "=== receipt ==="
IMG_SHA="$(sha256 "$DEV/knix.img")"
python3 - "$DEV" "$IMG_SHA" "$ROOT" <<'PY'
import json, pathlib, sys, datetime
dev, img_sha, root = pathlib.Path(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3])
identity = json.loads((root / "platform/identity/identity.json").read_text())
receipt = {
    "product": "knix",
    "version": identity["version"],
    "stage": identity["stage"],
    "artifact": "knix.img",
    "format": "qcow2",
    "sha256": img_sha,
    "bytes": (dev / "knix.img").stat().st_size,
    "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "identity": "platform/identity/",
    "publish_surface": identity["publish_surface"],
}
(dev / "BUILD-RECEIPT.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
PY
"$ROOT/platform/image/sign.sh"
echo "BUILD OK: knix.img + BUILD-RECEIPT.json"
