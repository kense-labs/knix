# Release

## Surface

`knix.img` and `origin/main` are the only publish surfaces. The image is a
build artifact and is not stored in this repository; `origin/main` carries the
identity, the build recipe and the verification gate that reproduce it.

## Current state (2026-11-12, local development paused)

The published image and receipt are still the P17 build. The P18 unified health
snapshot (`knix-status`, I30, 19 checks, 60 self-test cases) is present as
in-repo contract, assembly script, guest verifier and gates, and was
pre-flighted inside a qcow2 overlay of the published image. It has **not** been
built into a new image: rootfs was not rebuilt, `build.sh --build` was not run,
and no new receipt was written. `verify-release.py` therefore refuses the
current receipt with `diagnostics status drift`. That refusal is the correct
behaviour, not a broken gate. Resuming work requires a rootfs rebuild, a
three-stage baseline refresh, a full build with reboot proof, and a fresh
release acceptance run.

| 阶段 | 命令 | 产物 |
| --- | --- | --- |
| 输入校验 | `platform/image/build.sh --check` | 无 |
| 内核 | `platform/image/build-kernel.sh --apply`（guest） | `knix-Image` · `modules/` · `kernel-build.json` |
| 原生 rootfs | `platform/image/build-rootfs.sh --apply`（guest） | `knix-rootfs.tar.zst` · `rootfs-build.json` |
| 镜像构建 | `platform/image/build.sh --build`（host） | `$KNIX_DEV_DIR/knix.img` |
| 摘要与签名 | `platform/image/sign.sh` | `BUILD-RECEIPT.json` + `.sha256`（+ 可选 `.sig`） |
| 契约校验 | `python3 scripts/verify-knix.py` | 无 |
| 可复现基线 | `python3 scripts/verify-repro.py` | 无 |
| 发布验收 | `python3 scripts/verify-release.py` | 无（主机侧只读） |
| 发布验收自测 | `python3 scripts/verify-release.py --self-test` | 无（临时目录） |
| 刷新策略自测 | `python3 scripts/refresh-baseline.py --self-test` | 无（临时目录） |
| 锁定基线检查 | `python3 scripts/refresh-baseline.py --check` | 无 |
| 锁定基线刷新 | `python3 scripts/refresh-baseline.py --propose` → 评审 → `--apply --reason "..."` | `$KNIX_DEV_DIR/BASELINE-PROPOSAL.json`（仓外） |

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

`scripts/verify-release.py` is the single read-only acceptance gate. It runs on
any host with Python 3.9+, needs no QEMU, no guest and no third-party module,
and writes nothing.

```bash
# from the repository, with the image and receipt in $KNIX_DEV_DIR (../knix-m0)
python3 scripts/verify-release.py

# or point it at a downloaded release
python3 scripts/verify-release.py \
  --image /path/knix.img \
  --receipt /path/BUILD-RECEIPT.json \
  --checksum /path/BUILD-RECEIPT.json.sha256

# machine-readable result
python3 scripts/verify-release.py --json
```

It refuses the release unless all 19 checks in `manifest.json.release.checks`
pass: image bytes and sha256 equal the receipt; the `.sha256` openssl digest
covers the exact receipt file; every pinned input group and `seed_sha512`
equals `manifest.json.inputs`; `baseline.sha256` is the sha256 of the canonical
`build_baseline` object; the kernel and rootfs summaries carry every locked
field; the boot chain is UEFI self-boot with no seed ISO; `graphics` and its
`session`, `autostart`, `resilience`, `crash_loop`, `diagnostics`,
`diagnostics.persistence`, `diagnostics.operations`, `diagnostics.trends`,
`authentication` and `framebuffer` sections are all verified (at least 2
guest-reported diagnostics events, a reboot-surviving guest-owned archive, a
bounded rotation drill plus a cross-boot statistics proof over 2 boots, a
guest-recomputed per-boot trend report that also spans 2 boots and stays within
8 boots / 4096 bytes, guest-owned accounts with root and password logins
refused, and 9/9 framebuffer samples plus swaybar pixels); and
the publish surface is exactly `knix.img` plus
`origin/main`.

A successful run prints `RELEASE VERIFY OK`; any drift prints one
`RELEASE FAILED:` line per reason and exits 1. The gate is deliberately absent
from `inputs.*_sha256`: pinning it would change the receipt it judges. It adds
no product capability and is not a substitute for the build-time guest
verification. The receipt's `kernel`, `rootfs`, `inputs`, `boot`, `graphics` and
`baseline` sections therefore still have to match the committed manifest; a
mismatch means the image was assembled from drifted inputs or skipped a
verification stage.

`--self-test` builds a policy-consistent synthetic receipt in a temporary
directory and refuses 60 tampered variants (image swap, edited receipt, stale or
missing checksum, input and baseline drift, missing evidence, a host-owned or
non-reboot-proof crash archive, a host-owned / unverified / one-boot trend
report with a moved path or loosened bounds, a receipt that re-enables root or
password logins, extra publish surface) without QEMU or a build
artifact; CI runs it as the `Release verification gate` step. `scripts/verify-repro.py`, I25 and the CI
single-image contract block keep the policy itself from drifting.

For a signed release the detached signature is still checked out of band:

```bash
openssl dgst -sha256 -verify knix-release.pub \
  -signature BUILD-RECEIPT.json.sig BUILD-RECEIPT.json
```

The receipt's `sha256` only matches the image at the moment `build.sh --build`
finished. qcow2 is writable, so booting the published image afterwards changes
its bytes; extra checks use a `qemu-img create -b knix.img` overlay and
re-verify the backing file. `.sha256` covers the receipt, not the image.

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
live inside the image.

The session is a guest-owned systemd unit, not a root shell, and it is enabled
by the guest's own `multi-user.target`: a booted `knix.img` already has a desktop
before any verification script runs. `assemble-native.sh` adds `knix` to the
`video`, `input` and `render` groups, installs a guest-owned udev rule that tags
PCI virtio input nodes `seat` and `uaccess`, writes `knix-session.service` and
creates the `multi-user.target.wants` enablement symlink. The unit runs sway as
`knix` through the guest's own `seatd` with `WLR_BACKENDS=drm,libinput` -- naming
only `drm` excludes the libinput backend and leaves `swaymsg -t get_inputs` empty
regardless of seat setup. `ConditionPathExists=/dev/dri/card0` keeps it inactive
on the `GPU=none` path while the serial console and ssh stay available.

`build.sh --build` checks `enabled` and `active` on the cold-booted guest before
any verification script touches the unit. `platform/image/verify-graphics.sh`
then runs as root, restarts the unit only to obtain a clean log, and requires the
sway process to belong to `knix`, `get_inputs` to report the QEMU Virtio Keyboard
and Tablet, sway 1.10.1 to allocate a GBM buffer and a GL FBO, an active 1280x800
output, and `foot` to get a real Wayland window that commits `wlr_surface`
surfaces. It emits `AUTOSTART VERIFY OK`, leaves the session running and is a
hard gate of `build.sh --build`. The receipt records the device, the verification
recipe, `graphics.verified` and a `graphics.session` section with the user, seat
manager, unit, backends, input devices and the `autostart` result.

The unit also owns crash recovery: `Restart=on-failure`, `RestartSec=2`,
`StartLimitBurst=5` and `StartLimitIntervalSec=30` are written by
`assemble-native.sh` and declared by `session.json.resilience`. After the first
full verification, `verify-graphics.sh` sends SIGKILL with
`systemctl kill --signal=SIGKILL`, waits for a different `MainPID`, then requires
the restarted sway to be owned by `knix` and to again report the virtio input
devices, an active `Virtual-1` DRM output, freshly allocated GBM buffers and a
GL FBO, plus a `foot` window committing a `wlr_surface`. `ExecStartPre` truncates
the sway log on every start, so those render markers can only come from the new
process. `RESILIENCE VERIFY OK` is required before the receipt is written, and
`graphics.session.resilience.verified` records the result. The start limit stops
a broken compositor from looping forever; on the `GPU=none` path the DRM
condition stays unmet and the unit remains inactive.

## Crash-loop limit

One successful restart does not prove that a broken compositor is contained.
`StartLimitBurst=5` / `StartLimitIntervalSec=30` were declared in P9 but never
exercised. After `RESILIENCE VERIFY OK`, `verify-graphics.sh` keeps sending
`systemctl kill --signal=SIGKILL knix-session.service` until the guest's own
systemd refuses another restart. It then requires `ActiveState=failed`,
`MainPID=0`, zero surviving sway processes, the journal line
`Start request repeated too quickly.`, exactly one failed unit (the session unit
itself), `ssh.service` active and `/dev/ttyAMA0` present. That is an observable,
still-manageable degraded guest -- not a host-managed recovery.

`systemctl reset-failed` + `systemctl restart` afterwards is test cleanup, not
the recovery proof (that belongs to the P9 segment). Stale sway IPC sockets are
removed first, otherwise the checks would talk to a dead compositor's socket.
The recovered sway must again belong to `knix` and provide the three libinput
devices, the active `Virtual-1` output, freshly allocated GBM buffers and a GL
FBO, plus a `foot` window committing a `wlr_surface`. `LIMIT VERIFY OK` is a
hard gate of `build.sh --build`, and `graphics.session.crash_loop.verified` is
written into the receipt.

The 30-second sliding window means `NRestarts` is 4 or 5 and `Result` stays
`signal`; the gate therefore checks a range and the journal line rather than a
hard-coded counter or `Result=start-limit-hit`. On the `GPU=none` path the DRM
condition stays unmet, the unit remains inactive and no crash loop is created.

## Crash diagnostics

P11 proved that guest systemd stops a restart loop, but the failure was only
ever observed from outside by a verification script. P12 makes the guest report
its own failures. `knix-session.service` declares
`OnFailure=knix-session-failure.service`; systemd invokes that guest-owned
oneshot once per failed state, including the brief failed state before each
automatic restart. The guest script
`/usr/lib/knix/session-failure-report.sh` writes
`/run/knix/session-failure.txt` (latest snapshot as `key=value` lines) and
appends one line per event to `/run/knix/session-failure.history`. Every field
comes from the guest's own `systemctl show`, guest journal and local device
nodes -- no host input, no network, no private framework.

The reporter must always succeed: a failed diagnostics unit would become a
second failed unit and break the crash-loop contract's "exactly one failed
unit" degraded state. `verify-graphics.sh` checks, while the session is still
failed, that the report contains `active_state=failed`, `result=signal`,
`exec_main_status=9`, `failed_units=1`, `sway_processes=0`, `ssh=active` and
`serial=present`, that the history holds more than one reported crash and that
its last line is the terminal failed state. It emits `DIAGNOSTICS VERIFY OK`
and `DIAGNOSTICS EVENTS: N`; `build.sh --build` parses that event count and
refuses a receipt without it. The receipt records
`graphics.session.diagnostics` with the guest-reported event count.

`/run` is a tmpfs runtime snapshot; the persistent journal and the guest-owned
archive keep the durable record. On the `GPU=none` path the DRM condition is
unmet, no failure is produced and the serial console and ssh remain the full
fallback path.

## Persistent crash archive

P12's report lived only in `/run`: after a reboot the journal still showed that
systemd had invoked the reporter, but the report contents and the crash
timeline were gone. P14 makes the guest archive its own evidence.

`assemble-native.sh` writes
`/etc/systemd/journald.conf.d/knix-persistence.conf` with
`Storage=persistent`, creates `/var/lib/knix` on the root filesystem and keeps
`/var/log/journal`. The guest-owned `OnFailure=` script mirrors the runtime
snapshot to `/var/lib/knix/session-failure.txt` and appends one boot-stamped
line per event to `/var/lib/knix/session-failure.log`, carrying the boot id,
timestamp and every report field. It still always exits 0 so it never becomes a
second failed unit. Nothing is written, collected or replayed by the host.

After the cold-boot verification, the QMP framebuffer capture and a clean
power-off, `build.sh --build` relaunches the **same** `knix.img` with its EFI
variable store kept. On that second boot it requires:

- the archive and snapshot still exist and contain `active_state=failed` plus at
  least two events;
- `/run/knix/session-failure.*` is gone, proving the archive is not an
  in-memory leftover;
- the snapshot's `boot=` id differs from the current boot id;
- `journalctl --list-boots` still lists the previous boot;
- zero failed units, active ssh, an active knix-owned sway session and the
  serial console.

Only then does it emit `DIAGNOSTICS PERSISTENCE VERIFY OK` and parse
`PERSISTENCE EVENTS: N`. A second clean power-off follows, **then** the image
sha256 is computed and the receipt is written, so the published bytes still
correspond exactly to what ships. The receipt records
`graphics.session.diagnostics.persistence` (owner, manager, paths, event count,
`survives_reboot` and `verified`), and `verify-release.py` refuses any receipt
missing that proof. On `GPU=none` no failure is produced, so no archive entry
is written and the serial console plus ssh remain the full fallback path.

## Diagnostics operations

A durable archive is not yet an operable one: an unbounded log, no cumulative
count and no visible alert would still leave an operator blind. P15 keeps all
three duties inside the same guest-owned `OnFailure=` script.

- **Bounded rotation.** The active archive is capped at 8192 bytes with three
  retained generations (`.1`..`.3`), oldest dropped first. The production
  archive is never rotated by a verification step: `verify-graphics.sh` runs a
  160-iteration drill with `KNIX_DIAG_DRILL_DIR=/run/knix/rotation-drill`,
  which redirects both the runtime and archive directories into an isolated
  tmpfs. The drill requires three generations, at least three rotations, a main
  archive under the cap and statistics that exactly equal the event count of
  the whole archive family; it then re-checks that the production archive and
  statistics are byte-for-byte untouched.
- **Cross-boot statistics.** Every report recomputes
  `/var/lib/knix/session-failure.stats` from the whole archive family:
  `total_events`, `boots_seen`, `rotations`, `last_boot`, `last_event` and
  `terminal_active_state`. On the second boot, the host asks guest systemd to
  signal its own compositor; the guest `OnFailure=` unit writes one new event
  with the new boot id. The build requires `total_events` to grow, `boots_seen`
  to reach 2, both boot ids to remain in the archive, and guest systemd to
  restore a `knix`-owned session with zero failed units before the receipt is
  written.
- **Local alert.** The guest writes a persistent alert to
  `/var/lib/knix/session-failure.alert`, emits an `err`-priority journal
  message tagged `knix-diagnostics` through `systemd-cat`, and installs a
  login-visible notice that only fires when the alert's boot id matches the
  current boot, so a healthy reboot never repeats an old failure. There is no
  network channel, no host collector and no external service.

`DIAGNOSTICS OPS VERIFY OK`, `ROTATION VERIFY OK` and
`DIAGNOSTICS OPS PERSISTENCE VERIFY OK` are all build-time hard gates. The
receipt records `graphics.session.diagnostics.operations`; `verify-release.py`
refuses a release without the rotation drill, the cross-boot proof and the
guest ownership fields. `GPU=none` produces no failed state and therefore no
alert or archive entry.

## Login and authentication policy

The image ships its own accounts, credentials and login decisions. `session.json.authentication`
and `manifest.json.graphics.authentication` are the two machine truths.
`assemble-native.sh` writes `/etc/ssh/sshd_config.d/10-knix-auth.conf`
(`PermitRootLogin no`, `PasswordAuthentication no`,
`KbdInteractiveAuthentication no`, `PubkeyAuthentication yes`,
`PermitEmptyPasswords no`, `AuthenticationMethods publickey`),
`/etc/systemd/logind.conf.d/10-knix-login.conf` (`IdleAction=ignore`,
`IdleActionSec=30min`, `NAutoVTs=2`), locks `root` and keeps sudo in the
guest-owned `/etc/sudoers.d/knix`. No host supplies an account, a credential,
a login manager or a getty autologin override.

`platform/image/verify-login.sh` runs inside the guest before the graphics
gate. It asserts every `sshd -T` line, checks that shadow holds no usable
password, that `knix` is the only interactive account with an active logind
state, that no root session exists and `seat0` is graphical, that
`knix-session.service` is already enabled and active with sway owned by
`knix`, and that `IdleAction=ignore` is in effect. It then uses the guest's own
ssh client to make one real password login attempt and one real root login
attempt; both must be refused. `LOGIN POLICY OK` and `LOGIN VERIFY OK` are hard
gates, the receipt records `graphics.session.authentication`, and
`verify-release.py` refuses any receipt that re-enables root or passwords.
`GPU=none` keeps the serial console and key-based ssh as the full fallback.

The current seatd kiosk session is started by a system unit rather than a
display manager, so logind has no seat0 graphical login session. That is
deferred, not silently claimed: the policy proves a single guest-owned account
without a host-supplied login, not a multi-user display-manager session.

## Framebuffer pixel proof

Reporting a compositor process is not proof that a desktop was painted. After
the crash-loop recovery, `verify-graphics.sh` closes the verification terminals, picks a
24-bit colour from the guest's own `/dev/urandom` (each channel is lifted into
`0x40..0xff` so a black screen cannot pass) and asks the recovered sway to
paint it with `output * background #RRGGBB solid_color` on the standard
`virtio-gpu-pci` scanout. It prints `FRAMEBUFFER PATTERN: <hex>` into the guest
verification log.

`platform/image/verify-framebuffer.py` then runs on the build host. It reads
that colour, opens the QEMU QMP unix socket exposed by `boot-dev.sh`, sends
`qmp_capabilities` and `screendump` with `format=ppm`, and parses the resulting
P6 framebuffer. It requires 1280x800, maxval 255, a complete pixel payload, all
nine sample points to equal the guest-reported colour exactly, and at least 100
non-pattern pixels in the top bar (the guest swaybar). Only after
`FRAMEBUFFER VERIFY OK` does `build.sh --build` stop the guest and write the
receipt; the receipt's `graphics.framebuffer` section carries the pattern
digest, sample counts and swaybar pixel count. The host draws nothing, injects
nothing and configures nothing inside the guest -- it only reads a standard
QEMU scanout. `GPU=none` has no scanout and keeps the serial console and ssh as
the full fallback path.

CI runs `python3 platform/image/verify-framebuffer.py --self-test`, which
exercises 12 synthetic-PPM cases (control, wrong colour, black scanout, no
swaybar, wrong resolution, wrong maxval, truncated pixels, malformed pattern
log) without QEMU, a seed image or a VM. Framebuffer captures (`.ppm`) stay in
`$KNIX_DEV_DIR` and are refused as repository artifacts by `verify-repro.py`.

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

`scripts/verify-repro.py` is the read-only CI gate: all pinned inputs, the
baseline structure, lockfile ordering and uniqueness, executable build stages,
the receipt contract, the baseline-refresh policy and the absence of binary
artifacts under `platform/`, `scripts/`, `docs/` and `.github/`. It needs no
seed image and no VM. Running a full QEMU/HVF build in CI remains out of scope:
the seed is not in the repository and HVF only exists on macOS.

## Refreshing the locked baseline

When the Debian mirror or a build recipe legitimately changes, the lock is not
edited by hand. `scripts/refresh-baseline.py` owns the refresh:

```bash
python3 scripts/refresh-baseline.py --check       # read-only field-level diff
python3 scripts/refresh-baseline.py --propose    # writes BASELINE-PROPOSAL.json
# review old/new values, the four fingerprints and the reason, then:
python3 scripts/refresh-baseline.py --apply --reason "Debian mirror updated trixie"
python3 scripts/refresh-baseline.py --check
KNIX_DEV_DIR=... platform/image/build.sh --build   # rewrite the receipt
```

`--apply` refuses a missing or already-applied proposal, a reason shorter than
8 characters, stale fingerprints (kernel/rootfs summaries, the guest lockfile
or the manifest bytes), a field diff that no longer matches the current guest
summaries, and a guest lockfile that contradicts its own `rootfs-build.json`.
It only rewrites `build_baseline.rootfs`, the `*_sha256` input groups and, when
needed, the committed `package-versions.txt`; the kernel lock, artifact, boot,
graphics and publish-surface fields are never touched by this tool. A kernel
summary drift must go back through `build-kernel.sh --apply`, because a new
kernel changes the image itself.

`manifest.json.baseline_refresh` is the machine-readable policy. I20, the
`Baseline refresh policy` CI step (`--self-test`, 12 cases in a temporary
directory without guest artifacts) and the single-image contract block refuse
any drift in that policy. The proposal file lives outside the repository and is
not a publish surface.

## Run backends

`platform/contract.json` declares `native-apple` and `vm-virt`. Each owns a
schema-1 `backend.json`; `scripts/verify-backends.py` proves they are launch
paths: guest-owned capability, device, authentication, interface and input
semantics, one `knix.img`, one publish surface, read-only kernel-fragment and
boot-chain references, no host adapters and no build artifacts. Backend truths
are deliberately absent from `manifest.json.inputs`, so switching launch paths
never changes the pinned inputs, `build_baseline` or the receipt.

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
