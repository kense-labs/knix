# Release

## Surface

`knix.img` and `origin/main` are the only publish surfaces. The image is a
build artifact and is not stored in this repository; `origin/main` carries the
identity, the build recipe and the verification gate that reproduce it.

| 阶段 | 命令 | 产物 |
| --- | --- | --- |
| 输入校验 | `platform/image/build.sh --check` | 无 |
| 内核 | `platform/image/build-kernel.sh --apply`（guest） | `knix-Image` · `modules/` · `kernel-build.json` |
| 原生 rootfs | `platform/image/build-rootfs.sh --apply`（guest） | `knix-rootfs.tar.zst` · `rootfs-build.json` |
| 镜像构建 | `platform/image/build.sh --build`（host） | `$KNIX_DEV_DIR/knix.img` |
| 摘要与签名 | `platform/image/sign.sh` | `BUILD-RECEIPT.json` + `.sha256`（+ 可选 `.sig`） |
| 契约校验 | `python3 scripts/verify-knix.py` | 无 |
| 可复现基线 | `python3 scripts/verify-repro.py` | 无 |

`build.sh --build` creates a blank qcow2, assembles the native rootfs, the
self-built kernel and its matching 773 modules inside the helper guest,
cold-boots the result through its own ESP, then verifies identity, brand bytes,
module loading, failed units, the upstream-signed regulatory.db and real sway
rendering on the guest-owned DRM device (`GRAPHICS VERIFY OK`). Only then does it
write the receipt. Before assembling, it refuses any kernel/rootfs summary that
differs from the locked `build_baseline` or a `package-versions.txt` that is not
byte-identical to the committed lockfile. The seed is never the source of the
image contents.

## Verify a release

```bash
# 1. the receipt itself is intact
openssl dgst -sha256 BUILD-RECEIPT.json
cat BUILD-RECEIPT.json.sha256

# 2. the image matches the receipt
python3 - <<'PY'
import hashlib, json, pathlib
r = json.loads(pathlib.Path("BUILD-RECEIPT.json").read_text())
h = hashlib.sha256()
with pathlib.Path("knix.img").open("rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        h.update(chunk)
print("receipt:", r["sha256"])
print("image:  ", h.hexdigest())
print("MATCH" if h.hexdigest() == r["sha256"] else "MISMATCH")
PY

# 3. when a release key was used
openssl dgst -sha256 -verify knix-release.pub \
  -signature BUILD-RECEIPT.json.sig BUILD-RECEIPT.json
```

The receipt's `sha256` must equal `knix.img`. Its `kernel`, `rootfs`, `inputs`,
`boot`, `graphics` and `baseline` sections record every digest used by the
build, the locked kernel/rootfs baseline, the UEFI self-boot method and the fact
that real Wayland rendering was verified; a mismatch there means the image was
assembled from drifted inputs or skipped a verification stage. `.sha256` covers
the receipt, not the image.

`baseline.sha256` is the digest of the canonical `build_baseline` object in
`platform/image/manifest.json`. Compare it against the committed manifest to
prove which locked baseline produced this image:

```bash
python3 - <<'PY'
import hashlib, json, pathlib
m = json.loads(pathlib.Path("platform/image/manifest.json").read_text())
r = json.loads(pathlib.Path("BUILD-RECEIPT.json").read_text())
canonical = json.dumps(m["build_baseline"], sort_keys=True, separators=(",", ":"))
print("MATCH" if hashlib.sha256(canonical.encode()).hexdigest() == r["baseline"]["sha256"]
      else "BASELINE DRIFT")
PY
```
A release key is never stored in the repository; `KNIX_SIGNING_KEY` selects one
at signing time, and the matching public key is distributed out of band.

## Boot

`knix.img` is a GPT disk with a 512 MiB FAT32 EFI system partition (`/dev/vda1`)
and an ext4 root (`/dev/vda2`). The ESP carries the whole boot chain:

| Path | Role |
| --- | --- |
| `EFI/BOOT/BOOTAA64.EFI` | systemd-boot at the removable media path |
| `EFI/knix/vmlinuz-7.2.15-knix` | the kernel built from `repos/linux` |
| `EFI/knix/initrd.img-7.2.15-knix` | initramfs generated inside the arm64 guest |
| `loader/loader.conf` · `loader/entries/knix.conf` | boot entry and `root=/dev/vda2 rw console=ttyAMA0` |

`platform/backends/vm-virt/boot-native.sh` starts it through EDK2 with no
`-kernel`, no seed ISO and a fresh EFI variable store, so the boot depends on
neither a host kernel nor an EFI Boot#### entry. A `KERNEL` override on
`boot-dev.sh` remains available for debugging. The receipt records the boot
method, the ESP path and the `root=` append.

## Graphics

`boot-native.sh` exposes standard `virtio-gpu-pci`, `virtio-keyboard-pci` and
`virtio-tablet-pci` devices. The guest kernel loads its own `virtio-gpu` and
`virtio_input` modules and owns `/dev/dri/card0`, `renderD128` and the
`Virtual-1` connector; the compositor, session file and input semantics all
live inside the image. `platform/image/verify-graphics.sh` runs as root inside
the cold-booted guest and requires sway 1.10.1 to allocate a GBM buffer and a
GL FBO, report an active 1280x800 output, and give `foot` a real Wayland window
that commits `wlr_surface` surfaces. It is a hard gate of `build.sh --build`.

The headless verification starts sway as root without a logind seat, so
`swaymsg -t get_inputs` is empty. The virtio input nodes are still owned by the
guest kernel; a real seatd/logind session takes them over. `GPU=none` on
`boot-dev.sh` keeps the serial console and ssh as the full fallback path.

## Reproducibility baseline

The kernel and rootfs are built inside an arm64 Linux guest, so their summaries
are not in-repo files. `platform/image/manifest.json` owns `build_baseline`,
which pins every field of `kernel-build.json` and `rootfs-build.json`: kernel
version, config/image digests, 773 modules, graphics drivers, the rootfs
tarball digest and byte count, the package-set digest and 388 installed package
versions. `platform/image/package-versions.txt` is the text lockfile produced
by `dpkg-query` and sorted with `LC_ALL=C`; it is a build input, not a binary
build artifact.

`build.sh --build` fetches both summaries and the guest lockfile, compares every
field against `build_baseline` and compares the two lockfiles byte-for-byte. Any
drift aborts the build with a `BASELINE DRIFT` message and a refresh hint. A
deliberately refreshed Debian mirror therefore cannot silently change what
ships in `knix.img`. The receipt carries `baseline.sha256`,
`baseline.kernel`, `baseline.rootfs`, `baseline.package_lock` and
`baseline.verified`.

`scripts/verify-repro.py` is the read-only CI gate: all 17 pinned inputs, the
baseline structure, lockfile ordering and uniqueness, executable build stages,
the receipt contract and the absence of binary artifacts under `platform/`,
`scripts/`, `docs/` and `.github/`. It needs no seed image and no VM. Running a
full QEMU/HVF build in CI remains out of scope: the seed is not in the
repository and HVF only exists on macOS.

## Run backends

`platform/contract.json` declares `native-apple` and `vm-virt`. Each owns a
schema-1 `backend.json`; `scripts/verify-backends.py` proves they are launch
paths: guest-owned capability, device, authentication, interface and input
semantics, one `knix.img`, one publish surface, read-only kernel-fragment and
boot-chain references, no host adapters and no build artifacts. Backend truths
are deliberately absent from `manifest.json.inputs`, so switching launch paths
never changes the 17 pinned inputs, `build_baseline` or the receipt.

`vm-virt` is the verified path. `native-apple` stays `contract-only` with
`verified: false` and no boot entry; bare-metal boot, the external ABI mapping,
image assembly and the install flow remain deferred.

## Identity ownership

`platform/identity/` owns the name, version, kernel baseline, package set,
session, brand bytes and publish surface. A run backend reads this identity and
never defines it. `repos.json` is an archive baseline and is never rewritten by
a build, a boot or a release.

## Rollback

Revert the `origin/main` commit that changed `platform/identity/` or
`platform/image/`, then rebuild. The image itself is derived, so it has no
independent version line.
