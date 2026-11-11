#!/bin/bash
# Build the native knix rootfs. Executed INSIDE an aarch64 Linux guest, never
# on the build host. The seed distribution is only a package source.
#
#   platform/image/build-rootfs.sh --check     # toolchain + package set only
#   platform/image/build-rootfs.sh --apply     # build rootfs and tarball
set -euo pipefail

ROOTFS="${KNIX_ROOTFS:-/mnt/knix-rootfs}"
IDENTITY_DIR="${KNIX_IDENTITY_DIR:-/etc/knix}"
# Plain HTTP keeps apt's downloads reliable behind QEMU's slirp user networking;
# TLS connections there can stall on a single small package. The mirror is only
# a package source and is never a product capability.
MIRROR="${KNIX_MIRROR:-http://deb.debian.org/debian}"
VARIANT="${KNIX_VARIANT:-minbase}"
TARBALL="${KNIX_TARBALL:-/tmp/knix-rootfs.tar.zst}"
MODE="${1:-${KNIX_MODE:---check}}"
SETS="${KNIX_SETS:-base,firmware,graphics,desktop,audio}"

if [ ! -f "$IDENTITY_DIR/packages.json" ]; then
  echo "identity documents missing at $IDENTITY_DIR; run inject-identity.sh first" >&2
  exit 1
fi

read_packages() {
  python3 - "$IDENTITY_DIR/packages.json" "$1" "$SETS" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
kind, wanted = sys.argv[2], sys.argv[3].split(",")
if kind == "include":
    print(",".join(p for name, group in d["sets"].items() if name in wanted for p in group))
else:
    print(",".join(d.get(kind, [])))
PY
}

INCLUDE="$(read_packages include)"
EXCLUDE="$(read_packages excludes)"
SUITE="$(python3 -c 'import json;print(json.load(open("'"$IDENTITY_DIR"'/packages.json"))["suite"])')"
echo "suite=$SUITE"
echo "variant=$VARIANT"
echo "mirror=$MIRROR"
echo "include=$INCLUDE"
echo "exclude=$EXCLUDE"

# mmdebstrap has no --exclude option. apt spellings with a trailing '-' mean
# "remove instead of install", so exclusions are appended to --include.
INCLUDE_SPEC="$INCLUDE"
IFS=',' read -ra excluded_pkgs <<< "$EXCLUDE"
for pkg in "${excluded_pkgs[@]}"; do
  [ -n "$pkg" ] && INCLUDE_SPEC+=",$pkg-"
done

if [ "$MODE" = "--check" ]; then
  command -v mmdebstrap >/dev/null || echo "mmdebstrap: not installed (--apply will install it)"
  command -v zstd >/dev/null || echo "zstd: not installed (--apply will install it)"
  echo "package set parsed; guest is the build environment"
  exit 0
fi

if [ "$MODE" != "--apply" ]; then
  echo "usage: build-rootfs.sh [--check|--apply]" >&2
  exit 2
fi

if ! command -v mmdebstrap >/dev/null || ! command -v zstd >/dev/null; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mmdebstrap zstd
fi

ARCH="$(python3 -c 'import json;print(json.load(open("'"$IDENTITY_DIR"'/packages.json"))["arch"])')"

# Optional local .deb cache, used only to avoid re-downloading on a dev host.
# It is never a build input: the package set itself is owned by packages.json.
HOOK_ARGS=()
if [ -n "${KNIX_DEB_CACHE:-}" ] && [ -d "$KNIX_DEB_CACHE" ]; then
  n="$(find "$KNIX_DEB_CACHE" -maxdepth 1 -name '*.deb' | wc -l)"
  echo "reusing $n cached debs from $KNIX_DEB_CACHE"
  HOOK_ARGS+=(--setup-hook="mkdir -p \"\$1/var/cache/apt/archives\" && cp -n \"$KNIX_DEB_CACHE\"/*.deb \"\$1/var/cache/apt/archives/\"")
fi

sudo install -d -m 0755 "$ROOTFS"
sudo mmdebstrap \
  "${HOOK_ARGS[@]}" \
  --variant="$VARIANT" \
  --architectures="$ARCH" \
  --include="$INCLUDE_SPEC" \
  "$SUITE" "$ROOTFS" "$MIRROR"

sudo install -d -m 0755 "$ROOTFS/etc/knix" "$ROOTFS/usr/share/knix/brand" \
                      "$ROOTFS/usr/share/wayland-sessions"
sudo cp "$IDENTITY_DIR"/* "$ROOTFS/etc/knix/"
sudo rm -f "$ROOTFS/etc/os-release"
sudo install -m 0644 "$IDENTITY_DIR/os-release" "$ROOTFS/etc/os-release"
sudo install -m 0644 "$IDENTITY_DIR/knix-release" "$ROOTFS/etc/knix-release"
printf 'knix\n' | sudo tee "$ROOTFS/etc/hostname" >/dev/null
sudo cp /usr/share/knix/brand/* "$ROOTFS/usr/share/knix/brand/"

sudo tee "$ROOTFS/usr/share/wayland-sessions/knix.desktop" >/dev/null <<'DESKTOP'
[Desktop Entry]
Name=Knix
Comment=Knix Wayland session
Exec=sway
Type=Application
DesktopNames=Knix
DESKTOP

printf '%s\n' \
  "bin/bash executable: $(sudo test -x "$ROOTFS/bin/bash" && echo yes || echo no)" \
  "os-release ID:       $(sudo sed -n 's/^ID=//p' "$ROOTFS/etc/os-release")" \
  "hostname:            $(sudo cat "$ROOTFS/etc/hostname")" \
  "sway present:        $(sudo test -x "$ROOTFS/usr/bin/sway" && echo yes || echo no)" \
  "brand assets:        $(sudo ls -1 "$ROOTFS/usr/share/knix/brand" | wc -l)"

# The reused .deb cache is a build aid, not part of a knix rootfs.
sudo rm -f "$ROOTFS/var/cache/apt/archives"/*.deb
sudo tar --zstd -cf "$TARBALL" -C "$ROOTFS" .
sudo sync
echo "rootfs tarball: $TARBALL"
ls -lh "$TARBALL"
sha256sum "$TARBALL"

# Record every input that produced this tarball. build.sh copies the summary
# back to the host so the release receipt can be audited without mounting the
# qcow2 build disk.
SUMMARY="${KNIX_ROOTFS_SUMMARY:-$(dirname "$TARBALL")/rootfs-build.json}"
sudo python3 - "$SUMMARY" "$TARBALL" "$IDENTITY_DIR/packages.json" \
                 "$SUITE" "$VARIANT" "$ARCH" "$INCLUDE_SPEC" <<'PY'
import hashlib, json, pathlib, sys
out, tarball, pkgset, suite, variant, arch, include = sys.argv[1:]
def digest(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
data = {
    "schema": 1,
    "generator": "mmdebstrap",
    "suite": suite,
    "variant": variant,
    "arch": arch,
    "artifact": pathlib.Path(tarball).name,
    "tarball_sha256": digest(tarball),
    "tarball_bytes": pathlib.Path(tarball).stat().st_size,
    "package_set_sha256": digest(pkgset),
    "package_count": len([p for p in include.split(",") if p and not p.endswith("-")]),
}
pathlib.Path(out).write_text(json.dumps(data, indent=2) + "\n")
print(json.dumps(data, indent=2))
PY
sudo sync
